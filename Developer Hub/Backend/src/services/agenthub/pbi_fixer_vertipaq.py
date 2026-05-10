"""Vertipaq Analyzer aggregator (no SLL sidecar).

Fetches the storage DMVs via :mod:`services.xmla_client` and reshapes
them into the six sections that match what Michael Kovalsky's
``sempy_labs.vertipaq_analyzer`` and DAX Studio's Vertipaq Analyzer
produce.

Critical correctness notes — these were the bug in v0.80:
    • ``DISCOVER_STORAGE_TABLES`` does NOT have ``TABLE_SIZE`` /
      ``DATA_SIZE`` / ``DICTIONARY_SIZE`` columns. It has ``ROWS_COUNT``
      and metadata, that's it.
    • ``DISCOVER_STORAGE_TABLE_COLUMNS`` has ``DICTIONARY_SIZE``,
      ``DICTIONARY_TEMPERATURE``, ``DICTIONARY_LAST_ACCESSED``,
      ``DICTIONARY_ISRESIDENT``, ``COLUMN_ENCODING`` — but NOT a
      ``COLUMN_TOTAL_SIZE`` column.
    • Per-column DATA size = SUM(USED_SIZE) over
      ``DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS`` for that column.
    • Per-column HIERARCHY size = SUM(USED_SIZE) over
      ``DISCOVER_STORAGE_TABLE_COLUMN_HIERARCHIES`` (auto attribute
      hierarchies — DIFFERENT DMV from ``USER_HIERARCHIES``).
    • Per-column TOTAL = data + dictionary + hierarchy.
    • Per-table TOTAL = sum(column total) + sum(user-hierarchy USED_SIZE)
      + sum(relationship USED_SIZE for relationships originating in
      this table).
    • Cardinality is NOT in the storage DMVs; sempy_labs derives it
      via DAX (``COUNTROWS(DISTINCT(...))``) when ``read_stats_from_data``
      is true. We surface segment record counts instead and leave the
      true distinct-cardinality column blank.
    • Column display names live in ``TMSCHEMA_COLUMNS.ExplicitName``
      (the storage DMVs only have an internal ``ATTRIBUTE_NAME`` which
      may be a numeric column ID).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException

from services import dax_info_client

log = logging.getLogger("vertipaq_analyzer")


# Internal columns to hide from the Columns tab (mirrors sempy_labs).
def _is_internal_column(name: str) -> bool:
    if not name:
        return True
    return name.startswith("RowNumber") or name.startswith("$") or name == "ID"


def _num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _int(v: Any) -> int:
    return int(_num(v))


def _str(v: Any) -> str:
    return "" if v is None else str(v)


def _safe_pct(num: float, den: float) -> float:
    return 0.0 if den <= 0 else round((num / den) * 100.0, 4)


def _get(row: dict, *keys: str) -> Any:
    """Return the first non-None value across the given key candidates."""
    for k in keys:
        if k in row and row[k] is not None and row[k] != "":
            return row[k]
    return None


# ─────────────────────────────────────────────────────────────────────
#  Workspace / dataset name resolution
# ─────────────────────────────────────────────────────────────────────


async def resolve_workspace_name(
    workspace_id: str,
    *,
    fabric_token: str,
    pbi_token: str | None = None,
) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}",
            headers={"Authorization": f"Bearer {fabric_token}"},
        )
        if resp.status_code == 200:
            data = resp.json()
            name = data.get("displayName") or data.get("name")
            if name:
                return str(name)

        token = pbi_token or fabric_token
        resp2 = await client.get(
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp2.status_code == 200:
            name = resp2.json().get("name")
            if name:
                return str(name)
        raise HTTPException(
            resp2.status_code if resp2.status_code >= 400 else 404,
            f"Workspace {workspace_id} not found or inaccessible: {resp2.text[:300]}",
        )


async def resolve_dataset_name(
    workspace_id: str,
    dataset_id: str,
    *,
    pbi_token: str,
) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}",
            headers={"Authorization": f"Bearer {pbi_token}"},
        )
        if resp.status_code == 200:
            return str(resp.json().get("name") or dataset_id)
        raise HTTPException(
            resp.status_code,
            f"Dataset {dataset_id} not found in workspace {workspace_id}: {resp.text[:300]}",
        )


# ─────────────────────────────────────────────────────────────────────
#  Aggregation
# ─────────────────────────────────────────────────────────────────────


_ENCODING_MAP = {1: "Hash", 2: "Value", 3: "RLE", 4: "Many-to-many", "1": "Hash", "2": "Value"}
_PARTITION_MODE_MAP = {
    1: "Import",
    2: "DirectQuery",
    3: "Default",
    4: "Push",
    5: "Streaming",
    6: "DirectLake",
    7: "DirectLakeOrImport",
}


def aggregate_vertipaq(
    *,
    storage_tables: list[dict[str, Any]],
    storage_columns: list[dict[str, Any]],
    storage_segments: list[dict[str, Any]],
    storage_column_hierarchies: list[dict[str, Any]],
    storage_user_hierarchies: list[dict[str, Any]],
    storage_relationships: list[dict[str, Any]],
    tmschema_tables: list[dict[str, Any]],
    tmschema_columns: list[dict[str, Any]],
    tmschema_partitions: list[dict[str, Any]],
    catalogs: list[dict[str, Any]],
    dataset_name: str,
) -> dict[str, Any]:
    # ── TMSCHEMA lookups for nice display names ──
    # TMSCHEMA_TABLES rows: ID, Name, IsHidden, Description …
    tbl_id_to_name: dict[int, str] = {}
    for t in tmschema_tables:
        tid = _int(_get(t, "ID"))
        nm = _str(_get(t, "Name"))
        if tid and nm:
            tbl_id_to_name[tid] = nm

    # TMSCHEMA_COLUMNS: ID, TableID, ExplicitName, InferredName, ExplicitDataType …
    # Maps storage COLUMN_ID (which == TMSCHEMA_COLUMNS.ID) → friendly column name.
    col_id_to_info: dict[int, dict[str, Any]] = {}
    for c in tmschema_columns:
        cid = _int(_get(c, "ID"))
        if not cid:
            continue
        col_id_to_info[cid] = {
            "name": _str(_get(c, "ExplicitName", "InferredName", "Name")),
            "tableId": _int(_get(c, "TableID")),
            "isHidden": bool(_int(_get(c, "IsHidden") or 0)),
            "dataType": _int(_get(c, "ExplicitDataType", "InferredDataType") or 0),
        }

    # ── Tables: seed from DISCOVER_STORAGE_TABLES (rows count + table id) ──
    tables_acc: dict[str, dict[str, Any]] = {}
    for t in storage_tables:
        name = _str(_get(t, "DIMENSION_NAME"))
        if not name or name.startswith("$") or name.startswith("DateTableTemplate"):
            continue
        rows = _int(_get(t, "ROWS_COUNT"))
        if name not in tables_acc:
            tables_acc[name] = {
                "table": name,
                "rows": 0,
                "totalSize": 0.0,
                "dataSize": 0.0,
                "dictionarySize": 0.0,
                "hierarchySize": 0.0,
                "userHierarchySize": 0.0,
                "relationshipSize": 0.0,
                "partitionsCount": 0,
                "columnsCount": 0,
                "segmentsCount": 0,
                "mode": "",
            }
        # ROWS_COUNT in DISCOVER_STORAGE_TABLES is per partition; sum it.
        tables_acc[name]["rows"] += rows

    # ── Per-column data size from segments ──
    # Key by (table, column_id). Segments has DIMENSION_NAME, COLUMN_ID,
    # ATTRIBUTE_NAME, USED_SIZE, RECORDS_COUNT, SEGMENT_NUMBER.
    col_data_size: dict[tuple[str, int], float] = {}
    col_records: dict[tuple[str, int], int] = {}
    col_segments: dict[tuple[str, int], int] = {}
    col_attr_name: dict[tuple[str, int], str] = {}
    for s in storage_segments:
        tname = _str(_get(s, "DIMENSION_NAME"))
        cid = _int(_get(s, "COLUMN_ID"))
        if not tname or not cid:
            continue
        key = (tname, cid)
        col_data_size[key] = col_data_size.get(key, 0.0) + _num(_get(s, "USED_SIZE"))
        col_records[key] = col_records.get(key, 0) + _int(_get(s, "RECORDS_COUNT"))
        col_segments[key] = col_segments.get(key, 0) + 1
        if key not in col_attr_name:
            attr = _str(_get(s, "ATTRIBUTE_NAME"))
            if attr:
                col_attr_name[key] = attr

    # ── Per-column auto-attribute-hierarchy size ──
    col_hier_size: dict[tuple[str, int], float] = {}
    for h in storage_column_hierarchies:
        tname = _str(_get(h, "DIMENSION_NAME"))
        cid = _int(_get(h, "COLUMN_ID"))
        if not tname or not cid:
            continue
        col_hier_size[(tname, cid)] = col_hier_size.get((tname, cid), 0.0) + _num(_get(h, "USED_SIZE"))

    # ── Build column rows by joining DISCOVER_STORAGE_TABLE_COLUMNS
    #    (dictionary size + encoding + temperature) with the segment
    #    aggregates above.
    col_rows: list[dict[str, Any]] = []
    for c in storage_columns:
        tname = _str(_get(c, "DIMENSION_NAME"))
        cid = _int(_get(c, "COLUMN_ID"))
        if not tname or not cid:
            continue

        info = col_id_to_info.get(cid, {})
        # Resolve display name: TMSCHEMA explicit/inferred name beats
        # ATTRIBUTE_NAME from segments, which beats raw COLUMN_ID.
        display_name = (
            info.get("name")
            or col_attr_name.get((tname, cid))
            or f"col_{cid}"
        )
        if _is_internal_column(display_name):
            continue

        dict_size = _num(_get(c, "DICTIONARY_SIZE"))
        data_size = col_data_size.get((tname, cid), 0.0)
        hier_size = col_hier_size.get((tname, cid), 0.0)
        total = dict_size + data_size + hier_size

        encoding_raw = _get(c, "COLUMN_ENCODING")
        encoding = _ENCODING_MAP.get(encoding_raw, _str(encoding_raw))

        col_rows.append({
            "table": tname,
            "column": display_name,
            "totalSize": total,
            "dataSize": data_size,
            "dictionarySize": dict_size,
            "hierarchySize": hier_size,
            "encoding": encoding,
            "isResident": bool(_int(_get(c, "DICTIONARY_ISRESIDENT") or 0)),
            "temperature": _num(_get(c, "DICTIONARY_TEMPERATURE")),
            "lastAccessed": _str(_get(c, "DICTIONARY_LAST_ACCESSED")),
            "records": col_records.get((tname, cid), 0),
            "segments": col_segments.get((tname, cid), 0),
            "dataType": _str(_get(c, "COLUMN_TYPE")),
        })

        # Roll into the table aggregate.
        ent = tables_acc.get(tname)
        if ent is not None:
            ent["columnsCount"] += 1
            ent["dataSize"] += data_size
            ent["dictionarySize"] += dict_size
            ent["hierarchySize"] += hier_size
            ent["segmentsCount"] += col_segments.get((tname, cid), 0)

    # ── User hierarchies (table-level) ──
    hier_rows: list[dict[str, Any]] = []
    for h in storage_user_hierarchies:
        tname = _str(_get(h, "DIMENSION_NAME"))
        hname = _str(_get(h, "HIERARCHY_NAME"))
        used = _num(_get(h, "USED_SIZE"))
        hier_rows.append({
            "table": tname,
            "hierarchy": hname,
            "usedSize": used,
            "rowsCount": _int(_get(h, "ROWS_COUNT")),
        })
        ent = tables_acc.get(tname)
        if ent is not None:
            ent["userHierarchySize"] += used

    # ── Relationships ──
    rel_rows: list[dict[str, Any]] = []
    for r in storage_relationships:
        from_table = _str(_get(r, "DIMENSION_NAME"))
        # Resolve PARENT_TABLE_ID → name via TMSCHEMA when possible.
        parent_name = _str(_get(r, "PARENT_TABLE_NAME"))
        if not parent_name:
            pid = _int(_get(r, "PARENT_TABLE_ID"))
            if pid and pid in tbl_id_to_name:
                parent_name = tbl_id_to_name[pid]
        used = _num(_get(r, "USED_SIZE"))
        rel_rows.append({
            "fromTable": from_table,
            "fromColumn": _str(_get(r, "FROM_COLUMN", "FROM_TABLE_COLUMN_NAME")),
            "toTable": parent_name,
            "toColumn": _str(_get(r, "TO_COLUMN", "PARENT_TABLE_COLUMN_NAME")),
            "usedSize": used,
            "maxFromCardinality": _int(_get(r, "MAX_FROM_CARDINALITY")),
            "maxToCardinality": _int(_get(r, "MAX_TO_CARDINALITY")),
            "missingKeys": _int(_get(r, "MISSING_KEYS") or 0),
        })
        ent = tables_acc.get(from_table)
        if ent is not None:
            ent["relationshipSize"] += used

    # ── Partitions (TMSCHEMA_PARTITIONS) ──
    part_rows: list[dict[str, Any]] = []
    for p in tmschema_partitions:
        tid = _int(_get(p, "TableID"))
        tname = tbl_id_to_name.get(tid, "")
        mode_int = _int(_get(p, "Mode"))
        mode_label = _PARTITION_MODE_MAP.get(mode_int, str(mode_int) if mode_int else "—")
        part_rows.append({
            "table": tname,
            "partition": _str(_get(p, "Name")),
            "mode": mode_label,
            "dataSourceType": _str(_get(p, "DataSourceType")),
            "modifiedTime": _str(_get(p, "ModifiedTime")),
            "refreshedTime": _str(_get(p, "RefreshedTime")),
        })
        ent = tables_acc.get(tname)
        if ent is not None:
            ent["partitionsCount"] += 1
            if not ent["mode"]:
                ent["mode"] = mode_label

    # ── Finalise table totals + percentages ──
    table_rows = list(tables_acc.values())
    for r in table_rows:
        r["totalSize"] = (
            r["dataSize"] + r["dictionarySize"] + r["hierarchySize"]
            + r["userHierarchySize"] + r["relationshipSize"]
        )

    db_total = sum(r["totalSize"] for r in table_rows) or 1.0
    for r in table_rows:
        r["pctDb"] = _safe_pct(r["totalSize"], db_total)

    # Column-level percentages (now that table totals are known).
    table_total_by_name = {r["table"]: r["totalSize"] for r in table_rows}
    for r in col_rows:
        r["pctDb"] = _safe_pct(r["totalSize"], db_total)
        r["pctTable"] = _safe_pct(r["totalSize"], table_total_by_name.get(r["table"], 0.0))

    # ── Catalog row → compatibility level / default mode ──
    cat_row = next(
        (
            c for c in catalogs
            if _str(_get(c, "CATALOG_NAME")).lower() == dataset_name.lower()
        ),
        catalogs[0] if catalogs else {},
    )
    compat_level = _str(_get(cat_row, "COMPATIBILITY_LEVEL"))
    default_mode = _str(_get(cat_row, "Type", "DefaultMode"))

    model_row = {
        "datasetName": dataset_name,
        "compatibilityLevel": compat_level,
        "defaultMode": default_mode,
        "totalSize": db_total if db_total > 1.0 else 0.0,
        "tableCount": len(table_rows),
        "columnCount": len(col_rows),
        "partitionCount": len(part_rows),
        "hierarchyCount": len(hier_rows),
        "relationshipCount": len(rel_rows),
        "totalRows": sum(r["rows"] for r in table_rows),
    }

    return {
        "sections": {
            "model": [model_row],
            "tables": table_rows,
            "partitions": part_rows,
            "columns": col_rows,
            "hierarchies": hier_rows,
            "relationships": rel_rows,
        },
    }


async def run_vertipaq_analyzer(
    *,
    workspace_id: str,
    dataset_id: str,
    workspace_name: str | None,
    dataset_name: str | None,
    fabric_token: str,
    pbi_token: str,
) -> dict[str, Any]:
    if not workspace_name:
        workspace_name = await resolve_workspace_name(
            workspace_id, fabric_token=fabric_token, pbi_token=pbi_token,
        )
    if not dataset_name:
        dataset_name = await resolve_dataset_name(
            workspace_id, dataset_id, pbi_token=pbi_token,
        )

    bundle = await dax_info_client.fetch_vertipaq_via_execute_queries(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        pbi_token=pbi_token,
    )
    result = aggregate_vertipaq(dataset_name=dataset_name, **bundle)
    result["meta"] = {
        "workspaceId": workspace_id,
        "workspaceName": workspace_name,
        "datasetId": dataset_id,
        "datasetName": dataset_name,
    }
    return result
