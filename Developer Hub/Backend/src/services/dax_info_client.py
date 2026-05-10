"""Vertipaq metadata via the Power BI REST ``executeQueries`` endpoint.

Background
----------
- Raw HTTPS POST to the public Power BI XMLA endpoint does not work
  for SOAP envelopes — only AMO/ADOMD clients negotiate that route
  (verified by failed test ``20260509-0019-vertipaq-datapod-header``).
- ``executeQueries`` accepts arbitrary DAX over HTTPS but rejects
  bare ``INFO.*`` (TMSCHEMA-equivalent) and ``INFO.STORAGE*`` families
  with engine error 3239575574 unless the caller has Write permission
  on the model. Read/Build users get 400 even on
  ``EVALUATE INFO.TABLES()``.
- The user-facing ``INFO.VIEW.*`` family is the only TMSCHEMA-equivalent
  set that runs successfully through ``executeQueries`` for
  Build/Read users (verified on Bad Report - Testing dataset 2026-05).

Strategy
--------
Fetch ``INFO.VIEW.TABLES`` / ``INFO.VIEW.COLUMNS`` /
``INFO.VIEW.RELATIONSHIPS`` plus per-visible-table ``COUNTROWS()``,
then synthesise dict rows in the exact DMV-shape (``DIMENSION_NAME``,
``ROWS_COUNT``, ``COLUMN_ID``, ``DICTIONARY_SIZE``, ...) that the
existing ``aggregate_vertipaq`` consumes unchanged. Storage-only
fields are zeroed; the UI then shows "0 B" for sizes while still
rendering every table, column, and relationship by name.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import HTTPException

log = logging.getLogger("dax_info_client")

PBI_API_HOST = "https://api.powerbi.com"
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


def _strip_col_name(raw: str) -> str:
    if not raw:
        return raw
    s = raw
    if "[" in s:
        s = s[s.index("[") + 1:]
    if s.endswith("]"):
        s = s[:-1]
    return s


def _normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{_strip_col_name(k): v for k, v in row.items()} for row in rows]


async def _execute_query(
    client: httpx.AsyncClient,
    *,
    workspace_id: str,
    dataset_id: str,
    pbi_token: str,
    dax: str,
) -> list[dict[str, Any]]:
    url = (
        f"{PBI_API_HOST}/v1.0/myorg/groups/{workspace_id}"
        f"/datasets/{dataset_id}/executeQueries"
    )
    headers = {
        "Authorization": f"Bearer {pbi_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "queries": [{"query": dax}],
        "serializerSettings": {"includeNulls": True},
    }
    try:
        resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"executeQueries transport error: {exc}") from exc

    if resp.status_code == 401:
        raise HTTPException(
            401,
            f"executeQueries rejected the OBO token (401). Body: {resp.text[:300]}",
        )
    if resp.status_code == 403:
        raise HTTPException(
            403,
            f"executeQueries refused the request (403). Verify dataset Build "
            f"permission. Body: {resp.text[:300]}",
        )
    if resp.status_code != 200:
        raise HTTPException(
            502,
            f"executeQueries failed ({resp.status_code}) for `{dax}`: {resp.text[:400]}",
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise HTTPException(502, f"executeQueries returned non-JSON: {exc}") from exc

    results = body.get("results") or []
    if not results:
        return []
    tables = (results[0] or {}).get("tables") or []
    if not tables:
        return []
    rows = (tables[0] or {}).get("rows") or []
    return _normalise_rows(rows)


def _escape_table_name(name: str) -> str:
    # DAX uses single quotes for table names; double-up internal quotes.
    return "'" + name.replace("'", "''") + "'"


async def _safe_query(
    client: httpx.AsyncClient,
    *,
    workspace_id: str,
    dataset_id: str,
    pbi_token: str,
    dax: str,
    label: str,
) -> list[dict[str, Any]]:
    try:
        return await _execute_query(
            client,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            pbi_token=pbi_token,
            dax=dax,
        )
    except HTTPException as exc:
        log.warning("Vertipaq query '%s' skipped: %s", label, exc.detail)
        return []


async def fetch_vertipaq_via_execute_queries(
    *,
    workspace_id: str,
    dataset_id: str,
    pbi_token: str,
    timeout: httpx.Timeout | None = None,
) -> dict[str, list[dict[str, Any]]]:
    async with httpx.AsyncClient(
        timeout=timeout or _DEFAULT_TIMEOUT,
        follow_redirects=True,
    ) as client:
        view_tables = await _execute_query(
            client,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            pbi_token=pbi_token,
            dax="EVALUATE INFO.VIEW.TABLES()",
        )

        view_columns, view_relationships = await asyncio.gather(
            _safe_query(
                client,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                pbi_token=pbi_token,
                dax="EVALUATE INFO.VIEW.COLUMNS()",
                label="INFO.VIEW.COLUMNS",
            ),
            _safe_query(
                client,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                pbi_token=pbi_token,
                dax="EVALUATE INFO.VIEW.RELATIONSHIPS()",
                label="INFO.VIEW.RELATIONSHIPS",
            ),
        )

        countable: list[str] = []
        for t in view_tables:
            name = str(t.get("Name") or "").strip()
            if not name or name.startswith("$") or name.startswith("DateTableTemplate"):
                continue
            countable.append(name)

        async def _count(name: str) -> tuple[str, int]:
            dax = f"EVALUATE ROW(\"c\", COUNTROWS({_escape_table_name(name)}))"
            rows = await _safe_query(
                client,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                pbi_token=pbi_token,
                dax=dax,
                label=f"COUNTROWS({name})",
            )
            n = 0
            if rows:
                v = next(iter(rows[0].values()), 0)
                try:
                    n = int(v) if v is not None else 0
                except (TypeError, ValueError):
                    n = 0
            return name, n

        count_results = await asyncio.gather(*[_count(n) for n in countable]) if countable else []
        row_counts: dict[str, int] = dict(count_results)

    tmschema_tables: list[dict[str, Any]] = []
    storage_tables: list[dict[str, Any]] = []
    for t in view_tables:
        tid = t.get("ID")
        name = t.get("Name")
        if tid is None or not name:
            continue
        tmschema_tables.append({
            "ID": tid,
            "Name": name,
            "IsHidden": 1 if t.get("IsHidden") else 0,
        })
        storage_tables.append({
            "DIMENSION_NAME": name,
            "ROWS_COUNT": row_counts.get(str(name), 0),
        })

    name_to_table_id: dict[str, Any] = {
        str(t.get("Name")): t.get("ID")
        for t in view_tables
        if t.get("Name") is not None and t.get("ID") is not None
    }
    tmschema_columns: list[dict[str, Any]] = []
    storage_columns: list[dict[str, Any]] = []
    for c in view_columns:
        cid = c.get("ID")
        cname = c.get("Name")
        tname = c.get("Table")
        if cid is None or not cname or not tname:
            continue
        tid = name_to_table_id.get(str(tname))
        tmschema_columns.append({
            "ID": cid,
            "TableID": tid,
            "ExplicitName": cname,
            "InferredName": cname,
            "IsHidden": 1 if c.get("IsHidden") else 0,
            "ExplicitDataType": 0,
        })
        storage_columns.append({
            "DIMENSION_NAME": tname,
            "COLUMN_ID": cid,
            "DICTIONARY_SIZE": 0,
            "COLUMN_ENCODING": 0,
            "COLUMN_TYPE": str(c.get("DataType") or ""),
            "DICTIONARY_ISRESIDENT": 0,
            "DICTIONARY_TEMPERATURE": 0,
            "DICTIONARY_LAST_ACCESSED": "",
        })

    return {
        "storage_tables":              storage_tables,
        "storage_columns":             storage_columns,
        "storage_segments":            [],
        "storage_column_hierarchies":  [],
        "storage_user_hierarchies":    [],
        "storage_relationships":       [],
        "tmschema_tables":             tmschema_tables,
        "tmschema_columns":            tmschema_columns,
        "tmschema_partitions":         [],
        "catalogs":                    [],
    }
