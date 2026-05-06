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


# ---------------------------------------------------------------------------
# Fix_AvoidAdding0 (v0.47)
# ---------------------------------------------------------------------------
# Strip a leading "0+" / "0 +" / "0 + " prefix from measure DAX expressions.
# Original Python: pbi_fixer/src/_Fix_AvoidAdding0.py.
#
# Measure expressions in TMDL come in two shapes:
#   1) inline:   measure 'X' = 0 + COUNTROWS(Foo)
#   2) block:    measure 'X' =
#                    0 + COUNTROWS(Foo)
#                    formatString: #,0
#
# For the block form the expression body is the run of indented lines
# that come after the header up to the first property line (matching
# ``<indent><word>:`` like ``formatString:``) or the next blank line.
# The original `0 +` is almost always on a single line — we only need
# to rewrite the first non-empty line of the body.

_MEASURE_HEADER_LINE = re.compile(
    r"^([\t ]*)measure\s+(['\"]?)(.+?)\2(\s*=\s*)(.*)$"
)


def _strip_leading_zero_plus(expr: str) -> tuple[str, bool]:
    """If ``expr`` starts with optional whitespace then ``0`` + ``+`` (any
    spacing), strip that prefix. Returns ``(new_expr, changed)``."""
    m = re.match(r"^\s*0\s*\+\s*", expr)
    if not m:
        return expr, False
    return expr[m.end():], True


def fix_avoid_adding_zero(parts: list[Part], scan_only: bool) -> HandlerResult:
    def tx(tname: str, text: str):
        lines = text.split("\n")
        findings: list[Finding] = []
        i = 0
        # Walk forward — we only ever rewrite a single line per measure
        # so indices stay stable.
        while i < len(lines):
            m = _MEASURE_HEADER_LINE.match(lines[i])
            if not m:
                i += 1
                continue
            mname = m.group(3)
            if len(mname) >= 2 and mname[0] == "'" and mname[-1] == "'":
                mname = mname[1:-1].replace("''", "'")
            inline_expr = m.group(5)
            # Inline expression on the header line itself.
            if inline_expr.strip():
                new_expr, changed = _strip_leading_zero_plus(inline_expr)
                if changed:
                    findings.append(Finding(
                        object_path=f"'{tname}'[{mname}]",
                        detail="strip leading '0+' from expression",
                        before=inline_expr.strip(),
                        after=new_expr.strip(),
                    ))
                    lines[i] = (
                        m.group(1) + "measure " + m.group(2) + m.group(3) + m.group(2)
                        + m.group(4) + new_expr
                    )
                i += 1
                continue
            # Block form: scan for the first non-empty body line that
            # is NOT a property (``key:``).
            j = i + 1
            while j < len(lines):
                body = lines[j]
                if not body.strip():
                    j += 1
                    continue
                # Property line ends the expression body.
                if re.match(r"^[\t ]+\w+:\s", body):
                    break
                # Annotation / sub-block keyword lines also end it.
                if _KEYWORD_PROPS.match(body.strip()):
                    break
                # First real expression line — try to strip the prefix.
                indent = body[: len(body) - len(body.lstrip())]
                stripped, changed = _strip_leading_zero_plus(body[len(indent):])
                if changed:
                    findings.append(Finding(
                        object_path=f"'{tname}'[{mname}]",
                        detail="strip leading '0+' from expression",
                        before=body.strip(),
                        after=stripped.strip(),
                    ))
                    lines[j] = indent + stripped
                break
            i = j + 1 if j > i else i + 1
        return "\n".join(lines), findings, []
    return _apply_per_table(parts, scan_only, tx)


# ---------------------------------------------------------------------------
# Add_LastRefreshTable (v0.48)
# ---------------------------------------------------------------------------
# Add a hidden "Last Refresh" table with one M-partition column + a
# user-facing measure that surfaces the timestamp of the last refresh.
# Original Python: pbi_fixer/src/_Add_Table_LastRefresh.py.
#
# Skips creation if any existing table name contains "refresh"
# (case-insensitive). When creating, the measure is placed in the first
# table whose name contains "measure" if one exists, otherwise on the
# new "Last Refresh" table itself.

_LR_TABLE_NAME = "Last Refresh"
_LR_MEASURE_NAME = "Last Refresh Measure"
_LR_MEASURE_DAX = "\"Last Refresh: \" & MAX('Last Refresh'[Last Refreshes])"

