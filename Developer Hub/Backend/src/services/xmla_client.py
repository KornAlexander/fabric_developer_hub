"""XMLA-over-HTTPS client for the Power BI / Fabric semantic model
endpoint.

Because Power BI's XMLA endpoint requires an ADOMD-style cluster
handshake (the bare ``api.powerbi.com/v1.0/myorg/<ws>`` URL returns
404 to a raw SOAP POST — that route is only used by the AMO/ADOMD
client libraries to *negotiate* a redirect), we have to:

    1. Resolve the user's tenant cluster URL (the ``wabi-*-redirect``
       host) by hitting the global service.
    2. POST the SOAP ``Discover`` envelope at one of several candidate
       XMLA paths under that cluster host. The exact path varies by
       region/tenant; we try a small ranked list and keep whichever
       returns a SOAP rowset.

We deliberately avoid pythonnet / AMO / ADOMD: pure ``httpx`` + stdlib
``xml.etree``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx
from fastapi import HTTPException

log = logging.getLogger("xmla_client")

PBI_API_HOST = "https://api.powerbi.com"

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_XMLA = "urn:schemas-microsoft-com:xml-analysis"
NS_ROW = "urn:schemas-microsoft-com:xml-analysis:rowset"

_SOAPACTION_DISCOVER = '"urn:schemas-microsoft-com:xml-analysis:Discover"'
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_discover_envelope(
    request_type: str,
    catalog: str,
    *,
    restrictions: dict[str, str] | None = None,
) -> str:
    rest_xml = ""
    if restrictions:
        items = "".join(
            f"<{k}>{_xml_escape(str(v))}</{k}>" for k, v in restrictions.items()
        )
        rest_xml = f"<RestrictionList>{items}</RestrictionList>"

    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{NS_SOAP}">'
        "<soap:Body>"
        f'<Discover xmlns="{NS_XMLA}">'
        f"<RequestType>{_xml_escape(request_type)}</RequestType>"
        f"<Restrictions>{rest_xml}</Restrictions>"
        "<Properties>"
        "<PropertyList>"
        f"<Catalog>{_xml_escape(catalog)}</Catalog>"
        "<Format>Tabular</Format>"
        "<Content>SchemaData</Content>"
        "</PropertyList>"
        "</Properties>"
        "</Discover>"
        "</soap:Body>"
        "</soap:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _coerce(value: str, type_attr: str | None) -> Any:
    if value == "":
        return None
    t = (type_attr or "").lower()
    try:
        if t.endswith(("int", "long", "short", "unsignedint", "unsignedlong", "byte")):
            return int(value)
        if t.endswith(("double", "float", "decimal")):
            return float(value)
        if t.endswith("boolean"):
            return value.lower() in ("true", "1")
    except (ValueError, TypeError):
        pass
    return value


def _parse_rowset(xml_bytes: bytes) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise HTTPException(502, f"XMLA: malformed SOAP response: {exc}") from exc

    fault = root.find(f".//{{{NS_SOAP}}}Fault")
    if fault is not None:
        code = fault.findtext("faultcode") or "Unknown"
        msg = fault.findtext("faultstring") or "(no message)"
        raise HTTPException(502, f"XMLA fault [{code}]: {msg}")

    rows: list[dict[str, Any]] = []
    for row_el in root.iter(f"{{{NS_ROW}}}row"):
        row: dict[str, Any] = {}
        for child in row_el:
            name = _local(child.tag)
            type_attr = child.attrib.get(
                "{http://www.w3.org/2001/XMLSchema-instance}type"
            )
            row[name] = _coerce(child.text or "", type_attr)
        rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────
#  Cluster resolution
# ─────────────────────────────────────────────────────────────────────


_CLUSTER_LOOKUP_PATHS = (
    # Most commonly cited resolver — returns ``{"clusterUrl": "..."}``.
    "/powerbi/globalservice/v201606/clusterDetails",
    # Older/alternate variant.
    "/powerbi/databases/v201606/clusterResolve",
)


async def resolve_cluster_url(
    client: httpx.AsyncClient, *, pbi_token: str
) -> tuple[str, list[dict[str, Any]]]:
    """Return the user's ``https://wabi-*-redirect.analysis.windows.net``
    cluster host, along with the trace of probed URLs (for diagnostics
    on failure).
    """
    attempts: list[dict[str, Any]] = []
    headers = {
        "Authorization": f"Bearer {pbi_token}",
        "Accept": "application/json",
        "X-PowerBI-User-Admin": "1",
    }

    for path in _CLUSTER_LOOKUP_PATHS:
        url = f"{PBI_API_HOST}{path}"
        for method in ("GET", "PUT"):
            try:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.put(url, headers=headers, json={"contextHeaders": {}})
            except httpx.HTTPError as exc:
                attempts.append({"url": url, "method": method, "error": str(exc)})
                continue

            attempts.append({
                "url": url,
                "method": method,
                "status": resp.status_code,
                "body": resp.text[:300],
            })

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    continue
                cluster = (
                    data.get("clusterUrl")
                    or data.get("FixedClusterUri")
                    or data.get("BackendUrl")
                )
                if isinstance(cluster, str) and cluster.startswith("http"):
                    return cluster.rstrip("/"), attempts

    # Fallback: hit the datasets list and read the cluster URL from
    # response headers (some PBI tenants return ``home-cluster-uri``).
    try:
        resp = await client.get(
            f"{PBI_API_HOST}/v1.0/myorg/datasets", headers=headers
        )
        attempts.append({
            "url": f"{PBI_API_HOST}/v1.0/myorg/datasets",
            "method": "GET",
            "status": resp.status_code,
            "headers": {k: v for k, v in resp.headers.items() if "cluster" in k.lower()},
        })
        for hk in ("home-cluster-uri", "x-ms-public-api-cluster-url"):
            v = resp.headers.get(hk)
            if v and v.startswith("http"):
                return v.rstrip("/"), attempts
    except httpx.HTTPError as exc:
        attempts.append({"url": f"{PBI_API_HOST}/v1.0/myorg/datasets", "error": str(exc)})

    raise HTTPException(
        502,
        "Could not resolve the Power BI XMLA cluster URL. "
        f"Tried: {attempts}",
    )


# ─────────────────────────────────────────────────────────────────────
#  XMLA Discover (ranked URL fallback)
# ─────────────────────────────────────────────────────────────────────


def _candidate_xmla_urls(cluster: str, workspace_name: str) -> list[str]:
    ws = quote(workspace_name, safe="")
    return [
        f"{cluster}/webapi/xmla",
        f"{cluster}/xmla",
        f"{cluster}/webapi/xmla?vs={ws}",
        f"{cluster}/xmla?vs={ws}",
        f"{cluster}/v1.0/myorg/{ws}",
        # Last-resort: the bare api.powerbi.com URL (known to 404 today
        # but kept for the day Microsoft enables it).
        f"{PBI_API_HOST}/v1.0/myorg/{ws}",
    ]


async def _xmla_discover_at(
    client: httpx.AsyncClient,
    url: str,
    body: str,
    pbi_token: str,
    workspace_id: str | None,
) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {pbi_token}",
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": _SOAPACTION_DISCOVER,
        "Accept": "text/xml, application/xml",
    }
    # ADOMD over HTTP routes to a specific tenant workspace via this
    # header. Without it the gateway doesn't know which "DataPod" to
    # forward the SOAP call to and 404s.
    if workspace_id:
        headers["X-AS-DataPodID"] = workspace_id
    return await client.post(url, headers=headers, content=body.encode("utf-8"))


async def _xmla_discover(
    client: httpx.AsyncClient,
    cluster: str,
    workspace_name: str,
    workspace_id: str | None,
    dataset_name: str,
    request_type: str,
    *,
    restrictions: dict[str, str] | None = None,
    pbi_token: str,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run a single Discover, sticking to the first XMLA URL that
    returned a parseable SOAP envelope on any prior call.
    """
    body = _build_discover_envelope(request_type, dataset_name, restrictions=restrictions)

    # Once we discover the working URL we stash it on the shared state
    # so the remaining DMVs don't have to re-probe.
    chosen = state.get("xmla_url")
    candidates = [chosen] if chosen else _candidate_xmla_urls(cluster, workspace_name)
    last_err: str | None = None

    for url in candidates:
        try:
            resp = await _xmla_discover_at(client, url, body, pbi_token, workspace_id)
        except httpx.HTTPError as exc:
            last_err = f"transport error at {url}: {exc}"
            log.debug("XMLA candidate transport error: %s -> %s", url, exc)
            continue

        ct = resp.headers.get("content-type", "")
        is_soap = "xml" in ct.lower() or resp.text.lstrip().startswith("<")

        if resp.status_code == 200 and is_soap:
            state["xmla_url"] = url
            try:
                return _parse_rowset(resp.content)
            except HTTPException:
                raise
        if resp.status_code == 401:
            raise HTTPException(
                401,
                f"XMLA endpoint rejected the OBO token (401) at {url}: {resp.text[:300]}",
            )
        if resp.status_code == 403:
            raise HTTPException(
                403,
                f"XMLA endpoint refused the request (403) at {url}. "
                "Verify the user has Build/Read access to the dataset and "
                "the workspace is on Premium / Fabric capacity. "
                f"Body: {resp.text[:300]}",
            )

        last_err = f"HTTP {resp.status_code} at {url}: {resp.text[:200] or '(empty body)'}"
        log.debug("XMLA candidate %s -> %s", url, last_err)

    raise HTTPException(
        502,
        f"XMLA Discover {request_type} failed against every candidate URL. "
        f"Last error: {last_err}. Tried: {candidates}. "
        f"X-AS-DataPodID header was {'set' if workspace_id else 'NOT set'}.",
    )


