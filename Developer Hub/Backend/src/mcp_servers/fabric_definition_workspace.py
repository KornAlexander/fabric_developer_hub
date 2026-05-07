"""Fabric definition workspace MCP server.

This server provides the canonical local workflow for Fabric item definition
editing:

    checkout -> local files -> validate/diff/plan -> publish

The tools intentionally operate on a checkout id after the initial download so
agents can use local search/edit workflows without repeatedly pulling large
definition payloads into the model context.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from jose import jwt
from mcp.server.fastmcp import FastMCP

from mcp_servers._common import format_http_error, shared_client

FABRIC_API = "https://api.fabric.microsoft.com/v1"
DEFAULT_MAX_POLL_SECONDS = 180
DEFAULT_POLL_INTERVAL_SECONDS = 5
MAX_CHECKOUT_ITEMS = 25

mcp = FastMCP("fabric-definition-workspace", log_level="WARNING")


ITEM_ENDPOINTS: dict[str, dict[str, str]] = {
    "report": {"collection": "reports", "type": "Report"},
    "semanticmodel": {"collection": "semanticModels", "type": "SemanticModel"},
    "notebook": {"collection": "notebooks", "type": "Notebook"},
    "datapipeline": {"collection": "dataPipelines", "type": "DataPipeline"},
    "sparkjobdefinition": {"collection": "sparkJobDefinitions", "type": "SparkJobDefinition"},
}


def _token_claims(token: str) -> dict | None:
    if not token:
        return None
    try:
        claims = jwt.get_unverified_claims(token)
    except Exception:
        return None
    return claims if isinstance(claims, dict) else None


def _token_auth_mode(claims: dict | None) -> str:
    if not claims:
        return "unknown"
    scp = claims.get("scp")
    idtyp = str(claims.get("idtyp") or "").lower()
    roles = claims.get("roles")
    if isinstance(scp, str) and scp.strip():
        return "delegated_user"
    if idtyp == "app" or (roles and not scp):
        return "application"
    return "unknown"


def _delegated_token_block_message(operation: str, claims: dict | None) -> str:
    principal = "the app registration/service principal"
    if isinstance(claims, dict):
        principal = (
            claims.get("app_displayname")
            or claims.get("name")
            or claims.get("appid")
            or claims.get("azp")
            or principal
        )
    return (
        f"Blocked Fabric definition publish for {operation}: FABRIC_API_TOKEN is an "
        f"application/service-principal token ({principal}). AgentHub refuses to publish "
        "Fabric item definitions with app-only identity because newly-created semantic models "
        "would be owned by the app registration instead of the mission user. Re-authenticate "
        "through delegated user/OBO flow and retry."
    )


def _require_delegated_user_token(operation: str) -> None:
    token = os.environ.get("FABRIC_API_TOKEN", "")
    if not token:
        raise RuntimeError("FABRIC_API_TOKEN not set — user may not be authenticated.")
    claims = _token_claims(token)
    if _token_auth_mode(claims) == "application":
        raise RuntimeError(_delegated_token_block_message(operation, claims))


def _headers(*, require_delegated: bool = False, operation: str = "Fabric definition API call") -> dict[str, str]:
    token = os.environ.get("FABRIC_API_TOKEN", "")
    if not token:
        raise RuntimeError("FABRIC_API_TOKEN not set — user may not be authenticated.")
    if require_delegated:
        _require_delegated_user_token(operation)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _checkout_root() -> Path:
    configured = os.environ.get("FABRIC_DEFINITION_WORKSPACE_ROOT")
    root = Path(configured) if configured else Path("/tmp/agenthub-fabric-definition-workspaces")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _checkout_dir(checkout_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9\-]{36}", checkout_id):
        raise ValueError("Invalid checkout_id")
    root = _checkout_root()
    path = (root / checkout_id).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Checkout path escaped root")
    return path


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._")[:80] or "item"


def _safe_relative_path(path: str) -> Path:
    if not path or path.startswith(("/", "~")):
        raise ValueError(f"Unsafe definition part path: {path!r}")
    rel = Path(path)
    if any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError(f"Unsafe definition part path: {path!r}")
    return rel


def _item_key(item: dict[str, Any]) -> str:
    name = item.get("displayName") or item.get("name") or "item"
    item_id = item.get("id") or uuid.uuid4().hex
    return f"{_safe_name(str(name))}_{str(item_id)[:8]}"


def _item_type_key(item_type: str) -> str:
    return re.sub(r"[^a-z0-9]", "", item_type.lower())


def _endpoint_for(item_type: str) -> dict[str, str]:
    key = _item_type_key(item_type)
    endpoint = ITEM_ENDPOINTS.get(key)
    if endpoint is None:
        supported = ", ".join(sorted(v["type"] for v in ITEM_ENDPOINTS.values()))
        raise ValueError(f"Item type {item_type!r} does not support definition checkout yet. Supported: {supported}")
    return endpoint


def _decode_payload(part: dict[str, Any]) -> tuple[str, str]:
    payload = part.get("payload", "")
    payload_type = part.get("payloadType") or part.get("payload_type")

    if payload_type == "InlineBase64" and isinstance(payload, str):
        raw = base64.b64decode(payload.encode("ascii"))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return payload, "base64"
        return _format_text_payload(text, part.get("path", "")), "text"

    if isinstance(payload, (dict, list)):
        return json.dumps(payload, indent=2, sort_keys=True) + "\n", "json"

    if isinstance(payload, str):
        return payload, "text"

    return json.dumps(payload, indent=2, sort_keys=True) + "\n", "json"


def _format_text_payload(text: str, path: str) -> str:
    if path.lower().endswith(".json"):
        try:
            return json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
        except json.JSONDecodeError:
            return text
    return text


def _encode_payload(file_path: Path, encoding: str, original_part: dict[str, Any]) -> dict[str, Any]:
    payload_type = original_part.get("payloadType") or original_part.get("payload_type") or "InlineBase64"
    part: dict[str, Any] = {"path": original_part["path"]}

    content_type = original_part.get("contentType") or original_part.get("content_type")
    if content_type:
        part["contentType"] = content_type

    text = file_path.read_text(encoding="utf-8")
    if encoding == "base64":
        part["payload"] = text.strip()
        part["payloadType"] = payload_type
        return part

    if payload_type == "InlineBase64":
        part["payload"] = base64.b64encode(text.encode("utf-8")).decode("ascii")
        part["payloadType"] = "InlineBase64"
        return part

    if encoding == "json":
        part["payload"] = json.loads(text)
    else:
        part["payload"] = text
    if payload_type:
        part["payloadType"] = payload_type
    return part


def _read_manifest(checkout_id: str) -> dict[str, Any]:
    manifest_path = _checkout_dir(checkout_id) / ".agenthub" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Checkout {checkout_id} was not found")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _write_manifest(checkout_path: Path, manifest: dict[str, Any]) -> None:
    agenthub_dir = checkout_path / ".agenthub"
    agenthub_dir.mkdir(parents=True, exist_ok=True)
    (agenthub_dir / "manifest.json").write_text(_json(manifest) + "\n", encoding="utf-8")


def _materialize_item(checkout_path: Path, item: dict[str, Any], parts: list[dict[str, Any]]) -> dict[str, Any]:
    item_dir_name = _item_key(item)
    item_dir = checkout_path / item_dir_name
    baseline_dir = checkout_path / ".agenthub" / "baseline" / item_dir_name
    item_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir.mkdir(parents=True, exist_ok=True)

    materialized_parts: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        raw_path = part.get("path") or part.get("name") or f"part-{index}.json"
        relative_part_path = _safe_relative_path(str(raw_path))
        text, encoding = _decode_payload({**part, "path": str(raw_path)})
        relative_file = relative_part_path
        if encoding == "base64":
            relative_file = Path(f"{relative_part_path}.base64")

        file_path = (item_dir / relative_file).resolve()
        baseline_path = (baseline_dir / relative_file).resolve()
        if item_dir not in file_path.parents or baseline_dir not in baseline_path.parents:
            raise ValueError(f"Definition path escaped checkout: {raw_path}")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")
        baseline_path.write_text(text, encoding="utf-8")

        materialized_parts.append({
            "path": str(raw_path),
            "file": str(Path(item_dir_name) / relative_file),
            "baselineFile": str(Path(".agenthub") / "baseline" / item_dir_name / relative_file),
            "encoding": encoding,
            "hash": _sha256_text(text),
            "contentType": part.get("contentType") or part.get("content_type"),
            "payloadType": part.get("payloadType") or part.get("payload_type"),
            "name": part.get("name"),
        })

    return {
        "itemId": item.get("id"),
        "displayName": item.get("displayName") or item.get("name"),
        "type": item.get("type"),
        "directory": item_dir_name,
        "parts": materialized_parts,
    }


async def _list_items(workspace_id: str) -> list[dict[str, Any]]:
    url = f"{FABRIC_API}/workspaces/{workspace_id}/items"
    items: list[dict[str, Any]] = []
    async with shared_client(30.0) as client:
        next_url: str | None = url
        while next_url:
            resp = await client.get(next_url, headers=_headers())
            if resp.status_code != 200:
                raise RuntimeError(format_http_error(resp, "listing workspace items"))
            body = resp.json()
            items.extend(body.get("value", []))
            if body.get("continuationUri"):
                next_url = body["continuationUri"]
            elif body.get("continuationToken"):
                next_url = f"{url}?continuationToken={body['continuationToken']}"
            else:
                next_url = None
    return items


async def _request_definition(workspace_id: str, item_id: str, item_type: str) -> list[dict[str, Any]]:
    endpoint = _endpoint_for(item_type)
    url = f"{FABRIC_API}/workspaces/{workspace_id}/{endpoint['collection']}/{item_id}/getDefinition"
    async with shared_client(60.0) as client:
        resp = await client.post(url, headers=_headers())
        if resp.status_code == 200:
            return resp.json().get("definition", {}).get("parts", [])
        if resp.status_code != 202:
            raise RuntimeError(format_http_error(resp, "getting definition"))

        location = resp.headers.get("Location")
        if not location:
            raise RuntimeError("Fabric returned 202 for getDefinition without a Location header")
        retry_after = int(resp.headers.get("Retry-After", str(DEFAULT_POLL_INTERVAL_SECONDS)))
        result = await _poll_operation(location, retry_after)
        return result.get("definition", {}).get("parts", [])


async def _poll_operation(location: str, retry_after: int = DEFAULT_POLL_INTERVAL_SECONDS) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + DEFAULT_MAX_POLL_SECONDS
    interval = max(retry_after, 1)
    async with shared_client(60.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(interval)
            resp = await client.get(location, headers=_headers())
            if resp.status_code == 200:
                body = resp.json() if resp.text else {}
                status = str(body.get("status", "")).lower()
                if status in {"succeeded", "completed"}:
                    result_url = location.rstrip("/") + "/result"
                    result_resp = await client.get(result_url, headers=_headers())
                    if result_resp.status_code == 200:
                        return result_resp.json() if result_resp.text else {}
                    return body
                if status == "failed":
                    raise RuntimeError(f"Fabric operation failed: {json.dumps(body)[:500]}")
            elif resp.status_code not in {202, 204}:
                raise RuntimeError(format_http_error(resp, "polling operation"))
            interval = min(int(interval * 1.5), 15)
    raise RuntimeError("Fabric operation timed out")


def _select_items(
    all_items: list[dict[str, Any]],
    item_ids: list[str] | None,
    item_names: list[str] | None,
    item_types: list[str] | None,
) -> list[dict[str, Any]]:
    id_set = set(item_ids or [])
    name_set = {name.lower() for name in item_names or []}
    type_set = {_item_type_key(item_type) for item_type in item_types or []}

    selected = []
    for item in all_items:
        item_id = item.get("id")
        item_name = str(item.get("displayName") or item.get("name") or "").lower()
        item_type = _item_type_key(str(item.get("type") or ""))
        if id_set and item_id in id_set:
            selected.append(item)
            continue
        if name_set and item_name in name_set:
            selected.append(item)
            continue
        if type_set and item_type in type_set:
            selected.append(item)

    if not id_set and not name_set and not type_set:
        raise ValueError("Provide item_ids, item_names, or item_types to checkout.")
    if len(selected) > MAX_CHECKOUT_ITEMS:
        raise ValueError(f"Checkout matched {len(selected)} items; narrow selection to {MAX_CHECKOUT_ITEMS} or fewer.")
    return selected


def _collect_file_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    checkout_path = _checkout_dir(manifest["checkoutId"])
    entries = []
    for item in manifest.get("items", []):
        for part in item.get("parts", []):
            file_path = checkout_path / part["file"]
            entries.append({
                "itemId": item["itemId"],
                "itemName": item["displayName"],
                "itemType": item["type"],
                "partPath": part["path"],
                "file": part["file"],
                "exists": file_path.exists(),
                "size": file_path.stat().st_size if file_path.exists() else 0,
                "changed": file_path.exists() and _sha256_text(file_path.read_text(encoding="utf-8")) != part["hash"],
            })
    return entries


def _diff_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    checkout_path = _checkout_dir(manifest["checkoutId"])
    changes: list[dict[str, Any]] = []
    tracked = set()

    for item in manifest.get("items", []):
        item_dir = checkout_path / item["directory"]
        for part in item.get("parts", []):
            relative_file = part["file"]
            tracked.add(relative_file)
            file_path = checkout_path / relative_file
            baseline_path = checkout_path / part["baselineFile"]
            if not file_path.exists():
                changes.append({"kind": "deleted", "file": relative_file, "itemId": item["itemId"], "partPath": part["path"]})
                continue
            current = file_path.read_text(encoding="utf-8")
            if _sha256_text(current) != part["hash"]:
                baseline = baseline_path.read_text(encoding="utf-8") if baseline_path.exists() else ""
                changes.append({
                    "kind": "modified",
                    "file": relative_file,
                    "itemId": item["itemId"],
                    "partPath": part["path"],
                    "beforeHash": part["hash"],
                    "afterHash": _sha256_text(current),
                    "beforeBytes": len(baseline.encode("utf-8")),
                    "afterBytes": len(current.encode("utf-8")),
                })

        if item_dir.exists():
            for path in item_dir.rglob("*"):
                if not path.is_file():
                    continue
                relative = str(path.relative_to(checkout_path))
                if relative not in tracked:
                    changes.append({
                        "kind": "added",
                        "file": relative,
                        "itemId": item["itemId"],
                        "partPath": str(path.relative_to(item_dir)),
                    })

    return {
        "checkoutId": manifest["checkoutId"],
        "changed": bool(changes),
        "changeCount": len(changes),
        "changes": changes,
    }


def _validate_json_file(path: Path) -> str | None:
    if not path.name.lower().endswith(".json"):
        return None
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"Invalid JSON in {path.name}: {exc.msg} at line {exc.lineno}"
    return None


def _validate_report_layout(item: dict[str, Any], checkout_path: Path) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    visuals_by_page: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for part in item.get("parts", []):
        if "/visuals/" not in part["path"].lower() or not part["path"].lower().endswith("/visual.json"):
            continue
        file_path = checkout_path / part["file"]
        if not file_path.exists():
            continue
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        page_key = part["path"].lower().split("/visuals/", 1)[0]
        visuals_by_page.setdefault(page_key, []).append((part["path"], payload.get("position") or {}))

    for page_key, visuals in visuals_by_page.items():
        positioned = []
        for path, position in visuals:
            try:
                x = float(position["x"])
                y = float(position["y"])
                width = float(position["width"])
                height = float(position["height"])
            except (KeyError, TypeError, ValueError):
                continue
            positioned.append((path, x, y, width, height))
        for index, left in enumerate(positioned):
            left_path, ax, ay, aw, ah = left
            for right_path, bx, by, bw, bh in positioned[index + 1:]:
                if ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by:
                    blockers.append({
                        "code": "visual_overlap",
                        "message": f"{left_path} overlaps {right_path} on {page_key}",
                    })
    return blockers


def _validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    checkout_path = _checkout_dir(manifest["checkoutId"])
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for item in manifest.get("items", []):
        for part in item.get("parts", []):
            file_path = (checkout_path / part["file"]).resolve()
            if checkout_path not in file_path.parents:
                blockers.append({"code": "path_escape", "message": part["file"]})
                continue
            if not file_path.exists():
                warnings.append({"code": "part_deleted", "message": part["file"]})
                continue
            error = _validate_json_file(file_path)
            if error:
                blockers.append({"code": "invalid_json", "message": error})

        if item.get("type") == "Report":
            blockers.extend(_validate_report_layout(item, checkout_path))
        if item.get("type") == "SemanticModel":
            has_model = any(part["path"].lower().endswith((".bim", ".tmdl")) for part in item.get("parts", []))
            if not has_model:
                warnings.append({"code": "semantic_model_definition_shape", "message": f"No BIM/TMDL files found for {item['displayName']}"})
        if item.get("type") == "Notebook":
            has_notebook = any(part["path"].lower().endswith((".ipynb", ".json")) for part in item.get("parts", []))
            if not has_notebook:
                warnings.append({"code": "notebook_definition_shape", "message": f"No notebook JSON part found for {item['displayName']}"})

    return {
        "checkoutId": manifest["checkoutId"],
        "valid": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def _parts_for_item(checkout_path: Path, item: dict[str, Any]) -> list[dict[str, Any]]:
    parts = []
    for part in item.get("parts", []):
        file_path = checkout_path / part["file"]
        if not file_path.exists():
            continue
        original = {
            "path": part["path"],
            "contentType": part.get("contentType"),
            "payloadType": part.get("payloadType"),
        }
        parts.append(_encode_payload(file_path, part["encoding"], original))
    return parts


async def _publish_update(workspace_id: str, item: dict[str, Any], parts: list[dict[str, Any]]) -> dict[str, Any]:
    endpoint = _endpoint_for(item["type"])
    url = f"{FABRIC_API}/workspaces/{workspace_id}/{endpoint['collection']}/{item['itemId']}/updateDefinition"
    body = {"definition": {"parts": parts}}
    async with shared_client(60.0) as client:
        resp = await client.post(
            url,
            json=body,
            headers=_headers(require_delegated=True, operation=f"publishing {item['type']} definition update"),
        )
        if resp.status_code in {200, 204}:
            return {"status": "succeeded", "statusCode": resp.status_code}
        if resp.status_code == 202:
            location = resp.headers.get("Location")
            if not location:
                return {"status": "accepted", "statusCode": 202}
            result = await _poll_operation(location, int(resp.headers.get("Retry-After", "5")))
            return {"status": "succeeded", "statusCode": 202, "operation": result}
        raise RuntimeError(format_http_error(resp, "updating definition"))


async def _publish_create(workspace_id: str, item: dict[str, Any], parts: list[dict[str, Any]], display_name: str) -> dict[str, Any]:
    body = {
        "displayName": display_name,
        "type": item["type"],
        "definition": {"parts": parts},
    }
    async with shared_client(60.0) as client:
        resp = await client.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/items",
            json=body,
            headers=_headers(require_delegated=True, operation=f"publishing new {item['type']} definition"),
        )
        if resp.status_code in {200, 201}:
            return {"status": "succeeded", "statusCode": resp.status_code, "item": resp.json() if resp.text else {}}
        if resp.status_code == 202:
            location = resp.headers.get("Location")
            if not location:
                return {"status": "accepted", "statusCode": 202}
            result = await _poll_operation(location, int(resp.headers.get("Retry-After", "5")))
            return {"status": "succeeded", "statusCode": 202, "operation": result}
        raise RuntimeError(format_http_error(resp, "creating item from definition"))


@mcp.tool()
async def fabric_definition_checkout(
    workspace_id: str,
    item_ids: list[str] | None = None,
    item_names: list[str] | None = None,
    item_types: list[str] | None = None,
) -> str:
    """Download Fabric item definitions into a local editable checkout.

    Select items by id, exact display name, type, or a combination. Supported
    definition item types currently include Report, SemanticModel, Notebook,
    DataPipeline, and SparkJobDefinition.
    """
    checkout_id = str(uuid.uuid4())
    checkout_path = _checkout_dir(checkout_id)
    checkout_path.mkdir(parents=True, exist_ok=False)

    try:
        all_items = await _list_items(workspace_id)
        selected = _select_items(all_items, item_ids, item_names, item_types)
        if not selected:
            raise ValueError("No matching Fabric items found for checkout.")

        manifest = {
            "checkoutId": checkout_id,
            "workspaceId": workspace_id,
            "createdAt": _now(),
            "root": str(checkout_path),
            "items": [],
        }

        for item in selected:
            parts = await _request_definition(workspace_id, item["id"], item["type"])
            manifest["items"].append(_materialize_item(checkout_path, item, parts))

        _write_manifest(checkout_path, manifest)
        return _json({
            "ok": True,
            "checkoutId": checkout_id,
            "root": str(checkout_path),
            "itemCount": len(manifest["items"]),
            "items": [{
                "itemId": item["itemId"],
                "displayName": item["displayName"],
                "type": item["type"],
                "directory": item["directory"],
                "partCount": len(item["parts"]),
            } for item in manifest["items"]],
        })
    except Exception:
        shutil.rmtree(checkout_path, ignore_errors=True)
        raise


@mcp.tool()
async def fabric_definition_list_files(checkout_id: str) -> str:
    """List files in a Fabric definition checkout."""
    manifest = _read_manifest(checkout_id)
    return _json({
        "ok": True,
        "checkoutId": checkout_id,
        "root": manifest["root"],
        "files": _collect_file_entries(manifest),
    })


@mcp.tool()
async def fabric_definition_diff(checkout_id: str) -> str:
    """Return changed, added, and deleted files for a checkout."""
    manifest = _read_manifest(checkout_id)
    return _json({"ok": True, **_diff_manifest(manifest)})


@mcp.tool()
async def fabric_definition_validate(checkout_id: str) -> str:
    """Validate a checkout before publishing definition changes."""
    manifest = _read_manifest(checkout_id)
    return _json({"ok": True, **_validate_manifest(manifest), "diff": _diff_manifest(manifest)})


@mcp.tool()
async def fabric_definition_plan_publish(
    checkout_id: str,
    mode: str = "update",
    target_display_name: str | None = None,
) -> str:
    """Build a publish plan without writing to Fabric."""
    if mode not in {"update", "create"}:
        raise ValueError("mode must be 'update' or 'create'")
    manifest = _read_manifest(checkout_id)
    validation = _validate_manifest(manifest)
    diff = _diff_manifest(manifest)
    operations = []
    for item in manifest.get("items", []):
        display_name = target_display_name or f"{item['displayName']} Copy"
        operations.append({
            "mode": mode,
            "itemId": item["itemId"] if mode == "update" else None,
            "sourceItemId": item["itemId"],
            "displayName": item["displayName"] if mode == "update" else display_name,
            "type": item["type"],
            "changedPartCount": sum(1 for change in diff["changes"] if change.get("itemId") == item["itemId"]),
            "partCount": len(_parts_for_item(_checkout_dir(checkout_id), item)),
        })

    return _json({
        "ok": True,
        "checkoutId": checkout_id,
        "mode": mode,
        "ready": validation["valid"],
        "validation": validation,
        "diff": diff,
        "operations": operations,
        "nextAction": "fabric_definition_publish" if validation["valid"] else "Fix validation blockers before publishing",
    })


@mcp.tool()
async def fabric_definition_publish(
    checkout_id: str,
    mode: str = "update",
    target_display_name: str | None = None,
) -> str:
    """Publish validated checkout changes back to Fabric.

    Use ``mode='update'`` to update existing items or ``mode='create'`` to
    create copies from the checkout definition. Run
    ``fabric_definition_plan_publish`` first to preview the operations.
    """
    if mode not in {"update", "create"}:
        raise ValueError("mode must be 'update' or 'create'")

    manifest = _read_manifest(checkout_id)
    validation = _validate_manifest(manifest)
    if not validation["valid"]:
        return _json({
            "ok": False,
            "checkoutId": checkout_id,
            "error": "Validation blockers must be resolved before publishing.",
            "validation": validation,
        })

    checkout_path = _checkout_dir(checkout_id)
    results = []
    for item in manifest.get("items", []):
        parts = _parts_for_item(checkout_path, item)
        if mode == "update":
            result = await _publish_update(manifest["workspaceId"], item, parts)
        else:
            if len(manifest.get("items", [])) > 1 and target_display_name:
                display_name = f"{target_display_name} - {item['displayName']}"
            else:
                display_name = target_display_name or f"{item['displayName']} Copy"
            result = await _publish_create(manifest["workspaceId"], item, parts, display_name)
        results.append({"itemId": item["itemId"], "displayName": item["displayName"], "type": item["type"], "result": result})

    manifest["lastPublishedAt"] = _now()
    _write_manifest(checkout_path, manifest)
    return _json({"ok": True, "checkoutId": checkout_id, "mode": mode, "results": results})


@mcp.tool()
async def fabric_definition_discard_checkout(checkout_id: str) -> str:
    """Delete a local Fabric definition checkout."""
    path = _checkout_dir(checkout_id)
    if path.exists():
        shutil.rmtree(path)
    return _json({"ok": True, "checkoutId": checkout_id, "discarded": True})


if __name__ == "__main__":
    mcp.run()
