"""TMDL table read/patch helpers + PBI Fixer handler registry.

Each handler ports a Python fixer from ``pbi_fixer/src/_Fix_*.py``. They
all share the same shape:

    handler(parts: list[Part], scan_only: bool) -> tuple[list[Part], list[Finding], list[str]]

where ``Part`` is the Fabric REST shape ``{path, payload, payloadType}``
(payload is base64 UTF-8 TMDL text) and ``Finding`` is a small dataclass
the API turns into JSON for the Fixer page.

Since AgentHub runs in a browser iframe (no XMLA / TOM), every model
fixer mutates TMDL text via the same Fabric ``getDefinition`` /
``updateDefinition`` round-trip used by ``updateMeasureProperties``
(v0.27) and ``pbi_fixer_translations_apply`` (v0.40).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

Part = dict  # {path, payload, payloadType}


@dataclass
class Finding:
    object_path: str
    detail: str | None = None
    before: str | None = None
    after: str | None = None


@dataclass
class HandlerResult:
    parts: list[Part]
    findings: list[Finding] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Generic TMDL block helpers (port of frontend patchMeasureInTmdl)
# ---------------------------------------------------------------------------

# TMDL keyword properties (no colon) we deliberately skip when matching
# `key: value` lines.
_KEYWORD_PROPS = re.compile(
    r"^(annotation|lineageTag|changedProperty|extendedProperty|kind|"
    r"sourceLineageTag|queryGroup|relatedColumnDetails)\b"
)


def _decode(part: Part) -> str:
    return base64.b64decode(part["payload"]).decode("utf-8")


def _encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _is_simple_name(name: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def _quote_name(name: str) -> str:
    if _is_simple_name(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _detect_unit(lines: list[str], header_indent: int) -> str:
    """Sample any ``<indent>key: value`` line to figure out the per-level
    indent unit (typically a single tab). Falls back to a tab."""
    for ln in lines:
        m = re.match(r"^([\t ]+)\w+:\s", ln)
        if not m:
            continue
        if len(m.group(1)) > header_indent:
            return m.group(1)[header_indent:]
    return "\t"


def _format_value(key: str, val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    s = "" if val is None else str(val)
    if key in ("formatString", "description"):
        esc = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{esc}"'
    if not s:
        return ""
    if any(c in s for c in (":", "#", '"')):
        esc = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{esc}"'
    return s


def _block_bounds(lines: list[str], header_idx: int, header_indent: int) -> int:
    """Return the exclusive end index of the block starting at ``header_idx``."""
    for i in range(header_idx + 1, len(lines)):
        ln = lines[i]
        if not ln.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= header_indent:
            return i
    return len(lines)


def _patch_block_props(
    lines: list[str],
    header_idx: int,
    header_indent: int,
    end_idx: int,
    edits: dict[str, object],
) -> int:
    """Set/replace the given props in the block. Returns new end_idx."""
    unit = _detect_unit(lines, header_indent)
    prop_indent = " " * 0  # placeholder, replaced below
    prop_indent = (lines[header_idx][:header_indent] if header_indent else "") + unit

    handled: set[str] = set()
    for i in range(header_idx + 1, end_idx):
        t = lines[i].strip()
        if not t:
            continue
        m = re.match(r"^(\w+):", t)
        if not m:
            continue
        key = m.group(1)
        if key in edits and key not in handled:
            v = edits[key]
            if v is None:
                # Drop the line (sentinel = remove)
                lines.pop(i)
                end_idx -= 1
                handled.add(key)
                # Re-process this index since it now points to a different line
                # — but our loop bounds are stale; safer to break and re-run.
                return _patch_block_props(
                    lines, header_idx, header_indent, end_idx, {k: v2 for k, v2 in edits.items() if k != key}
                )
            lines[i] = f"{prop_indent}{key}: {_format_value(key, v)}"
            handled.add(key)

    # Insert any not-yet-handled edits before block end (skip None sentinels).
    inserts: list[str] = []
    for k, v in edits.items():
        if k in handled or v is None:
            continue
        inserts.append(f"{prop_indent}{k}: {_format_value(k, v)}")
    if inserts:
        insert_at = end_idx
        while insert_at > header_idx + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = inserts
        end_idx += len(inserts)
    return end_idx


def _read_block_props(lines: list[str], header_idx: int, end_idx: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(header_idx + 1, end_idx):
        t = lines[i].strip()
        if not t or _KEYWORD_PROPS.match(t):
            continue
        m = re.match(r"^(\w+):\s*(.*)$", t)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        # Strip surrounding quotes if present.
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[key] = raw
    return out


# ---------------------------------------------------------------------------
# Block iterators per kind
# ---------------------------------------------------------------------------

_HEADER_RE = {
    "column": re.compile(r"^([\t ]*)column\s+(['\"]?)(.+?)\2(?:\s|$)"),
    "measure": re.compile(r"^([\t ]*)measure\s+(['\"]?)(.+?)\2(?:\s|=|$)"),
    "table": re.compile(r"^([\t ]*)table\s+(['\"]?)(.+?)\2(?:\s|$)"),
    "relationship": re.compile(r"^([\t ]*)relationship\s+(\S+)"),
}


def _iter_blocks(text: str, kind: str):
    """Yield ``(header_idx, header_indent, end_idx, name, lines_ref)``.

    ``lines_ref`` is the shared list — mutating + re-yielding is the
    caller's job. Caller MUST iterate from the END to safely splice.
    """
    lines = text.split("\n")
    pat = _HEADER_RE[kind]
    blocks = []
    for i, ln in enumerate(lines):
        m = pat.match(ln)
        if not m:
            continue
        # `column` matches inside `tableColumns` / `relatedColumnDetails`?
        # Filter: must be a real header line (no preceding non-whitespace).
        indent_str = m.group(1)
        if kind != "measure" and "(" in ln:
            # `relatedColumnDetails(column ...)` etc — skip
            continue
        name = m.group(3) if kind != "relationship" else m.group(2)
        # Unquote
        if len(name) >= 2 and name[0] == "'" and name[-1] == "'":
            name = name[1:-1].replace("''", "'")
        end = _block_bounds(lines, i, len(indent_str))
        blocks.append((i, len(indent_str), end, name))
    return lines, blocks


def _iter_columns(text: str):
    return _iter_blocks(text, "column")


def _iter_measures(text: str):
    return _iter_blocks(text, "measure")


def _iter_relationships(text: str):
    return _iter_blocks(text, "relationship")


# ---------------------------------------------------------------------------
# Per-fixer handlers
# ---------------------------------------------------------------------------

_TABLE_PART = re.compile(r"^definition/tables/(.+)\.tmdl$")
_REL_PART = "definition/relationships.tmdl"


def _table_parts(parts: list[Part]) -> list[Part]:
    return [p for p in parts if _TABLE_PART.match(p.get("path", ""))]


def _table_name(part: Part) -> str:
    m = _TABLE_PART.match(part.get("path", ""))
    if not m:
        return ""
    name = m.group(1)
    try:
        from urllib.parse import unquote
        return unquote(name)
    except Exception:
        return name


def _apply_per_table(
    parts: list[Part],
    scan_only: bool,
    transform: Callable[[str, str], tuple[str, list[Finding], list[str]]],
) -> HandlerResult:
    """Run a per-table TMDL transform across every table part.

    ``transform(table_name, text) -> (new_text, findings, log)``.
    """
    new_parts = [{**p} for p in parts]
    all_findings: list[Finding] = []
    all_log: list[str] = []
    for p in new_parts:
        if not _TABLE_PART.match(p.get("path", "")):
            continue
        try:
            text = _decode(p)
        except Exception:
            continue
        tname = _table_name(p)
        new_text, findings, log = transform(tname, text)
        all_findings.extend(findings)
        all_log.extend(log)
        if not scan_only and new_text != text:
            p["payload"] = _encode(new_text)
            p["payloadType"] = p.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=all_findings, log=all_log)


# ---- numeric-type detection ------------------------------------------------

_NUMERIC_TYPES = {"int64", "double", "decimal", "currency", "integer"}


def _column_data_type(props: dict[str, str]) -> str:
    return (props.get("dataType") or "").lower()


def _is_numeric_col(props: dict[str, str]) -> bool:
    return _column_data_type(props) in _NUMERIC_TYPES


# =============== HANDLERS ===================================================


def fix_floating_point_datatype(parts: list[Part], scan_only: bool) -> HandlerResult:
    def tx(tname: str, text: str):
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        # Walk in reverse so splices don't shift earlier indices.
        for hidx, hindent, end, cname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            if _column_data_type(props) == "double":
                findings.append(Finding(
                    object_path=f"'{tname}'[{cname}]",
                    detail="Double → Decimal",
                    before="dataType: double",
                    after="dataType: decimal",
                ))
                _patch_block_props(lines, hidx, hindent, end, {"dataType": "decimal"})
        return "\n".join(lines), findings, []
    return _apply_per_table(parts, scan_only, tx)


def fix_do_not_summarize(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Set summarizeBy: none on numeric columns where it's currently set."""
    def tx(tname: str, text: str):
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        for hidx, hindent, end, cname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            sb = (props.get("summarizeBy") or "").lower()
            if _is_numeric_col(props) and sb and sb != "none":
                findings.append(Finding(
                    object_path=f"'{tname}'[{cname}]",
                    detail=f"summarizeBy: {sb} → none",
                    before=f"summarizeBy: {sb}",
                    after="summarizeBy: none",
                ))
                _patch_block_props(lines, hidx, hindent, end, {"summarizeBy": "none"})
        return "\n".join(lines), findings, []
    return _apply_per_table(parts, scan_only, tx)


