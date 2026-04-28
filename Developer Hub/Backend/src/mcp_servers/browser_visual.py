"""Browser visual verification MCP server.

The tool here is intentionally single-shot. ``MCPClientManager`` starts a
fresh MCP process for every tool call, so exposing separate browser actions
like navigate/screenshot would lose state between calls. This server performs
navigation, waiting, screenshot capture, and lightweight visual analysis in one
read-only call.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("browser-visual", log_level="WARNING")

_MAX_TIMEOUT_SECONDS = int(os.environ.get("BROWSER_VISUAL_MAX_TIMEOUT_SECONDS", "90"))
_MAX_TEXT_CHARS = 4_000
_DEFAULT_EVIDENCE_DIR = Path(os.environ.get("BROWSER_VISUAL_EVIDENCE_DIR", "/tmp/agenthub-visual-evidence"))
_DEFAULT_STORAGE_STATE = os.environ.get("BROWSER_VISUAL_AUTH_STATE_PATH") or os.environ.get("PLAYWRIGHT_STORAGE_STATE_PATH")
_DEFAULT_ALLOWED_HOSTS = {
    "app.fabric.microsoft.com",
    "app.powerbi.com",
    "powerbi.com",
    "fabric.microsoft.com",
}
_HOST_SUFFIX_ALLOWLIST = (
    ".powerbi.com",
    ".fabric.microsoft.com",
)

_LOGIN_PATTERNS = (
    "login.microsoftonline.com",
    "signin",
    "sign in",
    "pick an account",
    "enter password",
    "stay signed in",
)
_RENDER_ERROR_PATTERNS = (
    "couldn't load",
    "could not load",
    "something went wrong",
    "unable to render",
    "can't display",
    "content isn't available",
    "report isn't available",
    "visual has exceeded",
    "query has exceeded",
    "error loading",
)


class BrowserVisualError(ValueError):
    """Raised when a browser visual verification request is unsafe."""


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _bounded_timeout(value: int | None) -> int:
    try:
        requested = int(value or 45)
    except (TypeError, ValueError):
        requested = 45
    return max(5, min(requested, _MAX_TIMEOUT_SECONDS))


def _allowed_hosts() -> set[str]:
    configured = os.environ.get("BROWSER_VISUAL_ALLOWED_HOSTS", "")
    hosts = {h.strip().lower() for h in configured.split(",") if h.strip()}
    return _DEFAULT_ALLOWED_HOSTS | hosts


def _normalize_url(url: str) -> str:
    value = (url or "").strip()
    if len(value) > 2_000:
        raise BrowserVisualError("url is too long")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise BrowserVisualError("only https URLs are allowed")
    if not parsed.hostname:
        raise BrowserVisualError("url must include a hostname")
    if parsed.username or parsed.password:
        raise BrowserVisualError("embedded credentials in URLs are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host not in _allowed_hosts() and not any(host.endswith(suffix) for suffix in _HOST_SUFFIX_ALLOWLIST):
        raise BrowserVisualError(
            "host is not allowed for Fabric/Power BI visual verification"
        )
    port = parsed.port
    if port is not None and port not in {443, 8443}:
        raise BrowserVisualError("only standard HTTPS ports are allowed")
    return value


def _safe_screenshot_name(name: str | None, url: str) -> str:
    stem = name or urlparse(url).path.rsplit("/", 1)[-1] or "visual-evidence"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")[:80]
    if not stem:
        stem = "visual-evidence"
    if not stem.lower().endswith(".png"):
        stem += ".png"
    return stem


def _screenshot_path(name: str | None, url: str) -> Path:
    _DEFAULT_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    base = _safe_screenshot_name(name, url)
    candidate = _DEFAULT_EVIDENCE_DIR / base
    if not candidate.exists():
        return candidate
    suffix = 1
    stem = candidate.stem
    while True:
        numbered = _DEFAULT_EVIDENCE_DIR / f"{stem}-{suffix}.png"
        if not numbered.exists():
            return numbered
        suffix += 1


def _node_script() -> str:
    return r'''
const fs = require('fs');

function loadPlaywright() {
  try {
    return require('playwright-core');
  } catch (firstError) {
    try {
      return require('playwright');
    } catch (secondError) {
      return { __loadError: `${firstError.message}; ${secondError.message}` };
    }
  }
}

async function bodyText(page) {
  try {
    return await page.locator('body').innerText({ timeout: 1500 });
  } catch (_) {
    return '';
  }
}

async function visualSignals(page) {
  return await page.evaluate(() => {
    const rectOf = (el) => {
      const rect = el.getBoundingClientRect();
      return {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    };
    const isVisible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 2 && rect.height > 2 && style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || '1') > 0.05;
    };
    const candidates = Array.from(document.querySelectorAll('canvas,svg,iframe,[role="img"],[role="figure"],[aria-label],[data-testid],[data-automation-id],.visualContainer,.visual-container'));
    const elements = candidates.slice(0, 120).map((el) => {
      const style = window.getComputedStyle(el);
      return {
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 160),
        dataTestId: (el.getAttribute('data-testid') || '').slice(0, 120),
        visible: isVisible(el),
        rect: rectOf(el),
        color: style.color,
        backgroundColor: style.backgroundColor,
        fontSize: style.fontSize,
      };
    });
    const colorSamples = Array.from(document.querySelectorAll('body, main, section, div, svg, canvas'))
      .slice(0, 500)
      .map((el) => window.getComputedStyle(el).backgroundColor)
      .filter((value) => value && value !== 'rgba(0, 0, 0, 0)' && value !== 'transparent');
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      bodyRect: rectOf(document.body),
      elementCount: candidates.length,
      visibleElementCount: elements.filter((el) => el.visible).length,
      elements,
      colorSamples: Array.from(new Set(colorSamples)).slice(0, 24),
    };
  });
}

(async () => {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'));
  const playwright = loadPlaywright();
  if (playwright.__loadError) {
    console.log(JSON.stringify({ ok: false, errorCode: 'PLAYWRIGHT_MODULE_MISSING', error: playwright.__loadError }));
    return;
  }
  const { chromium } = playwright;
  let browser;
  let context;
  let page;
  try {
    const executablePath = request.chromiumExecutablePath && fs.existsSync(request.chromiumExecutablePath)
      ? request.chromiumExecutablePath
      : undefined;
    browser = await chromium.launch({
      headless: true,
      executablePath,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
    const contextOptions = {
      viewport: { width: request.viewportWidth, height: request.viewportHeight },
      ignoreHTTPSErrors: true,
    };
    if (request.storageStatePath && fs.existsSync(request.storageStatePath)) {
      contextOptions.storageState = request.storageStatePath;
    }
    context = await browser.newContext(contextOptions);
    page = await context.newPage();
    const response = await page.goto(request.url, { waitUntil: 'domcontentloaded', timeout: request.timeoutMs });
    try {
      await page.waitForLoadState('networkidle', { timeout: Math.min(request.timeoutMs, 12000) });
    } catch (_) {}
    if (request.waitForText) {
      await page.waitForFunction(
        (expected) => (document.body && document.body.innerText || '').includes(expected),
        request.waitForText,
        { timeout: Math.min(request.timeoutMs, 20000) },
      );
    }
    const text = await bodyText(page);
    const signals = await visualSignals(page);
    await page.screenshot({ path: request.screenshotPath, fullPage: false });
    const stat = fs.statSync(request.screenshotPath);
    console.log(JSON.stringify({
      ok: true,
      httpStatus: response ? response.status() : null,
      finalUrl: page.url(),
      title: await page.title(),
      bodyTextSample: text.slice(0, request.maxTextChars),
      visualSignals: signals,
      screenshotPath: request.screenshotPath,
      screenshotBytes: stat.size,
      usedStorageState: Boolean(contextOptions.storageState),
    }));
  } catch (error) {
    let text = '';
    let signals = null;
    let finalUrl = '';
    let title = '';
    let screenshotBytes = 0;
    try {
      if (page) {
        text = await bodyText(page);
        signals = await visualSignals(page).catch(() => null);
        finalUrl = page.url();
        title = await page.title().catch(() => '');
        await page.screenshot({ path: request.screenshotPath, fullPage: false }).catch(() => {});
        if (fs.existsSync(request.screenshotPath)) {
          screenshotBytes = fs.statSync(request.screenshotPath).size;
        }
      }
    } catch (_) {}
    console.log(JSON.stringify({
      ok: false,
      errorCode: 'BROWSER_CAPTURE_FAILED',
      error: String(error && error.message || error),
      finalUrl,
      title,
      bodyTextSample: text.slice(0, request.maxTextChars),
      visualSignals: signals,
      screenshotPath: fs.existsSync(request.screenshotPath) ? request.screenshotPath : null,
      screenshotBytes,
    }));
  } finally {
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
  }
})();
'''


def _node_env() -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NODE_PATH",
        "BROWSER_VISUAL_CHROMIUM_EXECUTABLE",
        "PLAYWRIGHT_BROWSERS_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


async def _run_node_capture(request: dict[str, Any], timeout: int) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        return {
            "ok": False,
            "errorCode": "BROWSER_TOOL_UNAVAILABLE",
            "error": "node is not installed; browser visual verification cannot run",
        }

    with tempfile.TemporaryDirectory(prefix="agenthub-browser-visual-") as tmpdir:
        script_path = Path(tmpdir) / "capture.js"
        script_path.write_text(_node_script(), encoding="utf-8")
        commands = [[node, str(script_path)]]
        npx = shutil.which("npx")
        if npx:
            commands.append([npx, "-y", "-p", "playwright-core", "node", str(script_path)])

        last_error: dict[str, Any] | None = None
        for command in commands:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_node_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(json.dumps(request).encode("utf-8")),
                    timeout=timeout + 10,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                last_error = {
                    "ok": False,
                    "errorCode": "BROWSER_CAPTURE_TIMEOUT",
                    "error": f"browser capture exceeded {timeout} seconds",
                }
                continue

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(stdout_text.splitlines()[-1]) if stdout_text else {}
            except (json.JSONDecodeError, IndexError):
                parsed = {}
            if parsed.get("ok") or parsed.get("errorCode") != "PLAYWRIGHT_MODULE_MISSING":
                if process.returncode not in (0, None) and not parsed:
                    return {
                        "ok": False,
                        "errorCode": "BROWSER_CAPTURE_FAILED",
                        "error": stderr_text[:1000] or f"node exited {process.returncode}",
                    }
                return parsed or {
                    "ok": False,
                    "errorCode": "BROWSER_CAPTURE_FAILED",
                    "error": stderr_text[:1000] or "browser capture returned no JSON",
                }
            last_error = parsed

        return last_error or {
            "ok": False,
            "errorCode": "PLAYWRIGHT_MODULE_MISSING",
            "error": "playwright-core is not available to node or npx",
        }


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    lowered = haystack.lower()
    return any(needle in lowered for needle in needles)


def _classify_capture(capture: dict[str, Any], expected_text: str | None) -> dict[str, Any]:
    text = "\n".join(
        str(capture.get(key) or "")
        for key in ("finalUrl", "title", "bodyTextSample", "error")
    )
    signals = capture.get("visualSignals") or {}
    screenshot_bytes = int(capture.get("screenshotBytes") or 0)
    warnings: list[str] = []

    if capture.get("errorCode") in {"BROWSER_TOOL_UNAVAILABLE", "PLAYWRIGHT_MODULE_MISSING"}:
        return {
            "status": "unavailable",
            "errorCode": capture.get("errorCode"),
            "reason": capture.get("error"),
            "warnings": warnings,
        }
    if _contains_any(text, _LOGIN_PATTERNS):
        return {
            "status": "unavailable",
            "errorCode": "BROWSER_AUTH_REQUIRED",
            "reason": "browser rendered an authentication page instead of the target visual; configure BROWSER_VISUAL_AUTH_STATE_PATH/PLAYWRIGHT_STORAGE_STATE_PATH",
            "warnings": warnings,
        }
    if capture.get("errorCode") == "BROWSER_CAPTURE_TIMEOUT":
        return {
            "status": "failed",
            "errorCode": "BROWSER_CAPTURE_TIMEOUT",
            "reason": capture.get("error"),
            "warnings": warnings,
        }
    if _contains_any(text, _RENDER_ERROR_PATTERNS):
        return {
            "status": "failed",
            "errorCode": "VISUAL_RENDER_ERROR",
            "reason": "browser page contains a Fabric/Power BI render error message",
            "warnings": warnings,
        }
    if expected_text and expected_text not in str(capture.get("bodyTextSample") or ""):
        return {
            "status": "failed",
            "errorCode": "EXPECTED_TEXT_NOT_VISIBLE",
            "reason": f"expected text was not visible in the rendered page: {expected_text!r}",
            "warnings": warnings,
        }
    if screenshot_bytes < 2_000:
        return {
            "status": "failed",
            "errorCode": "SCREENSHOT_EMPTY_OR_TOO_SMALL",
            "reason": "screenshot artifact is empty or too small to be credible visual evidence",
            "warnings": warnings,
        }
    if int(signals.get("visibleElementCount") or 0) == 0:
        warnings.append("no visible visual-like DOM elements were detected")
    if not signals.get("colorSamples"):
        warnings.append("no non-transparent background color samples were detected")

    return {
        "status": "passed",
        "errorCode": None,
        "reason": "browser captured a non-empty rendered page without authentication or render-error signals",
        "warnings": warnings,
    }


async def _browser_verify_visual_render_impl(
    url: str,
    expected_text: str | None = None,
    wait_for_text: str | None = None,
    screenshot_name: str | None = None,
    viewport_width: int | None = 1440,
    viewport_height: int | None = 1000,
    timeout_seconds: int | None = 45,
) -> str:
    try:
        normalized = _normalize_url(url)
        timeout = _bounded_timeout(timeout_seconds)
        width = max(320, min(int(viewport_width or 1440), 3840))
        height = max(480, min(int(viewport_height or 1000), 2160))
        screenshot_path = _screenshot_path(screenshot_name, normalized)
        request = {
            "url": normalized,
            "expectedText": expected_text,
            "waitForText": wait_for_text or expected_text,
            "screenshotPath": str(screenshot_path),
            "viewportWidth": width,
            "viewportHeight": height,
            "timeoutMs": timeout * 1000,
            "maxTextChars": _MAX_TEXT_CHARS,
            "storageStatePath": _DEFAULT_STORAGE_STATE,
            "chromiumExecutablePath": os.environ.get(
                "BROWSER_VISUAL_CHROMIUM_EXECUTABLE", "/usr/bin/chromium"
            ),
        }
        capture = await _run_node_capture(request, timeout)
        verdict = _classify_capture(capture, expected_text)
        signals = capture.get("visualSignals") or {}
        return _json({
            "ok": verdict["status"] == "passed",
            "status": verdict["status"],
            "errorCode": verdict.get("errorCode"),
            "reason": verdict.get("reason"),
            "url": normalized,
            "finalUrl": capture.get("finalUrl"),
            "title": capture.get("title"),
            "httpStatus": capture.get("httpStatus"),
            "screenshotPath": capture.get("screenshotPath"),
            "screenshotBytes": capture.get("screenshotBytes"),
            "usedStorageState": capture.get("usedStorageState"),
            "bodyTextSample": str(capture.get("bodyTextSample") or "")[:_MAX_TEXT_CHARS],
            "visualSummary": {
                "viewport": signals.get("viewport"),
                "visualLikeElementCount": signals.get("elementCount"),
                "visibleVisualLikeElementCount": signals.get("visibleElementCount"),
                "colorSamples": signals.get("colorSamples"),
                "sampleElements": (signals.get("elements") or [])[:20],
            },
            "warnings": verdict.get("warnings") or [],
        })
    except BrowserVisualError as exc:
        return _json({
            "ok": False,
            "status": "failed",
            "errorCode": "BROWSER_VISUAL_POLICY_ERROR",
            "error": str(exc),
        })
    except Exception as exc:  # pragma: no cover - defensive MCP boundary
        return _json({
            "ok": False,
            "status": "failed",
            "errorCode": type(exc).__name__,
            "error": str(exc),
        })


@mcp.tool()
async def browser_verify_visual_render(
    url: str,
    expected_text: str | None = None,
    wait_for_text: str | None = None,
    screenshot_name: str | None = None,
    viewport_width: int | None = 1440,
    viewport_height: int | None = 1000,
    timeout_seconds: int | None = 45,
) -> str:
    """Capture and inspect a Fabric/Power BI visual page in a real browser.

    The tool is read-only: it opens an allowed Fabric/Power BI HTTPS URL,
    optionally waits for expected text, saves a PNG screenshot as evidence,
    and returns structured browser/DOM signals. If the browser lands on login,
    a render error, or no credible screenshot is produced, the result is not a
    pass. Use this for visual/style acceptance evidence; do not use metadata
    alone as a substitute when the user asks for visual verification.
    """

    return await _browser_verify_visual_render_impl(
        url=url,
        expected_text=expected_text,
        wait_for_text=wait_for_text,
        screenshot_name=screenshot_name,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        timeout_seconds=timeout_seconds,
    )


if __name__ == "__main__":
    mcp.run()
