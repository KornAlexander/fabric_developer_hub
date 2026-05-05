"""SLL Sidecar — exposes Michael Kovalsky's run_model_bpa and
vertipaq_analyzer over a tiny HTTP API so the AgentHub backend can
proxy the *literal* output to the PBI Fixer UI without rebuilding
either rule engine in TypeScript.

Endpoints
─────────
GET  /health                         → {"ok": true, "configured": bool}
POST /sll/model-bpa                  body {workspace, dataset}
                                     → {"rows": [...], "columns": [...]}
POST /sll/vertipaq                   body {workspace, dataset, read_stats?: bool}
                                     → {"html": "<...>"}

Auth
────
Requires a Service Principal with workspace access. SLL's
`evaluate_dax` / `connect_semantic_model` paths bind the credential
process-wide via ``set_service_principal`` (sempy.fabric); we cannot
inject an end-user OBO token into pyadomd's .NET stack from outside.

Set in .env:
  SLL_TENANT_ID=<tenant guid>
  SLL_CLIENT_ID=<sp app id>
  SLL_CLIENT_SECRET=<sp secret>
"""

from __future__ import annotations

import logging
import os
import sys
import types
from contextlib import contextmanager
from typing import Any

# ─────────────────────────────────────────────────────────────────────
#  Stub ``notebookutils`` BEFORE importing sempy_labs. SLL's
#  ``_base_api`` does ``import notebookutils`` at call time even when
#  the credential is supplied via ``set_service_principal``. We only
#  need the import to succeed; the real auth path uses our SP token
#  provider. ``notebookutils.credentials.getToken`` is only hit for
#  storage / kusto / blob / keyvault clients we don't use.
# ─────────────────────────────────────────────────────────────────────
if "notebookutils" not in sys.modules:
    _nbu = types.ModuleType("notebookutils")
    _nbu_creds = types.ModuleType("notebookutils.credentials")

    def _stub_get_token(audience: str) -> str:  # noqa: ARG001
        raise RuntimeError(
            "notebookutils.credentials.getToken is not available in the "
            "SLL sidecar; this code path requires a Fabric runtime."
        )

    _nbu_creds.getToken = _stub_get_token  # type: ignore[attr-defined]
    _nbu.credentials = _nbu_creds  # type: ignore[attr-defined]
    sys.modules["notebookutils"] = _nbu
    sys.modules["notebookutils.credentials"] = _nbu_creds

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sll-sidecar")

app = FastAPI(title="SLL Sidecar", version="0.77.0")


# ─────────────────────────────────────────────────────────────────────
#  Monkey patch: sempy_labs.run_model_bpa / run_vertipaq_analyzer
#  call connect_semantic_model with the workspace GUID. The Power BI
#  Analysis Services endpoint refuses GUID-style URLs for Service
#  Principals — only workspace **names** resolve. Translate workspace
#  GUID → workspace name in TOMWrapper.__init__ so the URL build uses
#  the name. Verified via /debug/xmla.
# ─────────────────────────────────────────────────────────────────────

def _install_workspace_name_patch() -> None:
    try:
        import sempy_labs.tom._model as _tom_mod  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log.warning("workspace-name patch skipped (sempy_labs not importable): %s", exc)
        return

    if getattr(_tom_mod.TOMWrapper, "_sll_workspace_name_patch_applied", False):
        return

    _orig_init = _tom_mod.TOMWrapper.__init__

    def _patched_init(self, dataset, workspace, readonly):  # type: ignore[no-untyped-def]
        try:
            if (
                isinstance(workspace, str)
                and not workspace.startswith("asazure://")
            ):
                # Always resolve to the workspace name. resolve_*_name_and_id
                # accepts either name or GUID and returns (name, guid).
                from sempy_labs._helper_functions import (  # type: ignore
                    resolve_workspace_name_and_id,
                )

                ws_name, _ws_id = resolve_workspace_name_and_id(workspace)
                if ws_name and ws_name != workspace:
                    log.info(
                        "workspace-name patch: %s → %s", workspace, ws_name,
                    )
                workspace = ws_name
        except Exception as exc:  # noqa: BLE001
            log.warning("workspace-name patch lookup failed: %s", exc)
        return _orig_init(self, dataset, workspace, readonly)

    _tom_mod.TOMWrapper.__init__ = _patched_init  # type: ignore[assignment]
    _tom_mod.TOMWrapper._sll_workspace_name_patch_applied = True  # type: ignore[attr-defined]
    log.info("workspace-name patch installed on sempy_labs.tom._model.TOMWrapper")


