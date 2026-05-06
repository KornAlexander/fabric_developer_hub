"""TMDL culture-file read/merge/write for the PBI Fixer translations apply
endpoint.

The Fabric ``/semanticModels/{id}/getDefinition`` + ``/updateDefinition``
round-trip exposes the model as a list of TMDL ``parts`` (path + base64
payload). For translations we only need to touch ``definition/cultures/
<culture>.tmdl`` — its body declares per-table / per-child translation
properties (``caption`` / ``description`` / ``displayFolder``) under a
``translations`` block.

This module:
- parses an existing culture body into a structured map,
- merges a new batch of translation items into that map,
- re-serialises a deterministic TMDL body that the Fabric serializer
  accepts on round-trip.

Reference: https://learn.microsoft.com/analysis-services/tmdl/tmdl-reference-tabular-object#translations-in-tmdl
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Property keys we know how to write in a culture file. ``description``
# only applies on Description-typed source items; ``displayFolder`` is
# not currently produced by the frontend but the parser preserves it on
# round-trip so manually-authored values aren't lost.
_KNOWN_PROPS = ("caption", "description", "displayFolder")

# Child object kinds that can carry translations under a table.
_CHILD_KINDS = ("column", "measure", "hierarchy")

_TABLE_KEY = ("Table", "")  # sentinel inside the per-table dict


@dataclass
class CultureModel:
    """Parsed culture file contents.

    ``by_table[<table>][<(kind, name)>] = {prop: value, ...}``. The
    sentinel key ``("Table", "")`` holds the table-level translation
    properties (caption / description on the table itself).

    ``model_props`` mirrors the same shape for the optional ``model
    <Name>`` block under ``translations``.
    """

    culture: str
    model_name: str | None = None
    model_props: dict[str, str] = field(default_factory=dict)
    by_table: dict[str, dict[tuple[str, str], dict[str, str]]] = field(default_factory=dict)
    linguistic_block: str | None = None  # raw text, including leading indent


def _unquote(name: str) -> str:
    s = name.strip()
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    return s


def _quote(name: str) -> str:
    """TMDL: enclose in single quotes if the name contains any of
    ``. = : '`` or whitespace; double-up embedded single quotes."""
    if not name:
        return "''"
    needs = any(c in name for c in " .=:'") or not name
    if needs:
        return "'" + name.replace("'", "''") + "'"
    return name