_LR_TABLE_TMDL = (
    "table 'Last Refresh'\n"
    "\tisHidden\n"
    "\n"
    "\tcolumn 'Last Refreshes'\n"
    "\t\tdataType: string\n"
    "\t\tsummarizeBy: none\n"
    "\t\tsourceColumn: Last Refreshes\n"
    "\n"
    "\tpartition 'Last Refresh' = m\n"
    "\t\tmode: import\n"
    "\t\tsource = ```\n"
    "\t\t\tlet\n"
    "\t\t\t    #\"Today\" = #table({\"Last Refreshes\"}, "
    "{{DateTime.From(DateTime.LocalNow())}})\n"
    "\t\t\tin\n"
    "\t\t\t    #\"Today\"\n"
    "\t\t\t```\n"
)

_LR_MEASURE_TMDL_BLOCK = (
    "\n"
    "\tmeasure '{name}' = {dax}\n"
    "\t\tformatString: General\n"
    "\t\tdisplayFolder: Meta\n"
)


def _find_refresh_tables(parts: list[Part]) -> list[str]:
    """Return existing table names whose name contains 'refresh'."""
    found: list[str] = []
    for p in parts:
        if not _TABLE_PART.match(p.get("path", "")):
            continue
        tname = _table_name(p)
        if "refresh" in tname.lower():
            found.append(tname)
    return found


def _find_measure_table(parts: list[Part]) -> Part | None:
    """Return the first table part whose table name contains 'measure'."""
    for p in parts:
        if not _TABLE_PART.match(p.get("path", "")):
            continue
        if "measure" in _table_name(p).lower():
            return p
    return None


def add_last_refresh_table(parts: list[Part], scan_only: bool) -> HandlerResult:
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    log: list[str] = []

    refresh = _find_refresh_tables(new_parts)
    if refresh:
        log.append(
            f"Refresh table already exists ({', '.join(refresh)}) — nothing to add."
        )
        return HandlerResult(parts=new_parts, findings=findings, log=log)

    findings.append(Finding(
        object_path=f"'{_LR_TABLE_NAME}'",
        detail="add hidden table + 'Last Refreshes' column + M-partition",
        before="(missing)",
        after=f"table '{_LR_TABLE_NAME}'",
    ))

    measure_host_part = _find_measure_table(new_parts)
    if measure_host_part is not None:
        host_name = _table_name(measure_host_part)
        findings.append(Finding(
            object_path=f"'{host_name}'[{_LR_MEASURE_NAME}]",
            detail=f"add measure to existing '{host_name}' table",
            before="(missing)",
            after=f"measure '{_LR_MEASURE_NAME}' = {_LR_MEASURE_DAX}",
        ))
    else:
        findings.append(Finding(
            object_path=f"'{_LR_TABLE_NAME}'[{_LR_MEASURE_NAME}]",
            detail="no measure table found — placing measure on Last Refresh",
            before="(missing)",
            after=f"measure '{_LR_MEASURE_NAME}' = {_LR_MEASURE_DAX}",
        ))

    if scan_only:
        return HandlerResult(parts=new_parts, findings=findings, log=log)

    # Create the new table part.
    table_body = _LR_TABLE_TMDL
    if measure_host_part is None:
        # Append the measure to the same table.
        table_body = table_body + _LR_MEASURE_TMDL_BLOCK.format(
            name=_LR_MEASURE_NAME, dax=_LR_MEASURE_DAX,
        )
    new_parts.append({
        "path": f"definition/tables/{_LR_TABLE_NAME}.tmdl",
        "payload": _encode(table_body),
        "payloadType": "InlineBase64",
    })

    # If a measure-host table exists, append the measure to it.
    if measure_host_part is not None:
        try:
            host_text = _decode(measure_host_part)
        except Exception:
            host_text = ""
        addition = _LR_MEASURE_TMDL_BLOCK.format(
            name=_LR_MEASURE_NAME, dax=_LR_MEASURE_DAX,
        )
        # Make sure we end on a newline before appending.
        if host_text and not host_text.endswith("\n"):
            host_text += "\n"
        new_text = host_text + addition
        # Find the host part inside ``new_parts`` (it's the same object
        # because we did a shallow copy of every part).
        for np in new_parts:
            if np.get("path") == measure_host_part.get("path"):
                np["payload"] = _encode(new_text)
                np["payloadType"] = np.get("payloadType") or "InlineBase64"
                break

    return HandlerResult(parts=new_parts, findings=findings, log=log)


# ---------------------------------------------------------------------------
# Add_MeasuresFromColumns (v0.49)
# ---------------------------------------------------------------------------
# For every column whose ``summarizeBy`` is set to a real aggregation
# (sum / count / min / max / average / distinctCount), create a measure
# that wraps the aggregation and hide the source column.
#
# Original Python: pbi_fixer/src/_Add_MeasuresFromColumns.py.

