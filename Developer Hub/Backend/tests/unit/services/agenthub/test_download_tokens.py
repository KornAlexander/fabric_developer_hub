from __future__ import annotations

import base64

import pytest

from services.agenthub import download_tokens


@pytest.fixture(autouse=True)
def _clear_pending_downloads() -> None:
    download_tokens._pending.clear()
    yield
    download_tokens._pending.clear()


@pytest.mark.asyncio
async def test_issue_and_consume_raw_text_token() -> None:
    token = await download_tokens.issue_token("notes.txt", "text/plain", "hello")

    entry = await download_tokens.consume_token(token)
    assert entry is not None
    assert entry.name == "notes.txt"
    assert entry.mime == "text/plain"
    assert entry.content == b"hello"

    assert await download_tokens.consume_token(token) is None


@pytest.mark.asyncio
async def test_issue_token_decodes_base64_and_urlencoded_data_uris() -> None:
    payload = base64.b64encode(b"binary-data").decode("ascii")
    token = await download_tokens.issue_token(
        "file.bin",
        "application/octet-stream",
        f"data:application/octet-stream;base64,{payload}",
    )
    assert (await download_tokens.consume_token(token)).content == b"binary-data"

    text_token = await download_tokens.issue_token(
        "file.txt",
        "text/plain",
        "data:text/plain,hello%20world",
    )
    assert (await download_tokens.consume_token(text_token)).content == b"hello world"


@pytest.mark.asyncio
async def test_malformed_data_uri_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Malformed data URI"):
        await download_tokens.issue_token("bad.txt", "text/plain", "data:text/plain;base64")


@pytest.mark.asyncio
async def test_expired_tokens_are_swept_before_consume(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(download_tokens.time, "time", lambda: now[0])

    token = await download_tokens.issue_token("old.txt", "text/plain", "old")
    assert token in download_tokens._pending

    now[0] += download_tokens._TTL_SECONDS + 1
    assert await download_tokens.consume_token(token) is None
    assert token not in download_tokens._pending


@pytest.mark.asyncio
async def test_pending_cap_evicts_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"n": 0}

    def fake_token_urlsafe(_n: int) -> str:
        counter["n"] += 1
        return f"tok-{counter['n']}"

    monkeypatch.setattr(download_tokens.secrets, "token_urlsafe", fake_token_urlsafe)
    monkeypatch.setattr(download_tokens, "_MAX_PENDING", 2)
    first = await download_tokens.issue_token("one.txt", "text/plain", "one")
    second = await download_tokens.issue_token("two.txt", "text/plain", "two")
    third = await download_tokens.issue_token("three.txt", "text/plain", "three")

    assert first not in download_tokens._pending
    assert second in download_tokens._pending
    assert third in download_tokens._pending