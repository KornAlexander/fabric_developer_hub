/**
 * RichComposer — contenteditable-based prompt composer with inline
 * mention chips.
 *
 * Why not a <textarea>?
 *   A textarea can only hold plain text. We want accepted @-mentions to
 *   render as colored, non-editable pill tokens that read inline with
 *   the rest of the prose (the pattern Cursor, Copilot Chat, Notion,
 *   Slack, Linear all use). That requires inline elements inside an
 *   editable surface — contenteditable is the only DOM primitive that
 *   supports this natively without pulling in a full rich-text framework
 *   (Lexical, Slate, Tiptap).
 *
 * Token model:
 *   The composer's value is a list of tokens:
 *     { type: "text", text: string }
 *     { type: "mention", id, name, kind, payload? }
 *   Plain-text projection (used by the planner, draft storage, and
 *   recent-prompt display) is the concatenation of text tokens with
 *   mention tokens serialised as "@<Name>". This keeps every upstream
 *   consumer unchanged — they still see a plain string.
 *
 * Editing behaviour:
 *   • Mention chips are contenteditable="false" so they move as a single
 *     unit. Backspace next to a chip removes the whole chip.
 *   • The caret rect (used to anchor the mention popover) comes from
 *     window.getSelection(), no mirror-div needed.
 *   • Input handling: we listen to "input" events, re-parse our own DOM
 *     into a token array, and emit onChange. Caret position is
 *     preserved via a text-offset bookmark.
 *   • Placeholder is rendered via `:empty::before { content: attr(…) }`.
 */
import React, {
    forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect,
    useMemo, useRef,
} from "react";

// ─── types ─────────────────────────────────────────────────────────────

export type MentionKind =
    | "workspace"
    // Fabric item types — kept in sync with the portal's native icon set
    // so chips + picker rows use the same visual vocabulary as the
    // workspace explorer.
    | "lakehouse" | "warehouse" | "notebook" | "pipeline"
    | "sqldb" | "sqlendpoint" | "kqldatabase" | "eventhouse"
    | "kqlqueryset" | "kqlscript"
    | "semantic" | "dataflow" | "dataflowgen2" | "eventstream"
    | "mirrored" | "mlmodel" | "mlexperiment" | "environment"
    | "report" | "dashboard" | "paginated" | "sparkjob"
    | "rdlreport" | "mobilereport" | "rtdashboard"
    | "scorecard" | "metric" | "schemamodel"
    | "userfunction" | "functionset" | "variables"
    | "exploration" | "dataagent" | "opsagent" | "app"
    | "map"
    | "reflex" | "datafactory" | "copyjob" | "datamart"
    | "item"
    // Local attachments (files the user dragged/pasted in).
    | "pdf" | "image" | "file";

export interface MentionToken {
    type: "mention";
    /** Stable id (e.g. "ws:<guid>", "it:<guid>", "file:<name>"). */
    id: string;
    name: string;
    kind: MentionKind;
    /** Opaque payload echoed back on serialization. */
    payload?: unknown;
}
export interface TextToken { type: "text"; text: string; }
export type ComposerToken = TextToken | MentionToken;

export interface RichComposerValue {
    tokens: ComposerToken[];
}

/** Trigger state emitted while the user is typing "@query". */
export interface RichTrigger {
    query: string;
    /** Viewport-coords rect for anchoring the picker popover. */
    anchor: { top: number; left: number; bottom: number };
}

export interface RichComposerHandle {
    focus(): void;
    /** Access the root contenteditable element (used for outside-click
     *  detection and other DOM-level integrations). */
    getElement(): HTMLDivElement | null;
    /** Insert `mention` in place of the current @-trigger. */
    acceptMention(mention: Omit<MentionToken, "type">): void;
    /** Remove every mention chip whose id equals `id`. Used when the
     *  user detaches an attachment from the pill rail so the inline
     *  reference doesn't become a dangling ghost. */
    removeMentionsById(id: string): void;
}

export interface RichComposerProps {
    value: RichComposerValue;
    onChange: (next: RichComposerValue, plainText: string) => void;
    onTriggerChange: (t: RichTrigger | null) => void;
    placeholder?: string;
    className?: string;
    id?: string;
}

// ─── helpers ───────────────────────────────────────────────────────────

