"""Delete every item and folder from a Microsoft Fabric workspace.

Order of operations:
    1. List folders so item discovery can query every folder explicitly.
    2. List root and folder-contained items -> hard DELETE each item.
    3. List recoverable items from the workspace recycle bin and purge them.
    4. Delete folders deepest-first so a parent never disappears
     before its children.

Authentication (first match wins):
  --token <bearer>
  $FABRIC_API_TOKEN
  `az account get-access-token --resource https://api.fabric.microsoft.com`

Usage:
  python clear_workspace.py <workspace-id> [--dry-run] [--yes]
  python clear_workspace.py <workspace-id> --token "$(cat .pb_token)"

The script is intentionally destructive: every supported item type,
recoverable item, and folder in the workspace will be removed. Pass
`--dry-run` first if you want to see what would be deleted.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Iterator
from urllib.parse import urlencode

import requests

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
REQUEST_TIMEOUT = 60
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def resolve_token(cli_token: str | None) -> str:
    if cli_token:
        return cli_token.strip()

    env_token = os.environ.get("FABRIC_API_TOKEN", "").strip()
    if env_token:
        return env_token

    if not shutil.which("az"):
        raise SystemExit(
            "No token provided and `az` CLI not found. Pass --token or set "
            "FABRIC_API_TOKEN."
        )

    try:
        result = subprocess.run(
            [
                "az", "account", "get-access-token",
                "--resource", "https://api.fabric.microsoft.com",
                "--query", "accessToken",
                "-o", "tsv",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"`az account get-access-token` failed: {exc.stderr.strip() or exc}"
        ) from exc

    token = result.stdout.strip()
    if not token:
        raise SystemExit("`az account get-access-token` returned an empty token.")
    return token


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _request_with_retries(
    method: str,
    url: str,
    token: str,
    *,
    max_attempts: int = 4,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.request(
                method, url, headers=_headers(token), timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == max_attempts:
                raise
            time.sleep(min(2 ** attempt, 10))
            continue

        if response.status_code in RETRYABLE_STATUS and attempt < max_attempts:
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 10)
            time.sleep(delay)
            continue
        return response

    raise RuntimeError(f"Request to {url} failed: {last_exc}")


def _paginate(url: str, token: str, key: str) -> Iterator[dict]:
    next_url: str | None = url
    while next_url:
        response = _request_with_retries("GET", next_url, token)
        if not response.ok:
            raise RuntimeError(
                f"GET {next_url} -> {response.status_code}: {response.text[:500]}"
            )
        body = response.json() if response.text else {}
        yield from body.get(key, [])

        token_value = body.get("continuationToken")
        cont_uri = body.get("continuationUri")
        if cont_uri:
            next_url = cont_uri
        elif token_value:
            sep = "&" if "?" in url else "?"
            next_url = f"{url}{sep}continuationToken={token_value}"
        else:
            next_url = None


# ---------------------------------------------------------------------------
# Fabric operations
# ---------------------------------------------------------------------------
def list_items(
    workspace_id: str,
    token: str,
    *,
    root_folder_id: str | None = None,
) -> list[dict]:
    params = {"recursive": "true"}
    if root_folder_id:
        params["rootFolderId"] = root_folder_id
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items?{urlencode(params)}"
    return list(_paginate(url, token, "value"))


def list_all_items(workspace_id: str, folders: list[dict], token: str) -> list[dict]:
    """List root and folder-contained items, deduplicated by item id.

    Fabric's workspace-level ``/items`` response can lag or omit folder
    contents while the folder API still rejects deletion as non-empty.
    Querying each folder as a root makes cleanup deterministic.
    """
    by_id: dict[str, dict] = {}
    locations: list[tuple[str, str | None]] = [("workspace root", None)]
    locations.extend(
        (
            folder.get("displayName") or folder.get("id") or "folder",
            folder.get("id"),
        )
        for folder in folders
        if folder.get("id")
    )

    for label, root_folder_id in locations:
        found = list_items(workspace_id, token, root_folder_id=root_folder_id)
        print(f"  {label}: found {len(found)} items")
        for item in found:
            item_id = item.get("id")
            if item_id:
                by_id.setdefault(item_id, item)

    return list(by_id.values())


def list_folders(workspace_id: str, token: str) -> list[dict]:
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders"
    return list(_paginate(url, token, "value"))


def list_recoverable_items(workspace_id: str, token: str) -> list[dict]:
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/recoverableItems"
    return list(_paginate(url, token, "value"))


def delete_item(
    workspace_id: str,
    item_id: str,
    token: str,
    *,
    hard_delete: bool,
) -> requests.Response:
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{item_id}"
    if hard_delete:
        url = f"{url}?hardDelete=true"
    return _request_with_retries("DELETE", url, token)


def delete_recoverable_item(
    workspace_id: str,
    item_id: str,
    token: str,
) -> requests.Response:
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/recoverableItems/{item_id}"
    return _request_with_retries("DELETE", url, token)


def delete_folder(workspace_id: str, folder_id: str, token: str) -> requests.Response:
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders/{folder_id}"
    return _request_with_retries("DELETE", url, token)


# ---------------------------------------------------------------------------
# Folder ordering (deepest-first)
# ---------------------------------------------------------------------------
def sort_folders_deepest_first(folders: list[dict]) -> list[dict]:
    """Sort folders so children precede their parents.

    Fabric returns folders flat with a ``parentFolderId`` link. Computing
    each folder's depth from the root and sorting descending guarantees a
    safe deletion order even when ``parentFolderId`` cycles or dangles.
    """
    by_id = {f["id"]: f for f in folders}

    def depth(folder: dict, _seen: set[str] | None = None) -> int:
        seen = _seen or set()
        parent_id = folder.get("parentFolderId")
        if not parent_id or parent_id in seen or parent_id not in by_id:
            return 0
        seen.add(folder["id"])
        return 1 + depth(by_id[parent_id], seen)

    return sorted(folders, key=depth, reverse=True)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _error_code(response: requests.Response) -> str | None:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return None
    return body.get("errorCode") if isinstance(body, dict) else None


def _delete_all(
    label: str,
    entries: list[dict],
    name_key: str,
    deleter,
    workspace_id: str,
    token: str,
    dry_run: bool,
    *,
    deleter_kwargs: dict | None = None,
    retry_error_codes: set[str] | None = None,
    retry_attempts: int = 0,
    retry_delay: float = 0.0,
) -> tuple[int, int]:
    succeeded = 0
    failed = 0
    for index, entry in enumerate(entries, start=1):
        entry_id = entry["id"]
        display = entry.get(name_key) or entry.get("displayName") or entry_id
        prefix = f"  [{index}/{len(entries)}] {label} {display!r} ({entry_id})"

        if dry_run:
            print(f"{prefix} -> dry-run, skipping")
            succeeded += 1
            continue

        for attempt in range(retry_attempts + 1):
            response = deleter(workspace_id, entry_id, token, **(deleter_kwargs or {}))
            if response.status_code in (200, 202, 204):
                print(f"{prefix} -> {response.status_code} OK")
                succeeded += 1
                break
            if response.status_code == 404:
                print(f"{prefix} -> 404 (already gone)")
                succeeded += 1
                break

            error_code = _error_code(response)
            can_retry = (
                error_code in (retry_error_codes or set())
                and attempt < retry_attempts
            )
            if can_retry:
                print(
                    f"{prefix} -> {response.status_code} {error_code}; "
                    f"retrying in {retry_delay:g}s ({attempt + 1}/{retry_attempts})"
                )
                time.sleep(retry_delay)
                continue

            print(
                f"{prefix} -> {response.status_code} FAILED: "
                f"{response.text[:300]}"
            )
            failed += 1
            break
    return succeeded, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete all items and folders from a Fabric workspace.",
    )
    parser.add_argument("workspace_id", help="Fabric workspace GUID.")
    parser.add_argument(
        "--token",
        help="Bearer token for api.fabric.microsoft.com. "
        "Falls back to $FABRIC_API_TOKEN, then `az account get-access-token`.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be deleted without making any DELETE calls.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    parser.add_argument(
        "--soft-delete-items",
        action="store_true",
        help="Soft-delete active items instead of permanently deleting them. "
        "This can leave folders non-empty until recycle-bin items are purged.",
    )
    parser.add_argument(
        "--skip-recoverable-items",
        action="store_true",
        help="Do not permanently delete items already in the workspace recycle bin. "
        "Folders containing recoverable items cannot be deleted.",
    )
    parser.add_argument(
        "--folder-retries",
        type=int,
        default=0,
        help="Retry folder deletes that Fabric reports as FolderNotEmpty.",
    )
    parser.add_argument(
        "--folder-retry-delay",
        type=float,
        default=10.0,
        help="Seconds to wait between FolderNotEmpty retry attempts.",
    )
    args = parser.parse_args(argv)

    token = resolve_token(args.token)

    print(f"Workspace: {args.workspace_id}")
    print("Fetching folders...")
    folders = list_folders(args.workspace_id, token)
    print(f"  found {len(folders)} folders")

    print("Fetching items...")
    items = list_all_items(args.workspace_id, folders, token)
    print(f"  found {len(items)} unique items")

    recoverable_items: list[dict] = []
    if args.skip_recoverable_items:
        print("Skipping recoverable items lookup.")
    else:
        print("Fetching recoverable items...")
        recoverable_items = list_recoverable_items(args.workspace_id, token)
        print(f"  found {len(recoverable_items)} recoverable items")

    if not items and not recoverable_items and not folders:
        print("Nothing to delete.")
        return 0

    if not args.dry_run and not args.yes:
        if not confirm(
            f"About to delete {len(items)} active items, "
            f"{len(recoverable_items)} recoverable items, and {len(folders)} folders "
            f"from workspace {args.workspace_id}. Continue?"
        ):
            print("Aborted.")
            return 1

    item_ok, item_fail = _delete_all(
        "item", items, "displayName", delete_item,
        args.workspace_id, token, args.dry_run,
        deleter_kwargs={"hard_delete": not args.soft_delete_items},
    )

    recoverable_ok, recoverable_fail = _delete_all(
        "recoverable item", recoverable_items, "displayName", delete_recoverable_item,
        args.workspace_id, token, args.dry_run,
    )

    ordered_folders = sort_folders_deepest_first(folders)
    folder_ok, folder_fail = _delete_all(
        "folder", ordered_folders, "displayName", delete_folder,
        args.workspace_id, token, args.dry_run,
        retry_error_codes={"FolderNotEmpty"},
        retry_attempts=max(args.folder_retries, 0),
        retry_delay=max(args.folder_retry_delay, 0.0),
    )

    print()
    print("Summary:")
    print(f"  items             : {item_ok} ok, {item_fail} failed")
    print(f"  recoverable items : {recoverable_ok} ok, {recoverable_fail} failed")
    print(f"  folders           : {folder_ok} ok, {folder_fail} failed")

    if item_fail or recoverable_fail or folder_fail:
        print(
            "\nSome deletions failed. If folders still return FolderNotEmpty, check\n"
            "the workspace recycle bin: Fabric does not allow deleting folders\n"
            "that contain recoverable items until those items are permanently deleted."
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - top-level guard
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