# ── Public per-DMV runner ──


async def fetch_vertipaq_dmvs(
    workspace_name: str,
    dataset_name: str,
    *,
    pbi_token: str,
    workspace_id: str | None = None,
    timeout: httpx.Timeout | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Resolve the cluster, probe for the working XMLA URL via the
    first DMV, then fan out the remaining DMVs in parallel against the
    cached URL.
    """
    state: dict[str, Any] = {}
    async with httpx.AsyncClient(
        timeout=timeout or _DEFAULT_TIMEOUT,
        follow_redirects=True,
    ) as client:
        cluster, _trace = await resolve_cluster_url(client, pbi_token=pbi_token)
        # The Power BI cluster lookup occasionally returns the host in
        # uppercase; some Azure layers route hostnames case-sensitively
        # so normalize defensively.
        cluster = cluster.lower()
        log.info("XMLA cluster resolved: %s", cluster)

        dmvs = (
            "DISCOVER_STORAGE_TABLES",
            "DISCOVER_STORAGE_TABLE_COLUMNS",
            "DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS",
            "DISCOVER_STORAGE_TABLE_COLUMN_HIERARCHIES",
            "DISCOVER_STORAGE_TABLE_USER_HIERARCHIES",
            "DISCOVER_STORAGE_TABLE_RELATIONSHIPS",
            "TMSCHEMA_TABLES",
            "TMSCHEMA_COLUMNS",
            "TMSCHEMA_PARTITIONS",
            "DBSCHEMA_CATALOGS",
        )

        # First DMV serially to lock in the working URL, then the rest
        # in parallel (now that the URL is known they run concurrently
        # without re-probing).
        first = await _xmla_discover(
            client, cluster, workspace_name, workspace_id, dataset_name, dmvs[0],
            pbi_token=pbi_token, state=state,
        )
        rest = await asyncio.gather(*[
            _xmla_discover(
                client, cluster, workspace_name, workspace_id, dataset_name, rt,
                pbi_token=pbi_token, state=state,
            )
            for rt in dmvs[1:]
        ])

    results = [first, *rest]
    keys = (
        "storage_tables",
        "storage_columns",
        "storage_segments",
        "storage_column_hierarchies",
        "storage_user_hierarchies",
        "storage_relationships",
        "tmschema_tables",
        "tmschema_columns",
        "tmschema_partitions",
        "catalogs",
    )
    return dict(zip(keys, results))