/** Plain-text projection used by upstream consumers. */
export function tokensToPlainText(tokens: ComposerToken[]): string {
    let out = "";
    for (const t of tokens) {
        out += t.type === "text" ? t.text : `@${t.name}`;
    }
    return out;
}

/** Parse a plain string into tokens (no mentions). Used to seed the
 *  composer from upstream state such as loaded drafts. */
export function plainTextToTokens(text: string): ComposerToken[] {
    return text ? [{ type: "text", text }] : [];
}

/** Merge adjacent text tokens and drop empty ones — keeps the DOM tidy
 *  and avoids invisible "" text nodes that break caret math. */
function normalize(tokens: ComposerToken[]): ComposerToken[] {
    const out: ComposerToken[] = [];
    for (const t of tokens) {
        if (t.type === "text") {
            if (!t.text) continue;
            const prev = out[out.length - 1];
            if (prev && prev.type === "text") {
                (prev as TextToken).text += t.text;
            } else {
                out.push({ ...t });
            }
        } else {
            out.push({ ...t });
        }
    }
    return out;
}

// ─── DOM ⇄ token model ─────────────────────────────────────────────────

/** Walk the composer's DOM and rebuild the token array. Mention chips
 *  are tagged with data-mention-id so we can read them back. Non-chip
 *  elements contribute their textContent. */