_AGG_BY_SUMMARIZEBY = {
    "sum": "SUM",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
    "average": "AVERAGE",
    "distinctcount": "DISTINCTCOUNT",
}


def _table_existing_measure_names(text: str) -> set[str]:
    names: set[str] = set()
    for m in re.finditer(
        r"^[\t ]*measure\s+(['\"]?)(.+?)\1\s*=", text, flags=re.MULTILINE,
    ):
        nm = m.group(2)
        if len(nm) >= 2 and nm[0] == "'" and nm[-1] == "'":
            nm = nm[1:-1].replace("''", "'")
        names.add(nm)
    return names


def _format_dax_measure_block(name: str, dax: str, *, display_folder: str) -> str:
    return (
        "\n"
        f"\tmeasure '{name}' = {dax}\n"
        "\t\tformatString: 0.0\n"
        f"\t\tdisplayFolder: {display_folder}\n"
    )


def add_measures_from_columns(parts: list[Part], scan_only: bool) -> HandlerResult:
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    log: list[str] = []

    # 1. Pick a measure-host table (first table whose name contains
    # "measure"). If none, measures go to their source table.
    measure_host_part = _find_measure_table(new_parts)
    measure_host_name = _table_name(measure_host_part) if measure_host_part else None
    if measure_host_name:
        log.append(f"Auto-detected measure table: '{measure_host_name}'")

    # Track existing measure names per host table to avoid collisions.
    # Keyed by table name.
    existing_by_table: dict[str, set[str]] = {}
    for p in new_parts:
        if not _TABLE_PART.match(p.get("path", "")):
            continue
        try:
            txt = _decode(p)
        except Exception:
            continue
        existing_by_table[_table_name(p)] = _table_existing_measure_names(txt)

    # Pending measure additions: {host_table_name: [(measure_name, dax), ...]}
    pending: dict[str, list[tuple[str, str, str]]] = {}

    # 2. Walk every table; for each numeric column with a real summarizeBy,
    # propose a measure + hide the column.
    for p in new_parts:
        if not _TABLE_PART.match(p.get("path", "")):
            continue
        try:
            text = _decode(p)
        except Exception:
            continue
        tname = _table_name(p)
        lines, blocks = _iter_columns(text)
        modified = False
        for hidx, hindent, end, cname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            sb = (props.get("summarizeBy") or "").strip().lower()
            if sb in ("", "none", "default"):
                continue
            agg = _AGG_BY_SUMMARIZEBY.get(sb)
            if not agg:
                continue
            host = measure_host_name or tname
            existing = existing_by_table.setdefault(host, set())
            if cname in existing:
                continue
            dax = f"{agg}('{tname}'[{cname}])"
            findings.append(Finding(
                object_path=f"'{host}'[{cname}]",
                detail=f"create measure from column '{tname}'[{cname}] (summarizeBy: {sb})",
                before=f"column '{tname}'[{cname}] (visible, summarizeBy: {sb})",
                after=f"measure '{cname}' = {dax}; source column hidden",
            ))
            existing.add(cname)
            pending.setdefault(host, []).append((cname, dax, tname))
            if not scan_only:
                # Hide the source column.
                if (props.get("isHidden") or "").lower() != "true":
                    end = _patch_block_props(
                        lines, hidx, hindent, end, {"isHidden": True},
                    )
                    modified = True
        if not scan_only and modified:
            p["payload"] = _encode("\n".join(lines))
            p["payloadType"] = p.get("payloadType") or "InlineBase64"

    if scan_only or not pending:
        return HandlerResult(parts=new_parts, findings=findings, log=log)

    # 3. Append measure blocks to each host table part.
    for p in new_parts:
        if not _TABLE_PART.match(p.get("path", "")):
            continue
        host = _table_name(p)
        if host not in pending:
            continue
        try:
            text = _decode(p)
        except Exception:
            continue
        if text and not text.endswith("\n"):
            text += "\n"
        for mname, dax, source_table in pending[host]:
            text += _format_dax_measure_block(mname, dax, display_folder=source_table)
        p["payload"] = _encode(text)
        p["payloadType"] = p.get("payloadType") or "InlineBase64"

    return HandlerResult(parts=new_parts, findings=findings, log=log)


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
# Report fixers — chart formatting (P1 batch, ported from
# pbi_fixer/src/report/_Fix_BarChart.py + _Fix_ColumnChart.py)
# ---------------------------------------------------------------------------

_BAR_CHART_TYPES = {"barChart", "clusteredBarChart"}
_COLUMN_CHART_TYPES = {"columnChart", "clusteredColumnChart"}