_install_workspace_name_patch()


# ─────────────────────────────────────────────────────────────────────
#  Bearer-token gate (set SIDECAR_AUTH_TOKEN env to enable)
# ─────────────────────────────────────────────────────────────────────

@app.middleware("http")
async def _bearer_gate(request: Request, call_next):
    expected = os.environ.get("SIDECAR_AUTH_TOKEN", "").strip()
    # /health is always reachable so orchestrators can liveness-probe it.
    if expected and request.url.path != "/health":
        provided = request.headers.get("X-Sidecar-Token", "").strip()
        if provided != expected:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing X-Sidecar-Token"},
            )
    return await call_next(request)


# ─────────────────────────────────────────────────────────────────────
#  Service Principal binding
# ─────────────────────────────────────────────────────────────────────

def _sp_env() -> tuple[str, str, str] | None:
    t = os.environ.get("SLL_TENANT_ID", "").strip()
    c = os.environ.get("SLL_CLIENT_ID", "").strip()
    s = os.environ.get("SLL_CLIENT_SECRET", "").strip()
    if not (t and c and s):
        return None
    return t, c, s


@contextmanager
def _sp_context():
    """Bind sempy.fabric to the configured Service Principal for the
    duration of the call. Raises 503 if SP env vars are missing."""
    creds = _sp_env()
    if creds is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "SLL sidecar is not configured. Set SLL_TENANT_ID, "
                "SLL_CLIENT_ID and SLL_CLIENT_SECRET in the AgentHub "
                ".env file (the SP must have access to the target "
                "Fabric workspace)."
            ),
        )
    tenant_id, client_id, client_secret = creds
    from sempy.fabric import set_service_principal
    from sempy_labs._authentication import (
        ServicePrincipalTokenProvider,
        token_provider,
    )

    sp_provider = ServicePrincipalTokenProvider.from_aad_application_key_authentication(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    prior = token_provider.get()
    token_provider.set(sp_provider)
    try:
        with set_service_principal(tenant_id, client_id, client_secret=client_secret):
            yield
    finally:
        token_provider.set(prior)


# ─────────────────────────────────────────────────────────────────────
#  Output capture — sempy_labs.vertipaq_analyzer renders via
#  IPython.display(HTML(...)) and returns None. Patch display() to
#  collect the HTML payloads so we can ship them back to the UI.
# ─────────────────────────────────────────────────────────────────────

@contextmanager
def _capture_html() -> Any:
    """Yield a list that will be populated with HTML strings produced
    by every IPython ``display(HTML(...))`` call inside the block."""
    captured: list[str] = []
    import IPython.display as ipd

    original_display = ipd.display

    def patched(*objs: Any, **_: Any) -> None:
        for obj in objs:
            html = None
            if isinstance(obj, ipd.HTML):
                html = obj.data
            elif hasattr(obj, "_repr_html_"):
                try:
                    html = obj._repr_html_()
                except Exception:  # noqa: BLE001 — best-effort capture
                    html = None
            if html:
                captured.append(html)

    ipd.display = patched  # type: ignore[assignment]
    # _model_bpa / _vertipaq_analyzer also import display by name; patch
    # the live module attribute on the SLL submodules so their
    # already-bound references get the proxy too.
    import sempy_labs._model_bpa as _bpa_mod
    import sempy_labs.semantic_model._vertipaq_analyzer as _vp_mod

    bpa_orig = getattr(_bpa_mod, "display", None)
    vp_orig = getattr(_vp_mod, "display", None)
    _bpa_mod.display = patched  # type: ignore[assignment]
    _vp_mod.display = patched  # type: ignore[assignment]

    try:
        yield captured
    finally:
        ipd.display = original_display
        if bpa_orig is not None:
            _bpa_mod.display = bpa_orig  # type: ignore[assignment]
        if vp_orig is not None:
            _vp_mod.display = vp_orig  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────

class _ModelRequest(BaseModel):
    workspace: str
    dataset: str


class _VertipaqRequest(_ModelRequest):
    read_stats_from_data: bool = False


@app.get("/health")
def health() -> dict[str, Any]:
    import platform

    machine = platform.machine().lower()
    # sempy bundles x64 .NET assemblies for AnalysisServices/XmlaTools.
    # On non-x86_64 hosts the DLLs cannot be loaded and pythonnet/QEMU
    # crashes the process when emulated, so we surface this clearly.
    arch_supported = machine in ("x86_64", "amd64")
    return {
        "ok": True,
        "configured": _sp_env() is not None,
        "arch": machine,
        "arch_supported": arch_supported,
    }


@app.post("/debug/xmla")
def debug_xmla(payload: _ModelRequest) -> dict[str, Any]:
    """Diagnose XMLA connectivity directly via .NET, bypassing sempy_labs.
    Tries multiple auth & connection-string variants and returns each
    outcome separately."""
    import msal  # type: ignore[import-not-found]
    import requests as _requests
    from sempy.fabric._client._utils import _init_analysis_services

    creds = _sp_env()
    if creds is None:
        raise HTTPException(503, "SP env not configured")
    tenant, client, secret = creds

    _init_analysis_services()
    import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore
    import Microsoft.AnalysisServices as MAS  # type: ignore
    from System import DateTimeOffset  # type: ignore

    # Acquire SP token for Power BI / Fabric XMLA scope.
    pbi_app = msal.ConfidentialClientApplication(
        client_id=client,
        client_credential=secret,
        authority=f"https://login.microsoftonline.com/{tenant}",
    )
    tok_resp = pbi_app.acquire_token_for_client(
        scopes=["https://analysis.windows.net/powerbi/api/.default"]
    )
    if "access_token" not in tok_resp:
        raise HTTPException(502, f"token acquisition failed: {tok_resp}")
    pbi_token = tok_resp["access_token"]

    # Workspace GUID lookup via REST (avoids sempy).
    rest_h = {"Authorization": f"Bearer {pbi_token}"}
    ws_in = payload.workspace
    ws_guid = ws_in
    ws_name = ws_in
    try:
        r = _requests.get(
            "https://api.powerbi.com/v1.0/myorg/groups",
            headers=rest_h, timeout=30,
        )
        groups = r.json().get("value", [])
        for g in groups:
            if g.get("id") == ws_in or g.get("name") == ws_in:
                ws_guid = g["id"]
                ws_name = g["name"]
                break
    except Exception as exc:  # noqa: BLE001
        return {"error": f"workspace lookup failed: {exc}"}

    ds = payload.dataset

    def _attempt_token(label: str, conn_str: str) -> dict[str, Any]:
        try:
            srv = TOM.Server()
            seconds = 3600
            exp = DateTimeOffset.UtcNow.AddSeconds(seconds)
            srv.AccessToken = MAS.AccessToken(pbi_token, exp)
            srv.Connect(conn_str)
            dbs = [str(db.Name) for db in srv.Databases]
            try:
                srv.Disconnect()
            except Exception:
                pass
            return {"ok": True, "server": str(srv.Name), "databases": dbs[:20]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:1800]}

    def _attempt_inline_sp(label: str, conn_str: str) -> dict[str, Any]:
        try:
            srv = TOM.Server()
            srv.Connect(conn_str)
            dbs = [str(db.Name) for db in srv.Databases]
            try:
                srv.Disconnect()
            except Exception:
                pass
            return {"ok": True, "server": str(srv.Name), "databases": dbs[:20]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:1800]}

    base_pbi = "powerbi://api.powerbi.com/v1.0/myorg"
    inline_creds = f"User ID=app:{client}@{tenant};Password=***REDACTED***;"
    inline_creds_real = f"User ID=app:{client}@{tenant};Password={secret};"

    return {
        "workspace_input": ws_in,
        "workspace_resolved_name": ws_name,
        "workspace_resolved_guid": ws_guid,
        "dataset": ds,
        "token_len": len(pbi_token),
        "results": {
            "tok_by_name_with_catalog": _attempt_token(
                "tok_by_name_with_catalog",
                f"Data Source={base_pbi}/{ws_name};Initial Catalog={ds};",
            ),
            "tok_by_guid_with_catalog": _attempt_token(
                "tok_by_guid_with_catalog",
                f"Data Source={base_pbi}/{ws_guid};Initial Catalog={ds};",
            ),
            "tok_by_name_no_catalog": _attempt_token(
                "tok_by_name_no_catalog",
                f"Data Source={base_pbi}/{ws_name};",
            ),
            "tok_by_guid_no_catalog": _attempt_token(
                "tok_by_guid_no_catalog",
                f"Data Source={base_pbi}/{ws_guid};",
            ),
            "inline_sp_by_name": {
                "conn_str_redacted": (
                    f"Data Source={base_pbi}/{ws_name};Initial Catalog={ds};{inline_creds}"
                ),
                **_attempt_inline_sp(
                    "inline_sp_by_name",
                    f"Data Source={base_pbi}/{ws_name};Initial Catalog={ds};{inline_creds_real}",
                ),
            },
        },
    }




