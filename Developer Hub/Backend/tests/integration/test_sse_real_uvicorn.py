"""Real-world SSE streaming test — spawns a real uvicorn server with
the SAME middlewares as the backend and verifies timing via curl.

This is more reliable than httpx ASGITransport, which buffers.
"""
import asyncio
import json
import subprocess
import sys
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware

# Import the actual middlewares we rewrote.
from main import SecurityHeadersMiddleware, PrivateNetworkAccessMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(PrivateNetworkAccessMiddleware)

    async def emit():
        for i in range(5):
            yield f"id: {i}\ndata: frame {i} at wall={time.monotonic():.3f}\n\n"
            await asyncio.sleep(0.4)
        yield "data: done\n\n"

    @app.get("/stream")
    async def stream():
        return StreamingResponse(
            emit(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Content-Encoding": "identity",
            },
        )

    return app


@pytest.mark.asyncio
async def test_real_uvicorn_streams_under_all_middlewares() -> None:
    app = _build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    # Wait for the server to start listening.
    for _ in range(30):
        try:
            async with httpx.AsyncClient(timeout=1.0) as c:
                r = await c.get("http://127.0.0.1:8765/openapi.json")
                if r.status_code == 200:
                    break
        except Exception:
            pass
        await asyncio.sleep(0.1)

    frame_times: list[float] = []
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "GET",
                "http://127.0.0.1:8765/stream",
                headers={"Accept-Encoding": "gzip, deflate"},
            ) as resp:
                print(f"\nHTTP {resp.status_code}")
                print(f"Content-Encoding: {resp.headers.get('content-encoding', '<none>')}")
                print(f"Content-Type:     {resp.headers.get('content-type', '<none>')}")
                print(f"Transfer-Encoding:{resp.headers.get('transfer-encoding', '<none>')}")
                print(f"Server:           {resp.headers.get('server', '<none>')}")

                assert resp.status_code == 200
                ce = resp.headers.get("content-encoding", "").lower()
                assert ce == "identity", f"GZip compressed our stream: CE={ce!r}"
                assert "text/event-stream" in resp.headers.get("content-type", "")

                buf = ""
                async for raw in resp.aiter_bytes(chunk_size=1):
                    buf += raw.decode("utf-8", errors="replace")
                    while "\n\n" in buf:
                        frame, buf = buf.split("\n\n", 1)
                        now = time.monotonic() - start
                        for line in frame.splitlines():
                            if line.startswith("data:"):
                                frame_times.append(now)
                                print(f"  t={now:6.3f}s  {line[:60]}")
                    if "data: done" in buf or any(True for _ in ()):
                        if frame_times and "done" in buf:
                            break
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)

    assert len(frame_times) >= 5, f"got {len(frame_times)} frames"
    span = frame_times[4] - frame_times[0]  # first 5 data frames
    print(f"\nSpan of first 5 frames: {span:.2f}s (expected ~1.6s)")
    assert span > 1.2, (
        f"Frames were buffered! {len(frame_times)} frames in {span:.2f}s. "
        f"Timings: {[f'{t:.2f}' for t in frame_times]}"
    )
    print("✅ PASS — real uvicorn + all middlewares stream in real time")