def _unescape_value(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('""', '"')
    return s


def _escape_value(v: str) -> str:
    """Wrap in double-quotes when the value has surrounding whitespace
    or embedded double-quotes; doubles up embedded ``"``."""
    if v == "" or v != v.strip() or '"' in v or any(c in v for c in ":#-"):
        return '"' + v.replace('"', '""') + '"'
    return v


def parse_culture(text: str) -> CultureModel:
    """Parse a ``cultureInfo``/``culture`` TMDL body.

    Tolerant of indentation (1 tab / 2 spaces / 4 spaces). Unknown
    blocks are skipped (kept untouched in ``linguistic_block`` only when
    encountered as ``linguisticMetadata = ...``)."""
    cm = CultureModel(culture="")
    if not text:
        return cm

    # Normalise indentation by counting leading whitespace runs. We use
    # the indentation *level* not raw column count to be tolerant of
    # tab vs spaces.
    lines = text.split("\n")

    # Header: ``cultureInfo <code>`` or ``culture <code>``.
    for line in lines:
        m = re.match(r"^\s*(cultureInfo|culture)\s+(\S+)\s*$", line)
        if m:
            cm.culture = m.group(2).strip()
            break

    # Walk the file level-by-level. We track whether we're currently
    # inside the ``translations`` block, the current table, and the
    # current child (column/measure/hierarchy).
    in_translations = False
    cur_table: str | None = None
    cur_child: tuple[str, str] | None = None  # (Kind, Name)
    in_model = False  # inside `model <Name>` under translations

    i = 0
    while i < len(lines):
        line = lines[i]
        bare = line.strip()

        # Capture linguisticMetadata block verbatim (keeps roundtrip
        # of N-Gramm / inflectional data the user may have authored).
        m_ling = re.match(r"^(\s*)linguisticMetadata\b", line)
        if m_ling:
            indent_prefix = m_ling.group(1)
            block: list[str] = [line]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() == "":
                    block.append(nxt)
                    j += 1
                    continue
                # Stop when we hit a line indented less than the
                # linguisticMetadata header (i.e., a sibling of culture
                # body) — but linguisticMetadata is normally the LAST
                # block in the file, so we typically run to EOF.
                lead = len(nxt) - len(nxt.lstrip())
                if lead <= len(indent_prefix) and not nxt.startswith(indent_prefix + " ") and not nxt.startswith(indent_prefix + "\t"):
                    # Heuristic: if the next non-empty line is at or
                    # less indented than `linguisticMetadata`, stop.
                    # Practically nothing follows it.
                    break
                block.append(nxt)
                j += 1
            cm.linguistic_block = "\n".join(block).rstrip()
            i = j
            in_translations = False
            cur_table = None
            cur_child = None
            in_model = False
            continue

        if bare == "translations":
            in_translations = True
            cur_table = None
            cur_child = None
            in_model = False
            i += 1
            continue

        if not in_translations:
            i += 1
            continue

        # Inside `translations`. Decide what this line means based on
        # its starting keyword (after stripping whitespace).
        if not bare:
            i += 1
            continue

        m_table = re.match(r"^table\s+(.+)$", bare)
        m_model = re.match(r"^model\s+(.+)$", bare)
        m_child = re.match(r"^(column|measure|hierarchy)\s+(.+)$", bare)
        m_prop = re.match(r"^(caption|description|displayFolder)\s*:\s*(.*)$", bare)

        if m_table:
            cur_table = _unquote(m_table.group(1))
            cm.by_table.setdefault(cur_table, {})
            cur_child = None
            in_model = False
        elif m_model:
            cm.model_name = _unquote(m_model.group(1))
            in_model = True
            cur_table = None
            cur_child = None
        elif m_child and cur_table is not None:
            kind = m_child.group(1).capitalize()
            name = _unquote(m_child.group(2))
            cur_child = (kind, name)
            cm.by_table[cur_table].setdefault(cur_child, {})
        elif m_prop:
            prop = m_prop.group(1)
            val = _unescape_value(m_prop.group(2))
            if in_model:
                cm.model_props[prop] = val
            elif cur_table is not None:
                target = cm.by_table[cur_table]
                key = cur_child if cur_child is not None else _TABLE_KEY
                target.setdefault(key, {})[prop] = val
        # Unrecognised lines inside translations are ignored — we'll
        # drop them on re-serialise. That's acceptable because the only
        # lines TMDL emits there are model/table/column/measure/hierarchy
        # headers and their three known properties.

        i += 1

    return cm


def serialize_culture(cm: CultureModel, indent: str = "\t") -> str:
    """Serialise a CultureModel back to TMDL. Stable ordering: model
    block first (if any), tables alphabetically, then table-level props,
    then children alphabetically by (kind, name)."""
    out: list[str] = [f"cultureInfo {cm.culture}", ""]

    has_translations = bool(cm.model_name or cm.by_table)
    if has_translations:
        out.append(f"{indent}translations")
        if cm.model_name and cm.model_props:
            out.append(f"{indent * 2}model {_quote(cm.model_name)}")
            for prop in _KNOWN_PROPS:
                if prop in cm.model_props:
                    out.append(f"{indent * 3}{prop}: {_escape_value(cm.model_props[prop])}")

        for tname in sorted(cm.by_table.keys()):
            entries = cm.by_table[tname]
            out.append(f"{indent * 2}table {_quote(tname)}")
            # Table-level props
            table_props = entries.get(_TABLE_KEY, {})
            for prop in _KNOWN_PROPS:
                if prop in table_props:
                    out.append(f"{indent * 3}{prop}: {_escape_value(table_props[prop])}")
            # Children sorted by kind then name for determinism.
            child_items = sorted(
                ((k, v) for k, v in entries.items() if k != _TABLE_KEY),
                key=lambda kv: (kv[0][0], kv[0][1].lower()),
            )
            for (kind, name), props in child_items:
                if not props:
                    continue
                out.append(f"{indent * 3}{kind.lower()} {_quote(name)}")
                for prop in _KNOWN_PROPS:
                    if prop in props:
                        out.append(f"{indent * 4}{prop}: {_escape_value(props[prop])}")

    if cm.linguistic_block:
        out.append("")
        out.append(cm.linguistic_block.rstrip())

    return "\n".join(out) + "\n"


@dataclass
class ApplyItem:
    """Single translation entry to apply. ``object_type`` controls
    whether ``value`` lands in the ``caption`` or ``description`` slot:

    - ``Table`` / ``Column`` / ``Measure`` / ``Hierarchy`` → caption
    - ``Description`` → description (path identifies the parent object)

    ``object_path`` follows the TranslationsPage encoding: ``"Sales"``
    for tables; ``"Sales[Amount]"`` for column/measure/hierarchy. For
    ``Description`` we accept the same shape and infer the parent kind:
    bare path = table description, ``T[X]`` = column/measure/hierarchy
    description (the caller must pass ``parent_kind`` to disambiguate
    column vs measure if both exist with the same name; otherwise we
    fall back to ``Column``)."""

    object_type: str
    object_path: str
    value: str
    parent_kind: str | None = None  # Optional disambiguation for Description
    proposed_description: str | None = None


_PATH_RE = re.compile(r"^(.+?)\[(.+)\]$")


def _split_path(object_path: str) -> tuple[str, str | None]:
    m = _PATH_RE.match(object_path.strip())
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return (object_path.strip(), None)


def merge_items(cm: CultureModel, items: list[ApplyItem]) -> int:
    """Merge ``items`` into ``cm.by_table`` in place. Returns the count
    of distinct (object, property) pairs touched."""
    touched = 0
    for it in items:
        table, child_name = _split_path(it.object_path)
        if not table:
            continue
        cm.by_table.setdefault(table, {})

        otype = it.object_type
        # The frontend currently emits "Table" | "Column" | "Measure".
        # "Hierarchy" and "Description" are handled defensively.
        if otype == "Table" or (otype == "Description" and child_name is None):
            key = _TABLE_KEY
        elif otype in ("Column", "Measure", "Hierarchy"):
            key = (otype, child_name or "")
        elif otype == "Description":
            kind = (it.parent_kind or "Column").capitalize()
            if kind not in ("Column", "Measure", "Hierarchy"):
                kind = "Column"
            key = (kind, child_name or "")
        else:
            # Unknown type — skip silently
            continue

        prop = "description" if otype == "Description" else "caption"
        cm.by_table[table].setdefault(key, {})[prop] = it.value
        touched += 1

        # If a description was also proposed alongside a caption, set it.
        if otype != "Description" and it.proposed_description:
            cm.by_table[table][key]["description"] = it.proposed_description
            touched += 1

    return touched


def empty_culture(culture: str) -> CultureModel:
    """Bootstrap a fresh culture model when no part exists yet."""
    return CultureModel(culture=culture)