# (object_name, property_name, target_value, label)
_BAR_CHART_CHECKS: list[tuple[str, str, str, str]] = [
    ("valueAxis",    "showAxisTitle", "false", "X axis title"),
    ("valueAxis",    "show",          "false", "X axis values"),
    ("categoryAxis", "showAxisTitle", "false", "Y axis title"),
    ("labels",       "show",          "true",  "Data labels"),
    ("valueAxis",    "gridlineShow",  "false", "Vertical gridlines"),
]

_COLUMN_CHART_CHECKS: list[tuple[str, str, str, str]] = [
    ("categoryAxis", "showAxisTitle", "false", "X axis title"),
    ("valueAxis",    "showAxisTitle", "false", "Y axis title"),
    ("valueAxis",    "show",          "false", "Y axis values"),
    ("labels",       "show",          "true",  "Data labels"),
    ("categoryAxis", "gridlineShow",  "false", "Vertical gridlines"),
]


def _get_visual_property(visual: dict, object_name: str, property_name: str) -> str | None:
    obj_list = (visual.get("visual") or {}).get("objects", {}).get(object_name, [])
    if not obj_list:
        return None
    return (
        obj_list[0]
        .get("properties", {})
        .get(property_name, {})
        .get("expr", {})
        .get("Literal", {})
        .get("Value")
    )


def _set_visual_property(visual: dict, object_name: str, property_name: str, value: str) -> None:
    objects = visual.setdefault("visual", {}).setdefault("objects", {})
    if object_name not in objects or not objects[object_name]:
        objects[object_name] = [{"properties": {}}]
    obj = objects[object_name][0]
    if "properties" not in obj:
        obj["properties"] = {}
    obj["properties"][property_name] = {"expr": {"Literal": {"Value": value}}}


def _fix_chart_formatting(
    parts: list[Part],
    scan_only: bool,
    *,
    chart_types: set[str],
    checks: list[tuple[str, str, str, str]],
) -> HandlerResult:
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    for p in new_parts:
        if not re.match(r"^definition/pages/[^/]+/visuals/[^/]+/visual\.json$", p.get("path", "")):
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        vt = (doc.get("visual") or {}).get("visualType")
        if vt not in chart_types:
            continue
        path_match = re.match(r"definition/pages/([^/]+)/visuals/([^/]+)/", p["path"])
        page = path_match.group(1) if path_match else "?"
        vname = path_match.group(2) if path_match else "?"

        issues = [
            (object_name, property_name, value, label)
            for object_name, property_name, value, label in checks
            if _get_visual_property(doc, object_name, property_name) != value
        ]
        if not issues:
            continue
        labels = ", ".join(label for _, _, _, label in issues)
        findings.append(Finding(
            object_path=f"{page} › {vname}",
            detail=f"{vt}: {labels}",
        ))
        if not scan_only:
            for object_name, property_name, value, _label in issues:
                _set_visual_property(doc, object_name, property_name, value)
            p["payload"] = _encode(_json.dumps(doc, indent=2))
            p["payloadType"] = p.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=findings, log=[])