function readTokensFromDOM(root: HTMLElement): ComposerToken[] {
    const out: ComposerToken[] = [];
    function visit(node: Node) {
        if (node.nodeType === Node.TEXT_NODE) {
            out.push({ type: "text", text: (node as Text).data });
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        // Mention chip: opaque to editing.
        if (el.dataset.mentionId) {
            out.push({
                type: "mention",
                id: el.dataset.mentionId,
                name: el.dataset.mentionName || el.textContent || "",
                kind: (el.dataset.mentionKind as MentionKind) || "item",
                // Payload is stored as JSON on the element. Silent fallback
                // if something corrupted it.
                payload: (() => {
                    try { return JSON.parse(el.dataset.mentionPayload || "null"); }
                    catch { return null; }
                })(),
            });
            return;
        }
        // Line break (user pressed Enter).
        if (el.tagName === "BR") {
            out.push({ type: "text", text: "\n" });
            return;
        }
        // Block element — treat as implicit newline between its siblings.
        const isBlock = el.tagName === "DIV" || el.tagName === "P";
        if (isBlock && out.length > 0 && !(
            out[out.length - 1].type === "text" &&
            (out[out.length - 1] as TextToken).text.endsWith("\n")
        )) {
            out.push({ type: "text", text: "\n" });
        }
        for (const child of Array.from(el.childNodes)) visit(child);
    }
    for (const child of Array.from(root.childNodes)) visit(child);
    return normalize(out);
}

/** Build the DOM children for a token array. Kept as raw DOM nodes so we
 *  can imperatively splice them into the editable root without React's
 *  reconciler fighting us over the selection. */
function renderTokensToNodes(
    doc: Document,
    tokens: ComposerToken[],
): Node[] {
    const nodes: Node[] = [];
    for (const t of tokens) {
        if (t.type === "text") {
            // Split on newlines: each line becomes its own text node
            // separated by <br>. contenteditable behaves best with real
            // text nodes + <br>, not \n characters.
            const parts = t.text.split("\n");
            parts.forEach((p, i) => {
                if (p) nodes.push(doc.createTextNode(p));
                if (i < parts.length - 1) nodes.push(doc.createElement("br"));
            });
        } else {
            nodes.push(buildMentionChipPlaceholder(doc, t));
        }
    }
    return nodes;
}

/** DOM-only skeleton for a mention chip. The React subtree is portaled in
 *  after mount via `hydrateMentionChip` so we get Fluent icons without
 *  React touching the editable root's structure on every input. */
function buildMentionChipPlaceholder(doc: Document, m: MentionToken): HTMLElement {
    const span = doc.createElement("span");
    span.className = `rc-mention rc-mention--${m.kind}`;
    span.contentEditable = "false";
    span.dataset.mentionId = m.id;
    span.dataset.mentionName = m.name;
    span.dataset.mentionKind = m.kind;
    if (m.payload !== undefined) {
        try { span.dataset.mentionPayload = JSON.stringify(m.payload); }
        catch { /* ignore non-serialisable payloads */ }
    }
    // Accessible fallback text; React hydration replaces this with an
    // icon + name span pair.
    span.textContent = `@${m.name}`;
    // Aid text selection / contiguous copy by giving the chip a hair
    // of breathing room in the flow.
    return span;
}

/** Attach icon + name nodes inside the chip. Called after the DOM tree
 *  is committed so React's portal doesn't mutate the editable root. */
function hydrateMentionChip(
    span: HTMLElement,
    reactRenderIcon: (kind: MentionKind) => React.ReactNode,
    root: HTMLElement,
) {
    // No React portals for simplicity — use a static SVG-less fallback.
    // The color + name is enough to recognise the chip; the icon is a
    // nice-to-have and would require portaling which complicates caret
    // maths. We keep the DOM dead-simple: text only. (If we ever really
    // want icons we can upgrade, but Slack's mention chips are also text
    // only inside the input — only the popover has icons.)
    void reactRenderIcon; void root;
    const name = span.dataset.mentionName || "";
    span.textContent = "";
    const at = document.createElement("span");
    at.className = "rc-mention__at";
    at.textContent = "@";
    span.appendChild(at);
    span.appendChild(document.createTextNode(name));
}

// ─── caret bookkeeping ─────────────────────────────────────────────────

/** Compute the caret offset as a plain-text index within the root.
 *  Mention chips contribute `@<Name>.length` to the offset so the
 *  index matches `tokensToPlainText(...)`. */
function getCaretPlainOffset(root: HTMLElement): number | null {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    if (!root.contains(range.endContainer)) return null;
    let offset = 0;
    let done = false;
    function visit(node: Node) {
        if (done) return;
        if (node === range.endContainer) {
            if (node.nodeType === Node.TEXT_NODE) {
                offset += range.endOffset;
            } else {
                // Caret at element boundary; walk its children up to
                // endOffset.
                for (let i = 0; i < range.endOffset; i++) {
                    visitFully(node.childNodes[i]);
                }
            }
            done = true;
            return;
        }
        if (node.nodeType === Node.TEXT_NODE) {
            offset += (node as Text).data.length;
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        if (el.dataset.mentionId) {
            offset += 1 + (el.dataset.mentionName || "").length; // "@<Name>"
            return;
        }
        if (el.tagName === "BR") { offset += 1; return; }
        for (const child of Array.from(el.childNodes)) {
            visit(child);
            if (done) return;
        }
    }
    function visitFully(node: Node) {
        if (node.nodeType === Node.TEXT_NODE) { offset += (node as Text).data.length; return; }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const el = node as HTMLElement;
        if (el.dataset.mentionId) { offset += 1 + (el.dataset.mentionName || "").length; return; }
        if (el.tagName === "BR") { offset += 1; return; }
        for (const child of Array.from(el.childNodes)) visitFully(child);
    }
    visit(root);
    return done ? offset : null;
}

/** Place the caret at plain-text offset `target` within the root. */
function setCaretByPlainOffset(root: HTMLElement, target: number) {
    let remaining = target;
    let placed = false;
    const sel = window.getSelection();
    if (!sel) return;
    function tryPlace(node: Node): boolean {
        if (placed) return true;
        if (node.nodeType === Node.TEXT_NODE) {
            const len = (node as Text).data.length;
            if (remaining <= len) {
                const r = document.createRange();
                r.setStart(node, remaining);
                r.collapse(true);
                sel!.removeAllRanges();
                sel!.addRange(r);
                placed = true;
                return true;
            }
            remaining -= len;
            return false;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return false;
        const el = node as HTMLElement;
        if (el.dataset.mentionId) {
            const chipLen = 1 + (el.dataset.mentionName || "").length;
            if (remaining <= 0) {
                // Place caret before the chip.
                const r = document.createRange();
                r.setStartBefore(el);
                r.collapse(true);
                sel!.removeAllRanges();
                sel!.addRange(r);
                placed = true;
                return true;
            }
            if (remaining <= chipLen) {
                // Place caret after the chip — we treat the chip as
                // atomic; no caret position inside.
                const r = document.createRange();
                r.setStartAfter(el);
                r.collapse(true);
                sel!.removeAllRanges();
                sel!.addRange(r);
                placed = true;
                return true;
            }
            remaining -= chipLen;
            return false;
        }
        if (el.tagName === "BR") {
            if (remaining <= 0) {
                const r = document.createRange();
                r.setStartBefore(el);
                r.collapse(true);
                sel!.removeAllRanges();
                sel!.addRange(r);
                placed = true;
                return true;
            }
            remaining -= 1;
            return false;
        }
        for (const child of Array.from(el.childNodes)) if (tryPlace(child)) return true;
        return false;
    }
    tryPlace(root);
    if (!placed) {
        // Fallback: caret at end.
        const r = document.createRange();
        r.selectNodeContents(root);
        r.collapse(false);
        sel.removeAllRanges();
        sel.addRange(r);
    }
}

// ─── the component ─────────────────────────────────────────────────────

export const RichComposer = forwardRef<RichComposerHandle, RichComposerProps>(
function RichComposer(
    { value, onChange, onTriggerChange, placeholder, className, id },
    forwardedRef,
) {
    const rootRef = useRef<HTMLDivElement | null>(null);
    // Track the latest value WE produced so we can skip re-rendering the
    // DOM when the parent echoes our onChange back unchanged.
    const lastEmittedTokensRef = useRef<ComposerToken[]>(value.tokens);

    // Compare two token arrays by plain-text + mention-id signature.
    function tokensEqual(a: ComposerToken[], b: ComposerToken[]): boolean {
        if (a.length !== b.length) return false;
        for (let i = 0; i < a.length; i++) {
            const x = a[i]; const y = b[i];
            if (x.type !== y.type) return false;
            if (x.type === "text" && y.type === "text") {
                if (x.text !== y.text) return false;
            } else if (x.type === "mention" && y.type === "mention") {
                if (x.id !== y.id || x.name !== y.name || x.kind !== y.kind) return false;
            }
        }
        return true;
    }

    // Render tokens → DOM when the external value doesn't match what we
    // last emitted. This covers the "parent reset / loaded draft" case
    // without clobbering the DOM mid-typing.
    useLayoutEffect(() => {
        const root = rootRef.current;
        if (!root) return;
        if (tokensEqual(value.tokens, lastEmittedTokensRef.current)) return;
        // Preserve caret if the root is focused.
        const focused = document.activeElement === root;
        const caret = focused ? getCaretPlainOffset(root) : null;
        // Full replace — the token arrays differ, and partial reconciliation
        // isn't worth the complexity here.
        root.innerHTML = "";
        const nodes = renderTokensToNodes(document, value.tokens);
        for (const n of nodes) root.appendChild(n);
        // Hydrate chip visuals.
        root.querySelectorAll<HTMLElement>("[data-mention-id]").forEach(el => {
            hydrateMentionChip(el, () => null, root);
        });
        if (focused && caret != null) setCaretByPlainOffset(root, caret);
        lastEmittedTokensRef.current = value.tokens;
    }, [value.tokens]);

    // Trigger detection — runs on every input + selection change.
    const reportTrigger = useCallback(() => {
        const root = rootRef.current;
        if (!root) return;
        if (document.activeElement !== root) return;
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) {
            onTriggerChange(null);
            return;
        }
        const range = sel.getRangeAt(0);
        if (!root.contains(range.endContainer)) {
            onTriggerChange(null);
            return;
        }
        // Trigger detector operates on the plain text between the last
        // mention-chip / line-start and the caret. We walk backwards
        // through text nodes only — mention chips or <br> reset the
        // context (no "@" can straddle a chip).
        let textBeforeCaret = "";
        // Collect preceding text within the current text segment.
        if (range.endContainer.nodeType === Node.TEXT_NODE) {
            textBeforeCaret = (range.endContainer as Text).data.slice(0, range.endOffset);
            // Walk backwards through previous siblings until we hit a
            // chip, <br>, or the root.
            let cursor: Node | null = range.endContainer.previousSibling;
            while (cursor) {
                if (cursor.nodeType === Node.TEXT_NODE) {
                    textBeforeCaret = (cursor as Text).data + textBeforeCaret;
                    cursor = cursor.previousSibling;
                } else if (cursor.nodeType === Node.ELEMENT_NODE) {
                    const el = cursor as HTMLElement;
                    if (el.dataset.mentionId || el.tagName === "BR") break;
                    // Non-chip inline element — flatten its text.
                    textBeforeCaret = (el.textContent || "") + textBeforeCaret;
                    cursor = cursor.previousSibling;
                } else {
                    cursor = cursor.previousSibling;
                }
            }
        }
        // Find the trailing "@…" token.
        const MAX_QUERY = 60;
        const trigger = findTrigger(textBeforeCaret, MAX_QUERY);
        if (!trigger) {
            onTriggerChange(null);
            return;
        }
        // Anchor rect at caret.
        const rects = range.getClientRects();
        let rect: DOMRect | null = rects.length ? rects[0] : null;
        if (!rect || (rect.width === 0 && rect.height === 0)) {
            // Collapsed range sometimes has empty rects; fall back to the
            // parent element's rect.
            const parent = range.endContainer.nodeType === Node.ELEMENT_NODE
                ? (range.endContainer as Element)
                : range.endContainer.parentElement;
            rect = parent?.getBoundingClientRect() || null;
        }
        if (!rect) { onTriggerChange(null); return; }
        onTriggerChange({
            query: trigger.query,
            anchor: { top: rect.top, left: rect.left, bottom: rect.bottom },
        });
    }, [onTriggerChange]);

    // Input event: re-parse DOM, emit new value, re-check trigger.
    const onInput = useCallback(() => {
        const root = rootRef.current;
        if (!root) return;
        const tokens = readTokensFromDOM(root);
        const plain = tokensToPlainText(tokens);
        lastEmittedTokensRef.current = tokens;
        onChange({ tokens }, plain);
        reportTrigger();
    }, [onChange, reportTrigger]);

    // Keyboard: backspace next to a chip removes the chip cleanly.
    const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
        if (e.key !== "Backspace") return;
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return;
        const range = sel.getRangeAt(0);
        const c = range.endContainer;
        let prevNode: Node | null = null;
        if (c.nodeType === Node.TEXT_NODE && range.endOffset === 0) {
            prevNode = c.previousSibling;
        } else if (c.nodeType === Node.ELEMENT_NODE) {
            prevNode = (c as Element).childNodes[range.endOffset - 1] || null;
        }
        if (prevNode && prevNode.nodeType === Node.ELEMENT_NODE) {
            const el = prevNode as HTMLElement;
            if (el.dataset.mentionId) {
                e.preventDefault();
                el.remove();
                onInput();
            }
        }
    }, [onInput]);

    // Selection change → trigger check (arrow keys, mouse clicks).
    useEffect(() => {
        function onSel() { reportTrigger(); }
        document.addEventListener("selectionchange", onSel);
        return () => document.removeEventListener("selectionchange", onSel);
    }, [reportTrigger]);

    // Imperative API exposed to the parent.
    useImperativeHandle(forwardedRef, (): RichComposerHandle => ({
        focus: () => {
            const root = rootRef.current;
            if (!root) return;
            root.focus();
            // If nothing is selected, place the caret at the end.
            const sel = window.getSelection();
            if (sel && (!sel.rangeCount || !root.contains(sel.anchorNode))) {
                const r = document.createRange();
                r.selectNodeContents(root);
                r.collapse(false);
                sel.removeAllRanges();
                sel.addRange(r);
            }
        },
        getElement: () => rootRef.current,
        acceptMention: (m) => {
            const root = rootRef.current;
            if (!root) return;
            const sel = window.getSelection();
            if (!sel || sel.rangeCount === 0) return;
            const range = sel.getRangeAt(0);
            if (!root.contains(range.endContainer)) return;
            // Delete the in-progress "@query" text before the caret.
            // We walk backwards from the caret through text nodes only,
            // deleting characters until we've removed the "@" that started
            // the trigger.
            let remaining = findTriggerLength(root, range);
            if (remaining == null) return;
            if (remaining > 0) {
                deleteCharsBefore(range, remaining);
            }
            // Insert chip + a trailing non-breaking space so the caret
            // can land after the chip in the text flow.
            const chip = buildMentionChipPlaceholder(document, {
                type: "mention",
                id: m.id,
                name: m.name,
                kind: m.kind,
                payload: m.payload,
            });
            hydrateMentionChip(chip, () => null, root);
            const space = document.createTextNode("\u00A0");
            const newRange = sel.getRangeAt(0);
            newRange.insertNode(space);
            newRange.insertNode(chip);
            // Place caret after the space.
            const after = document.createRange();
            after.setStartAfter(space);
            after.collapse(true);
            sel.removeAllRanges();
            sel.addRange(after);
            // Emit.
            onInput();
        },
        removeMentionsById: (id) => {
            const root = rootRef.current;
            if (!root) return;
            const chips = root.querySelectorAll<HTMLElement>(
                `[data-mention-id="${CSS.escape(id)}"]`,
            );
            if (!chips.length) return;
            chips.forEach(c => c.remove());
            onInput();
        },
    }), [onInput]);

    const style = useMemo<React.CSSProperties>(() => ({
        // A couple of sane defaults — consumer styles override via className.
        outline: "none",
    }), []);

    return (
        <div
            id={id}
            ref={rootRef}
            className={className}
            contentEditable
            suppressContentEditableWarning
            role="textbox"
            aria-multiline="true"
            aria-placeholder={placeholder}
            data-placeholder={placeholder || ""}
            style={style}
            onInput={onInput}
            onKeyDown={onKeyDown}
        />
    );
});

