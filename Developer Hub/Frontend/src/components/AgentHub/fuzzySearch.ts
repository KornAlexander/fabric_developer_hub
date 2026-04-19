/**
 * Lightweight fuzzy search utilities.
 *
 * Zero dependencies on purpose — the search corpus is small (hundreds of
 * items at most) so we don't need fuse.js or MiniSearch. The algorithm here
 * mirrors what users expect from VS Code / GitHub / Linear style pickers:
 *
 *   1. Query is tokenized on whitespace; ALL tokens must match (AND).
 *   2. Match is case-insensitive and accent-insensitive (NFD strip).
 *   3. For each token we try, in descending score order:
 *         a. exact substring                 → +1000
 *         b. word-boundary substring         → +700
 *         c. prefix of any word              → +500
 *         d. subsequence (letters in order)  → +100 − gap penalty
 *      Short matches beat long ones, compact subsequences beat spread ones.
 *   4. A match returns a numeric score; higher is better. 0 means "no match".
 *
 * Usage:
 *     const rows = fuzzyFilter(query, items, (it) => [it.name, it.description]);
 *     // rows is sorted best-match first and unmatched items are dropped.
 */

function normalize(s: string): string {
    return s
        .normalize("NFD")
        // eslint-disable-next-line no-misleading-character-class
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase();
}

/**
 * Score a single `token` against a haystack. Returns 0 when it doesn't match.
 * Larger is better. Only a token-level helper — use `fuzzyScore` for a full
 * query with multiple whitespace-separated tokens.
 */
function scoreToken(token: string, haystack: string): number {
    if (!token) return 0;
    if (!haystack) return 0;
    const t = normalize(token);
    const h = normalize(haystack);

    // a. exact substring
    const idx = h.indexOf(t);
    if (idx === 0) return 1000 + wordStartBonus(h, 0) - lengthPenalty(h);
    if (idx > 0) {
        // b. word-boundary substring (preceded by non-alphanum)
        const prev = h.charCodeAt(idx - 1);
        const atWordBoundary = !(prev >= 97 && prev <= 122) && !(prev >= 48 && prev <= 57);
        const base = atWordBoundary ? 700 : 400;
        return base - Math.min(idx, 200) - lengthPenalty(h);
    }

    // c. prefix-of-any-word: first letter hits a word start
    // d. subsequence: letters of `t` appear in order in `h`.
    let hi = 0;
    let lastHit = -1;
    let gaps = 0;
    let startedAtWordBoundary = false;
    for (let ti = 0; ti < t.length; ti++) {
        const c = t.charCodeAt(ti);
        while (hi < h.length && h.charCodeAt(hi) !== c) hi++;
        if (hi >= h.length) return 0;
        if (ti === 0) {
            startedAtWordBoundary = hi === 0 || !isAlphaNum(h.charCodeAt(hi - 1));
        } else if (lastHit >= 0) {
            gaps += hi - lastHit - 1;
        }
        lastHit = hi;
        hi++;
    }
    const prefixBonus = startedAtWordBoundary ? 200 : 0;
    const gapPenalty = Math.min(gaps * 2, 180);
    return Math.max(1, 100 + prefixBonus - gapPenalty - lengthPenalty(h));
}

function isAlphaNum(code: number): boolean {
    return (code >= 97 && code <= 122) || (code >= 48 && code <= 57);
}

function wordStartBonus(h: string, idx: number): number {
    if (idx === 0) return 50;
    return !isAlphaNum(h.charCodeAt(idx - 1)) ? 30 : 0;
}

/** Mild preference for shorter fields (so "Atlas" beats "Atlas - Analyst"). */
function lengthPenalty(h: string): number {
    return Math.min(h.length, 120) * 0.1;
}

/**
 * Score a multi-token `query` against any of the given `fields`. Returns 0
 * if ANY token is missing from every field (strict AND semantics), else the
 * sum of per-token best-field scores.
 */
export function fuzzyScore(query: string, fields: readonly string[]): number {
    const q = query.trim();
    if (!q) return 0;
    const tokens = q.split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return 0;
    let total = 0;
    for (const tok of tokens) {
        let best = 0;
        for (const f of fields) {
            if (!f) continue;
            const s = scoreToken(tok, f);
            if (s > best) best = s;
        }
        if (best === 0) return 0;
        total += best;
    }
    return total;
}

/**
 * Convenience filter: returns items whose score > 0, sorted best-first.
 * `extract` returns the list of text fields to consider for each item.
 */
export function fuzzyFilter<T>(
    query: string,
    items: readonly T[],
    extract: (item: T) => readonly (string | null | undefined)[],
): T[] {
    if (!query.trim()) return items.slice();
    const scored: Array<{ item: T; score: number }> = [];
    for (const item of items) {
        const fields = extract(item).filter(Boolean) as string[];
        const score = fuzzyScore(query, fields);
        if (score > 0) scored.push({ item, score });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.map(x => x.item);
}

/** True when any of the fields matches the query under fuzzy semantics. */
export function fuzzyMatches(query: string, ...fields: (string | null | undefined)[]): boolean {
    if (!query.trim()) return true;
    return fuzzyScore(query, fields.filter(Boolean) as string[]) > 0;
}