def fix_discourage_implicit_measures(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Same as fix_do_not_summarize — numeric columns get summarizeBy: none."""
    return fix_do_not_summarize(parts, scan_only)


def fix_is_available_in_mdx_false(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Hidden columns get isAvailableInMdx: false (perf optimisation)."""
    def tx(tname: str, text: str):
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        for hidx, hindent, end, cname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            hidden = (props.get("isHidden") or "").lower() == "true"
            mdx = (props.get("isAvailableInMdx") or "").lower()
            if hidden and mdx != "false":
                findings.append(Finding(
                    object_path=f"'{tname}'[{cname}]",
                    detail="hidden + isAvailableInMdx ≠ false → false",
                    before=f"isAvailableInMdx: {mdx or '(default true)'}",
                    after="isAvailableInMdx: false",
                ))
                _patch_block_props(lines, hidx, hindent, end, {"isAvailableInMdx": False})
        return "\n".join(lines), findings, []
    return _apply_per_table(parts, scan_only, tx)


def fix_measure_format(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Measures with no formatString get '#,0' as a sane default."""
    def tx(tname: str, text: str):
        lines, blocks = _iter_measures(text)
        findings: list[Finding] = []
        for hidx, hindent, end, mname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            if "formatString" in props:
                continue
            findings.append(Finding(
                object_path=f"'{tname}'[{mname}]",
                detail="no formatString → '#,0'",
                before="formatString: (none)",
                after='formatString: "#,0"',
            ))
            _patch_block_props(lines, hidx, hindent, end, {"formatString": "#,0"})
        return "\n".join(lines), findings, []
    return _apply_per_table(parts, scan_only, tx)


def fix_percentage_format(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Measures whose name contains ``%`` get a percentage format."""
    PCT = "#,0.0%;-#,0.0%;#,0.0%"

    def tx(tname: str, text: str):
        lines, blocks = _iter_measures(text)
        findings: list[Finding] = []
        for hidx, hindent, end, mname in reversed(blocks):
            if "%" not in mname:
                continue
            props = _read_block_props(lines, hidx, end)
            current = props.get("formatString")
            if current and "%" in current:
                continue
            findings.append(Finding(
                object_path=f"'{tname}'[{mname}]",
                detail=f"% in name + format='{current or '(none)'}' → {PCT!r}",
                before=f"formatString: {current!r}" if current else "formatString: (none)",
                after=f'formatString: "{PCT}"',
            ))
            _patch_block_props(lines, hidx, hindent, end, {"formatString": PCT})
        return "\n".join(lines), findings, []
    return _apply_per_table(parts, scan_only, tx)


def fix_whole_number_format(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Int64 columns with no formatString get '#,0'."""
    def tx(tname: str, text: str):
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        for hidx, hindent, end, cname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            if _column_data_type(props) not in ("int64", "integer"):
                continue
            if "formatString" in props:
                continue
            findings.append(Finding(
                object_path=f"'{tname}'[{cname}]",
                detail="Int64 + no formatString → '#,0'",
                before="formatString: (none)",
                after='formatString: "#,0"',
            ))
            _patch_block_props(lines, hidx, hindent, end, {"formatString": "#,0"})
        return "\n".join(lines), findings, []
    return _apply_per_table(parts, scan_only, tx)


def fix_hide_foreign_keys(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Hide every column that is the ``from`` side of a relationship.

    Relationships live in ``definition/relationships.tmdl`` (and sometimes
    inline within table TMDL). We scan the relationships file for
    ``fromColumn: '<Table>'.<Column>`` and add ``isHidden: true`` to that
    column block in its table part.
    """
    # 1. Collect (table, column) pairs from relationships.tmdl.
    fk_pairs: set[tuple[str, str]] = set()
    rel_part = next(
        (p for p in parts if p.get("path") == _REL_PART),
        None,
    )
    if rel_part:
        try:
            rel_text = _decode(rel_part)
        except Exception:
            rel_text = ""
        # Match `fromColumn: '<table>'.'<col>'` or unquoted variants.
        for m in re.finditer(
            r"fromColumn:\s*('?)(.+?)\1\.('?)(.+?)\3(?:\s|$)",
            rel_text,
            flags=re.MULTILINE,
        ):
            fk_pairs.add((m.group(2), m.group(4)))

    if not fk_pairs:
        # Some models keep relationships inline in model.tmdl.
        model_part = next(
            (p for p in parts if p.get("path") == "definition/model.tmdl"),
            None,
        )
        if model_part:
            try:
                mtext = _decode(model_part)
            except Exception:
                mtext = ""
            for m in re.finditer(
                r"fromColumn:\s*('?)(.+?)\1\.('?)(.+?)\3(?:\s|$)",
                mtext,
                flags=re.MULTILINE,
            ):
                fk_pairs.add((m.group(2), m.group(4)))

    def tx(tname: str, text: str):
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        for hidx, hindent, end, cname in reversed(blocks):
            if (tname, cname) not in fk_pairs:
                continue
            props = _read_block_props(lines, hidx, end)
            if (props.get("isHidden") or "").lower() == "true":
                continue
            findings.append(Finding(
                object_path=f"'{tname}'[{cname}]",
                detail="foreign key → isHidden: true",
                before="isHidden: false (default)",
                after="isHidden: true",
            ))
            _patch_block_props(lines, hidx, hindent, end, {"isHidden": True})
        return "\n".join(lines), findings, []

    res = _apply_per_table(parts, scan_only, tx)
    if not fk_pairs:
        res.log.append("No relationships found in TMDL — nothing to hide.")
    return res


# =============== Report-side (PBIR JSON) handlers ===========================

import json as _json


def _pbir_visual_parts(parts: list[Part]):
    for p in parts:
        if re.match(r"^definition/pages/[^/]+/visuals/[^/]+/visual\.json$", p.get("path", "")):
            yield p


def _pbir_page_parts(parts: list[Part]):
    for p in parts:
        if re.match(r"^definition/pages/[^/]+/page\.json$", p.get("path", "")):
            yield p


def fix_hide_visual_filters(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Set ``isHiddenInViewMode: true`` on every visual-level filter."""
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    for p in new_parts:
        if not re.match(r"^definition/pages/[^/]+/visuals/[^/]+/visual\.json$", p.get("path", "")):
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        cfg = doc.get("filterConfig") or {}
        filters = cfg.get("filters") or []
        if not filters:
            continue
        changed = False
        path_match = re.match(r"definition/pages/([^/]+)/visuals/([^/]+)/", p["path"])
        page = path_match.group(1) if path_match else "?"
        vis = path_match.group(2) if path_match else "?"
        for f in filters:
            if not f.get("isHiddenInViewMode"):
                f["isHiddenInViewMode"] = True
                changed = True
                findings.append(Finding(
                    object_path=f"{page} › {vis}",
                    detail=f"filter '{f.get('name','?')}' → hidden",
                    before="isHiddenInViewMode: false/missing",
                    after="isHiddenInViewMode: true",
                ))
        if changed and not scan_only:
            doc["filterConfig"] = cfg
            p["payload"] = _encode(_json.dumps(doc, indent=2))
            p["payloadType"] = p.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=findings, log=[])


def fix_disable_show_items_no_data(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Drop ``showAll`` flags from visual queryState projections."""
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    for p in new_parts:
        if not re.match(r"^definition/pages/[^/]+/visuals/[^/]+/visual\.json$", p.get("path", "")):
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        qs = (doc.get("visual") or {}).get("query", {}).get("queryState") or {}
        path_match = re.match(r"definition/pages/([^/]+)/visuals/([^/]+)/", p["path"])
        page = path_match.group(1) if path_match else "?"
        vis = path_match.group(2) if path_match else "?"
        changed = False
        for role, role_def in qs.items():
            for proj in role_def.get("projections", []) or []:
                if proj.get("showAll"):
                    proj.pop("showAll", None)
                    changed = True
                    findings.append(Finding(
                        object_path=f"{page} › {vis} › {role}",
                        detail="showAll: true → removed",
                    ))
        if changed and not scan_only:
            p["payload"] = _encode(_json.dumps(doc, indent=2))
            p["payloadType"] = p.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=findings, log=[])


def fix_remove_unused_custom_visuals(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Remove entries from report.json ``publicCustomVisuals`` whose GUIDs
    aren't referenced by any visual."""
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    report_part = next(
        (p for p in new_parts if p.get("path", "").endswith("definition/report.json")),
        None,
    )
    if not report_part:
        return HandlerResult(parts=new_parts, findings=findings, log=["report.json not found."])
    try:
        report_doc = _json.loads(_decode(report_part))
    except Exception:
        return HandlerResult(parts=new_parts, findings=findings, log=["report.json parse failed."])
    declared = list(report_doc.get("publicCustomVisuals") or [])
    if not declared:
        return HandlerResult(parts=new_parts, findings=findings, log=["No custom visuals declared."])
    used: set[str] = set()
    for p in new_parts:
        if not re.match(r"^definition/pages/[^/]+/visuals/[^/]+/visual\.json$", p.get("path", "")):
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        vt = (doc.get("visual") or {}).get("visualType")
        if vt:
            used.add(vt)
    unused = [g for g in declared if g not in used]
    for g in unused:
        findings.append(Finding(object_path=f"publicCustomVisuals[{g}]", detail="unused → removed"))
    if unused and not scan_only:
        report_doc["publicCustomVisuals"] = [g for g in declared if g in used]
        report_part["payload"] = _encode(_json.dumps(report_doc, indent=2))
        report_part["payloadType"] = report_part.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=findings, log=[])


def fix_pie_chart(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Replace pie / donut / funnel visualTypes with ``barChart``."""
    pie_types = {"pieChart", "donutChart", "funnel"}
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    for p in new_parts:
        if not re.match(r"^definition/pages/[^/]+/visuals/[^/]+/visual\.json$", p.get("path", "")):
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        vis = doc.get("visual") or {}
        vt = vis.get("visualType")
        if vt not in pie_types:
            continue
        path_match = re.match(r"definition/pages/([^/]+)/visuals/([^/]+)/", p["path"])
        page = path_match.group(1) if path_match else "?"
        vname = path_match.group(2) if path_match else "?"
        findings.append(Finding(
            object_path=f"{page} › {vname}",
            detail=f"{vt} → barChart",
            before=f'"visualType": "{vt}"',
            after='"visualType": "barChart"',
        ))
        if not scan_only:
            vis["visualType"] = "barChart"
            doc["visual"] = vis
            p["payload"] = _encode(_json.dumps(doc, indent=2))
            p["payloadType"] = p.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=findings, log=[])


def fix_page_size(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Set every page to 1280×720."""
    TARGET_W, TARGET_H = 1280, 720
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    for p in new_parts:
        if not re.match(r"^definition/pages/[^/]+/page\.json$", p.get("path", "")):
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        w = doc.get("width") or 0
        h = doc.get("height") or 0
        if w <= 0 or h <= 0 or (w == TARGET_W and h == TARGET_H):
            continue
        path_match = re.match(r"definition/pages/([^/]+)/page\.json$", p["path"])
        page = path_match.group(1) if path_match else "?"
        findings.append(Finding(
            object_path=page,
            detail=f"{w}×{h} → {TARGET_W}×{TARGET_H}",
            before=f"width: {w}, height: {h}",
            after=f"width: {TARGET_W}, height: {TARGET_H}",
        ))
        if not scan_only:
            doc["width"] = TARGET_W
            doc["height"] = TARGET_H
            p["payload"] = _encode(_json.dumps(doc, indent=2))
            p["payloadType"] = p.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=findings, log=[])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Each handler's scope tells the API which definition to fetch
# ("sm" → semantic model, "report" → report).
FIXER_HANDLERS: dict[str, tuple[str, Callable[[list[Part], bool], HandlerResult]]] = {
    # Semantic-model fixers (TMDL)
    "Fix_FloatingPointDataType": ("sm", fix_floating_point_datatype),
    "Fix_DoNotSummarize": ("sm", fix_do_not_summarize),
    "Fix_DiscourageImplicitMeasures": ("sm", fix_discourage_implicit_measures),
    "Fix_IsAvailableInMdxFalse": ("sm", fix_is_available_in_mdx_false),
    "Fix_MeasureFormat": ("sm", fix_measure_format),
    "Fix_PercentageFormat": ("sm", fix_percentage_format),
    "Fix_WholeNumberFormat": ("sm", fix_whole_number_format),
    "Fix_HideForeignKeys": ("sm", fix_hide_foreign_keys),
    # Report fixers (PBIR JSON)
    "Fix_PieChart": ("report", fix_pie_chart),
    "Fix_PageSize": ("report", fix_page_size),
    "Fix_HideVisualFilters": ("report", fix_hide_visual_filters),
    "Fix_DisableShowItemsNoData": ("report", fix_disable_show_items_no_data),
    "Fix_RemoveUnusedCustomVisuals": ("report", fix_remove_unused_custom_visuals),
}