def fix_bar_chart(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Apply best-practice formatting to barChart / clusteredBarChart visuals.

    Removes X-axis title + values, Y-axis title and vertical gridlines,
    and turns on data labels.
    """
    return _fix_chart_formatting(
        parts, scan_only,
        chart_types=_BAR_CHART_TYPES,
        checks=_BAR_CHART_CHECKS,
    )


def fix_column_chart(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Apply best-practice formatting to columnChart / clusteredColumnChart visuals.

    Removes X-axis title, Y-axis title + values and vertical gridlines,
    and turns on data labels.
    """
    return _fix_chart_formatting(
        parts, scan_only,
        chart_types=_COLUMN_CHART_TYPES,
        checks=_COLUMN_CHART_CHECKS,
    )


# ---------------------------------------------------------------------------
# Report fixer — visual alignment (P1, ported from _Fix_VisualAlignment.py)
# ---------------------------------------------------------------------------

_ALIGNMENT_CHART_TYPES = {
    "barChart", "clusteredBarChart", "stackedBarChart", "hundredPercentStackedBarChart",
    "columnChart", "clusteredColumnChart", "stackedColumnChart", "hundredPercentStackedColumnChart",
    "lineChart", "areaChart", "stackedAreaChart", "lineStackedColumnComboChart",
    "lineClusteredColumnComboChart", "ribbonChart", "waterfallChart", "funnel",
    "scatterChart", "pieChart", "donutChart",
}

_ALIGNMENT_TOLERANCE_PCT = 2.0
_DEFAULT_PAGE_W = 1280.0
_DEFAULT_PAGE_H = 720.0


def _group_by_tolerance(values: list[float], tolerance: float) -> list[list[int]]:
    """Group indices of `values` that fall within `tolerance` of each other (sorted-anchor scan)."""
    if not values:
        return []
    sorted_pairs = sorted(enumerate(values), key=lambda x: x[1])
    groups: list[list[int]] = [[sorted_pairs[0][0]]]
    anchor = sorted_pairs[0][1]
    for idx, val in sorted_pairs[1:]:
        if abs(val - anchor) <= tolerance:
            groups[-1].append(idx)
        else:
            groups.append([idx])
            anchor = val
    return groups


def fix_visual_alignment(parts: list[Part], scan_only: bool) -> HandlerResult:
    """Snap nearly-aligned chart visuals to a common position/size.

    Within each page, groups visible chart visuals whose width/height/X/Y
    differ by no more than 2 % of the page dimension and snaps them to the
    first visual in the group. Mirrors `_Fix_VisualAlignment.py`.
    """
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []

    # Build per-page page size lookup: page_id → (w, h)
    page_size: dict[str, tuple[float, float]] = {}
    for p in new_parts:
        m = re.match(r"^definition/pages/([^/]+)/page\.json$", p.get("path", ""))
        if not m:
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        page_size[m.group(1)] = (
            float(doc.get("width") or _DEFAULT_PAGE_W),
            float(doc.get("height") or _DEFAULT_PAGE_H),
        )

    # Index visual parts by page
    by_page: dict[str, list[dict]] = {}
    for p in new_parts:
        m = re.match(r"^definition/pages/([^/]+)/visuals/([^/]+)/visual\.json$", p.get("path", ""))
        if not m:
            continue
        try:
            doc = _json.loads(_decode(p))
        except Exception:
            continue
        vt = (doc.get("visual") or {}).get("visualType")
        if vt not in _ALIGNMENT_CHART_TYPES:
            continue
        if (doc.get("visual") or {}).get("isHidden") is True or doc.get("isHidden") is True:
            continue
        pos = doc.get("position") or {}
        try:
            x = float(pos.get("x", 0))
            y = float(pos.get("y", 0))
            w = float(pos.get("width", 0))
            h = float(pos.get("height", 0))
        except (TypeError, ValueError):
            continue
        by_page.setdefault(m.group(1), []).append({
            "part": p,
            "vname": m.group(2),
            "doc": doc,
            "pos": pos,
            "x": x, "y": y, "w": w, "h": h,
            "dirty": False,
        })

    for page_id, visuals in by_page.items():
        if len(visuals) < 2:
            continue
        page_w, page_h = page_size.get(page_id, (_DEFAULT_PAGE_W, _DEFAULT_PAGE_H))
        tol_x = page_w * _ALIGNMENT_TOLERANCE_PCT / 100.0
        tol_y = page_h * _ALIGNMENT_TOLERANCE_PCT / 100.0

        for axis_label, attr, tol in (
            ("width",  "w", tol_x),
            ("height", "h", tol_y),
            ("X",      "x", tol_x),
            ("Y",      "y", tol_y),
        ):
            values = [v[attr] for v in visuals]
            for group in _group_by_tolerance(values, tol):
                if len(group) < 2:
                    continue
                target = values[group[0]]
                for gi in group[1:]:
                    cur = values[gi]
                    if cur == target:
                        continue
                    if abs(cur - target) > tol:
                        continue
                    v = visuals[gi]
                    findings.append(Finding(
                        object_path=f"{page_id} › {v['vname']}",
                        detail=f"{axis_label}: {cur:.0f} → {target:.0f}",
                        before=f"{axis_label}={cur:.0f}",
                        after=f"{axis_label}={target:.0f}",
                    ))
                    # Update the in-memory doc so subsequent axis passes
                    # see the new value (mirrors Python source which writes
                    # back after each group).
                    v[attr] = target
                    v["pos"][{"w": "width", "h": "height", "x": "x", "y": "y"}[attr]] = target
                    v["doc"]["position"] = v["pos"]
                    v["dirty"] = True

        if not scan_only:
            for v in visuals:
                if not v["dirty"]:
                    continue
                v["part"]["payload"] = _encode(_json.dumps(v["doc"], indent=2))
                v["part"]["payloadType"] = v["part"].get("payloadType") or "InlineBase64"

    return HandlerResult(parts=new_parts, findings=findings, log=[])


# ---------------------------------------------------------------------------
# P2 SM fixers (v0.51) — ports of small property-mutation fixers from
# pbi_fixer/src/. All TMDL round-trip; none rename objects.
# ---------------------------------------------------------------------------


# ---- Fix_DateColumnFormat --------------------------------------------------
# Source: _Fix_DateColumnFormat.py — set formatString="mm/dd/yyyy" on
# columns named exactly "Date" that have no formatString.

def fix_date_column_format(parts: list[Part], scan_only: bool) -> HandlerResult:
    def tx(tname: str, text: str):
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        for hidx, hindent, end, cname in reversed(blocks):
            if cname.lower() != "date":
                continue
            props = _read_block_props(lines, hidx, end)
            if (props.get("formatString") or "").strip():
                continue
            findings.append(Finding(
                object_path=f"'{tname}'[{cname}]",
                detail="formatString → mm/dd/yyyy",
                before="(none)",
                after='formatString: "mm/dd/yyyy"',
            ))
            _patch_block_props(lines, hidx, hindent, end, {"formatString": "mm/dd/yyyy"})
        return "\n".join(lines), findings, []

    return _apply_per_table(parts, scan_only, tx)


# ---- Fix_DataCategory ------------------------------------------------------
# Source: _Fix_DataCategory.py — set dataCategory on columns whose name
# matches well-known location / URL / image patterns and which have no
# (or "Uncategorized") dataCategory yet.

_DATA_CATEGORY_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bcity\b", re.IGNORECASE), "City"),
    (re.compile(r"\bcountry\b", re.IGNORECASE), "Country"),
    (re.compile(r"\bstate\b|\bprovince\b", re.IGNORECASE), "StateOrProvince"),
    (re.compile(r"\bpostal\s*code\b|\bzip\s*code\b|\bzip\b|\bplz\b", re.IGNORECASE), "PostalCode"),
    (re.compile(r"\bcontinent\b", re.IGNORECASE), "Continent"),
    (re.compile(r"\blatitude\b|\blat\b", re.IGNORECASE), "Latitude"),
    (re.compile(r"\blongitude\b|\blon\b|\blng\b", re.IGNORECASE), "Longitude"),
    (re.compile(r"\burl\b|\bweb\s*url\b|\bwebsite\b|\blink\b", re.IGNORECASE), "WebUrl"),
    (re.compile(r"\bimage\s*url\b|\bimage\b|\bthumbnail\b|\bphoto\b|\bpicture\b", re.IGNORECASE), "ImageUrl"),
    (re.compile(r"\baddress\b", re.IGNORECASE), "Address"),
    (re.compile(r"\bcounty\b", re.IGNORECASE), "County"),
]