// ─── helpers used by the component above ──────────────────────────────

/** Find the active "@query" token in `textBeforeCaret` — i.e. the longest
 *  suffix that starts with an "@" preceded by whitespace (or SOL). */
function findTrigger(textBeforeCaret: string, maxLen: number): { query: string } | null {
    // Walk backwards from the end.
    let i = textBeforeCaret.length - 1;
    let sawSpace = false;
    while (i >= 0 && textBeforeCaret.length - 1 - i < maxLen) {
        const ch = textBeforeCaret[i];
        if (ch === "\n") return null;
        if (ch === "@") {
            const prev = i === 0 ? "" : textBeforeCaret[i - 1];
            if (prev === "" || /\s/.test(prev)) {
                return { query: textBeforeCaret.slice(i + 1) };
            }
            return null;
        }
        if (ch === " ") {
            // Two consecutive spaces end the trigger — user has moved on.
            if (sawSpace) return null;
            sawSpace = true;
        } else {
            sawSpace = false;
        }
        i--;
    }
    return null;
}

/** Count characters to delete before the caret to remove the active
 *  "@query" token. Returns null if no trigger is active. */
function findTriggerLength(root: HTMLElement, range: Range): number | null {
    // Build the preceding-text string same way reportTrigger does, but
    // return the token length (1 + query.length) so the caller can
    // delete exactly that many characters before the caret.
    let textBeforeCaret = "";
    if (range.endContainer.nodeType === Node.TEXT_NODE) {
        textBeforeCaret = (range.endContainer as Text).data.slice(0, range.endOffset);
        let cursor: Node | null = range.endContainer.previousSibling;
        while (cursor) {
            if (cursor.nodeType === Node.TEXT_NODE) {
                textBeforeCaret = (cursor as Text).data + textBeforeCaret;
                cursor = cursor.previousSibling;
            } else if (cursor.nodeType === Node.ELEMENT_NODE) {
                const el = cursor as HTMLElement;
                if (el.dataset.mentionId || el.tagName === "BR") break;
                textBeforeCaret = (el.textContent || "") + textBeforeCaret;
                cursor = cursor.previousSibling;
            } else {
                cursor = cursor.previousSibling;
            }
        }
    }
    const trig = findTrigger(textBeforeCaret, 60);
    if (!trig) return null;
    return 1 + trig.query.length; // "@" + query
}

/** Delete `count` characters preceding the caret. Handles text-node
 *  boundaries. Does not cross mention chips. */
function deleteCharsBefore(range: Range, count: number) {
    let remaining = count;
    let node: Node | null = range.endContainer;
    let offset = range.endOffset;
    while (remaining > 0 && node) {
        if (node.nodeType === Node.TEXT_NODE) {
            const text = node as Text;
            const take = Math.min(offset, remaining);
            if (take > 0) {
                text.deleteData(offset - take, take);
                remaining -= take;
                offset -= take;
            }
            if (remaining === 0) {
                const sel = window.getSelection()!;
                const r = document.createRange();
                r.setStart(text, offset);
                r.collapse(true);
                sel.removeAllRanges();
                sel.addRange(r);
                return;
            }
            // Move to the previous sibling if this text node is exhausted.
            const prev = text.previousSibling;
            if (!prev) return;
            node = prev;
            if (node.nodeType === Node.TEXT_NODE) {
                offset = (node as Text).data.length;
            } else {
                return; // Hit a chip or block — stop.
            }
        } else {
            return;
        }
    }
}