def _require_supported_arch() -> None:
    import platform

    machine = platform.machine().lower()
    if machine not in ("x86_64", "amd64"):
        raise HTTPException(
            status_code=501,
            detail=(
                f"SLL sidecar host arch '{machine}' is not supported. "
                "sempy_labs requires x86_64 because Microsoft.AnalysisServices.* "
                "DLLs are x64-only. Deploy this container to an x86_64 host "
                "(Linux server, AKS amd64 node, GitHub Actions ubuntu-latest)."
            ),
        )


@app.post("/sll/model-bpa")
def model_bpa(req: _ModelRequest) -> dict[str, Any]:
    """Run sempy_labs.run_model_bpa(return_dataframe=True) and return
    the DataFrame as JSON-serialisable rows + column metadata. This
    matches how PBI Fixer (Alex's notebook) consumes the function."""
    log.info("run_model_bpa: workspace=%s dataset=%s", req.workspace, req.dataset)
    _require_supported_arch()
    with _sp_context():
        from sempy_labs import run_model_bpa

        try:
            df = run_model_bpa(
                dataset=req.dataset,
                workspace=req.workspace,
                return_dataframe=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("run_model_bpa failed")
            raise HTTPException(status_code=502, detail=f"run_model_bpa failed: {exc}") from exc

    if df is None or not isinstance(df, pd.DataFrame):
        return {"columns": [], "rows": []}
    columns = list(map(str, df.columns))
    # pandas → records with stringified scalars (HTML viewer is happy with strings)
    rows = df.astype(object).where(df.notna(), None).to_dict(orient="records")
    return {"columns": columns, "rows": rows}


@app.post("/sll/vertipaq")
def vertipaq(req: _VertipaqRequest) -> dict[str, Any]:
    """Run sempy_labs.vertipaq_analyzer() and return the captured HTML
    blocks (one per section: model summary, tables, columns,
    relationships, …) so the UI can render Michael's exact output."""
    log.info(
        "vertipaq_analyzer: workspace=%s dataset=%s read_stats=%s",
        req.workspace,
        req.dataset,
        req.read_stats_from_data,
    )
    _require_supported_arch()
    with _sp_context(), _capture_html() as html_blocks:
        from sempy_labs import vertipaq_analyzer

        try:
            vertipaq_analyzer(
                dataset=req.dataset,
                workspace=req.workspace,
                read_stats_from_data=req.read_stats_from_data,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("vertipaq_analyzer failed")
            raise HTTPException(status_code=502, detail=f"vertipaq_analyzer failed: {exc}") from exc

    return {"html": "\n<hr/>\n".join(html_blocks)}