def fix_data_category(parts: list[Part], scan_only: bool) -> HandlerResult:
    def tx(tname: str, text: str):
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        for hidx, hindent, end, cname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            current = (props.get("dataCategory") or "").strip()
            if current and current.lower() != "uncategorized":
                continue
            matched: str | None = None
            for pat, cat in _DATA_CATEGORY_MAP:
                if pat.search(cname):
                    matched = cat
                    break
            if matched is None:
                continue
            findings.append(Finding(
                object_path=f"'{tname}'[{cname}]",
                detail=f"dataCategory → {matched}",
                before=f"dataCategory: {current or '(none)'}",
                after=f"dataCategory: {matched}",
            ))
            _patch_block_props(lines, hidx, hindent, end, {"dataCategory": matched})
        return "\n".join(lines), findings, []

    return _apply_per_table(parts, scan_only, tx)


# ---- Fix_MarkPrimaryKeys ---------------------------------------------------
# Source: _Fix_MarkPrimaryKeys.py — for every relationship's To column,
# set isKey: true on that column when its table currently has no key column.

def fix_mark_primary_keys(parts: list[Part], scan_only: bool) -> HandlerResult:
    # 1. Collect (table, column) pairs from relationships' "to" side.
    pk_pairs: set[tuple[str, str]] = set()

    def _scan_rel_text(text: str) -> None:
        for m in re.finditer(
            r"toColumn:\s*('?)(.+?)\1\.('?)(.+?)\3(?:\s|$)",
            text,
            flags=re.MULTILINE,
        ):
            pk_pairs.add((m.group(2), m.group(4)))

    rel_part = next((p for p in parts if p.get("path") == _REL_PART), None)
    if rel_part:
        try:
            _scan_rel_text(_decode(rel_part))
        except Exception:
            pass
    if not pk_pairs:
        model_part = next((p for p in parts if p.get("path") == "definition/model.tmdl"), None)
        if model_part:
            try:
                _scan_rel_text(_decode(model_part))
            except Exception:
                pass

    # 2. Pre-pass: for each table, figure out whether it already has any
    #    column with isKey: true (so we can skip the whole table).
    tables_with_existing_key: set[str] = set()
    for p in parts:
        if not _TABLE_PART.match(p.get("path", "")):
            continue
        try:
            text = _decode(p)
        except Exception:
            continue
        tname = _table_name(p)
        _, blocks = _iter_columns(text)
        lines = text.split("\n")
        for hidx, _hindent, end, _cname in blocks:
            props = _read_block_props(lines, hidx, end)
            if (props.get("isKey") or "").lower() == "true":
                tables_with_existing_key.add(tname)
                break

    def tx(tname: str, text: str):
        if tname in tables_with_existing_key:
            return text, [], []
        targets = {col for (t, col) in pk_pairs if t == tname}
        if not targets:
            return text, [], []
        lines, blocks = _iter_columns(text)
        findings: list[Finding] = []
        # Only mark the FIRST relationship-target column on the table — once
        # we add isKey:true the table now has a key, so skip the rest.
        marked_one = False
        for hidx, hindent, end, cname in reversed(blocks):
            if marked_one:
                continue
            if cname not in targets:
                continue
            props = _read_block_props(lines, hidx, end)
            if (props.get("isKey") or "").lower() == "true":
                marked_one = True
                continue
            findings.append(Finding(
                object_path=f"'{tname}'[{cname}]",
                detail="primary key → isKey: true",
                before="isKey: false (default)",
                after="isKey: true",
            ))
            _patch_block_props(lines, hidx, hindent, end, {"isKey": True})
            marked_one = True
        return "\n".join(lines), findings, []

    res = _apply_per_table(parts, scan_only, tx)
    if not pk_pairs:
        res.log.append("No relationships found — nothing to mark.")
    return res


# ---- Fix_MeasureDescriptions ----------------------------------------------
# Source: _Fix_MeasureDescriptions.py — for every visible measure with no
# description, set description to its DAX expression.
#
# TMDL stores the expression inline on the measure header (or as a body
# block — see fix_avoid_adding_zero). For the description property we
# only care about the expression *text* — the simplest reliable read is
# from the inline header (which covers the vast majority of measures).
# Block-form expressions are rare in practice and we conservatively skip
# them rather than risk a wrong description.

def fix_measure_descriptions(parts: list[Part], scan_only: bool) -> HandlerResult:
    def tx(tname: str, text: str):
        lines, blocks = _iter_measures(text)
        findings: list[Finding] = []
        for hidx, hindent, end, mname in reversed(blocks):
            props = _read_block_props(lines, hidx, end)
            if (props.get("description") or "").strip():
                continue
            if (props.get("isHidden") or "").lower() == "true":
                continue
            m = _MEASURE_HEADER_LINE.match(lines[hidx])
            if not m:
                continue
            inline_expr = (m.group(5) or "").strip()
            if not inline_expr:
                # Block-form expression — conservatively skip.
                continue
            preview = inline_expr if len(inline_expr) <= 60 else inline_expr[:57] + "..."
            findings.append(Finding(
                object_path=f"'{tname}'[{mname}]",
                detail=f"description ← {preview}",
                before="(no description)",
                after=f'description: "{preview}"',
            ))
            _patch_block_props(lines, hidx, hindent, end, {"description": inline_expr})
        return "\n".join(lines), findings, []

    return _apply_per_table(parts, scan_only, tx)


# ---- Fix_UseDivideFunction -------------------------------------------------
# Source: _Fix_UseDivideFunction.py — rewrite simple `<expr> / <expr>`
# patterns inside measure expressions to `DIVIDE(<expr>, <expr>)`.
#
# We deliberately mirror the original regex: only match when both sides
# of the `/` are bracketed (`[...]` column / measure ref) or parenthesised
# `(...)` sub-expressions. Anything else is left alone.

_DIVIDE_PAIR = re.compile(
    r"(\[[^\]]+\]|\([^()]*\))\s*/\s*(\[[^\]]+\]|\([^()]*\))"
)


def _rewrite_divide(expr: str) -> tuple[str, int]:
    """Replace simple `A / B` (with bracketed/parenthesised sides) by
    `DIVIDE(A, B)`. Returns ``(new_expr, n_replacements)``."""
    n = 0

    def _sub(match: re.Match) -> str:
        nonlocal n
        n += 1
        return f"DIVIDE({match.group(1)}, {match.group(2)})"

    new_expr = _DIVIDE_PAIR.sub(_sub, expr)
    return new_expr, n


def fix_use_divide_function(parts: list[Part], scan_only: bool) -> HandlerResult:
    def tx(tname: str, text: str):
        lines = text.split("\n")
        findings: list[Finding] = []
        i = 0
        # Walk forward; we only touch a single line per measure (the
        # inline-expression line) so indices stay stable.
        while i < len(lines):
            m = _MEASURE_HEADER_LINE.match(lines[i])
            if not m:
                i += 1
                continue
            mname = m.group(3)
            if len(mname) >= 2 and mname[0] == "'" and mname[-1] == "'":
                mname = mname[1:-1].replace("''", "'")
            inline_expr = m.group(5)
            if not inline_expr or "/" not in inline_expr:
                i += 1
                continue
            # Skip measures that already use DIVIDE more than `/`.
            if inline_expr.upper().count("DIVIDE") > inline_expr.count("/"):
                i += 1
                continue
            new_expr, n = _rewrite_divide(inline_expr)
            if n > 0:
                findings.append(Finding(
                    object_path=f"'{tname}'[{mname}]",
                    detail=f"rewrite {n} division(s) to DIVIDE()",
                    before=inline_expr.strip(),
                    after=new_expr.strip(),
                ))
                indent = m.group(1)
                quote = m.group(2)
                eq = m.group(4)
                lines[i] = f"{indent}measure {quote}{m.group(3)}{quote}{eq}{new_expr}"
            i += 1
        return "\n".join(lines), findings, []

    return _apply_per_table(parts, scan_only, tx)


# ---- Fix_DefaultDataSourceVersion -----------------------------------------
# Source: _Fix_DefaultDataSourceVersion.py — set
# defaultPowerBIDataSourceVersion: PowerBI_V3 on the model when missing
# / set to anything else. This is a **model-level** property in
# definition/model.tmdl rather than a per-table block.

def fix_default_datasource_version(parts: list[Part], scan_only: bool) -> HandlerResult:
    new_parts = [{**p} for p in parts]
    findings: list[Finding] = []
    log: list[str] = []
    model_part = next(
        (p for p in new_parts if p.get("path") == "definition/model.tmdl"),
        None,
    )
    if model_part is None:
        log.append("definition/model.tmdl not found.")
        return HandlerResult(parts=new_parts, findings=findings, log=log)
    try:
        text = _decode(model_part)
    except Exception:
        log.append("Could not decode model.tmdl.")
        return HandlerResult(parts=new_parts, findings=findings, log=log)

    lines = text.split("\n")
    # Locate `model <Name>` header (no indent).
    header_idx: int | None = None
    for i, ln in enumerate(lines):
        if re.match(r"^model\s+\S", ln):
            header_idx = i
            break
    if header_idx is None:
        log.append("model header not found in model.tmdl.")
        return HandlerResult(parts=new_parts, findings=findings, log=log)

    end_idx = _block_bounds(lines, header_idx, 0)
    props = _read_block_props(lines, header_idx, end_idx)
    current = (props.get("defaultPowerBIDataSourceVersion") or "").strip()
    if current == "PowerBI_V3":
        return HandlerResult(parts=new_parts, findings=findings, log=log)

    findings.append(Finding(
        object_path="model",
        detail="defaultPowerBIDataSourceVersion → PowerBI_V3",
        before=f"defaultPowerBIDataSourceVersion: {current or '(none)'}",
        after="defaultPowerBIDataSourceVersion: PowerBI_V3",
    ))
    if not scan_only:
        _patch_block_props(
            lines, header_idx, 0, end_idx,
            {"defaultPowerBIDataSourceVersion": "PowerBI_V3"},
        )
        model_part["payload"] = _encode("\n".join(lines))
        model_part["payloadType"] = model_part.get("payloadType") or "InlineBase64"
    return HandlerResult(parts=new_parts, findings=findings, log=log)


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
    "Fix_AvoidAdding0": ("sm", fix_avoid_adding_zero),
    "Add_LastRefreshTable": ("sm", add_last_refresh_table),
    "Add_MeasuresFromColumns": ("sm", add_measures_from_columns),
    # P2 SM fixers (v0.51)
    "Fix_DateColumnFormat": ("sm", fix_date_column_format),
    "Fix_DataCategory": ("sm", fix_data_category),
    "Fix_MarkPrimaryKeys": ("sm", fix_mark_primary_keys),
    "Fix_MeasureDescriptions": ("sm", fix_measure_descriptions),
    "Fix_UseDivideFunction": ("sm", fix_use_divide_function),
    "Fix_DefaultDataSourceVersion": ("sm", fix_default_datasource_version),
    # Report fixers (PBIR JSON)
    "Fix_PieChart": ("report", fix_pie_chart),
    "Fix_PageSize": ("report", fix_page_size),
    "Fix_HideVisualFilters": ("report", fix_hide_visual_filters),
    "Fix_DisableShowItemsNoData": ("report", fix_disable_show_items_no_data),
    "Fix_RemoveUnusedCustomVisuals": ("report", fix_remove_unused_custom_visuals),
    "Fix_BarChart": ("report", fix_bar_chart),
    "Fix_ColumnChart": ("report", fix_column_chart),
    "Fix_VisualAlignment": ("report", fix_visual_alignment),
}
