"""Unit tests for the ServiceRegistry singleton."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.service_registry import ServiceRegistry, get_service_registry


@pytest.fixture(autouse=True)
def _reset_registry():
    """ServiceRegistry is a singleton — reset state between tests."""
    ServiceRegistry._instance = None
    yield
    ServiceRegistry._instance = None


# ── Singleton behaviour ────────────────────────────────────────────


def test_get_service_registry_returns_singleton() -> None:
    a = get_service_registry()
    b = ServiceRegistry()
    assert a is b


def test_singleton_init_does_not_wipe_existing_state() -> None:
    """REGRESSION: __init__ must not reset state when called again on the
    singleton instance (since __new__ returns the existing one)."""
    reg = get_service_registry()

    class Dummy: ...
    reg.register(Dummy, Dummy())

    # Re-construct via the public class — should still be the same instance
    reg2 = ServiceRegistry()
    assert reg2 is reg
    assert reg2.has(Dummy)


# ── register / get / has ───────────────────────────────────────────


def test_register_and_get_instance_roundtrip() -> None:
    reg = get_service_registry()

    class S: ...
    inst = S()
    reg.register(S, inst)

    assert reg.has(S)
    assert reg.get(S) is inst
    assert "S" in reg.get_all_services()


def test_register_factory_creates_instance_lazily() -> None:
    reg = get_service_registry()
    calls = []

    class S: ...
    def factory() -> S:
        calls.append(1)
        return S()

    reg.register_factory(S, factory)
    assert reg.has(S)
    assert calls == []  # not yet created

    inst1 = reg.get(S)
    inst2 = reg.get(S)
    assert inst1 is inst2
    assert len(calls) == 1  # cached after first creation


def test_get_unregistered_raises_keyerror() -> None:
    reg = get_service_registry()

    class Missing: ...
    with pytest.raises(KeyError, match="Missing"):
        reg.get(Missing)


def test_has_returns_false_for_unregistered() -> None:
    reg = get_service_registry()

    class S: ...
    assert reg.has(S) is False


# ── Cleanup auto-detection ──────────────────────────────────────────


def test_register_with_dispose_async_registers_handler() -> None:
    reg = get_service_registry()

    class WithAsyncDispose:
        async def dispose_async(self) -> None: ...

    inst = WithAsyncDispose()
    reg.register(WithAsyncDispose, inst)
    assert len(reg._cleanup_handlers) == 1
    assert reg._cleanup_handlers[0][0] == "WithAsyncDispose"


def test_register_with_close_only_registers_handler() -> None:
    reg = get_service_registry()

    class WithClose:
        def close(self) -> None: ...

    reg.register(WithClose, WithClose())
    assert len(reg._cleanup_handlers) == 1


def test_register_without_cleanup_methods_skips_handler() -> None:
    reg = get_service_registry()

    class Plain: ...
    reg.register(Plain, Plain())
    assert reg._cleanup_handlers == []


def test_factory_created_instance_also_registered_for_cleanup() -> None:
    reg = get_service_registry()

    class S:
        async def dispose_async(self) -> None: ...

    reg.register_factory(S, lambda: S())
    reg.get(S)
    assert len(reg._cleanup_handlers) == 1


# ── cleanup() async behaviour ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_calls_dispose_async_in_lifo_order() -> None:
    reg = get_service_registry()
    order: list[str] = []

    class A:
        async def dispose_async(self) -> None:
            order.append("A")

    class B:
        async def dispose_async(self) -> None:
            order.append("B")

    reg.register(A, A())
    reg.register(B, B())

    await reg.cleanup()
    # LIFO: B was registered last → cleaned up first
    assert order == ["B", "A"]
    # State is wiped after cleanup
    assert reg._services == {}
    assert reg._cleanup_handlers == []


@pytest.mark.asyncio
async def test_cleanup_falls_back_to_sync_close() -> None:
    reg = get_service_registry()
    closed: list[bool] = []

    class S:
        def close(self) -> None:
            closed.append(True)

    reg.register(S, S())
    await reg.cleanup()
    assert closed == [True]


@pytest.mark.asyncio
async def test_cleanup_falls_back_to_async_close() -> None:
    reg = get_service_registry()
    mock = MagicMock()
    mock.close = AsyncMock()

    class S: ...
    inst = S()
    inst.close = mock.close
    reg.register(S, inst)

    await reg.cleanup()
    mock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_swallows_per_handler_exceptions() -> None:
    """A failing handler must not block subsequent ones."""
    reg = get_service_registry()
    survived: list[str] = []

    class Boom:
        async def dispose_async(self) -> None:
            raise RuntimeError("explode")

    class Ok:
        async def dispose_async(self) -> None:
            survived.append("ok")

    reg.register(Ok, Ok())
    reg.register(Boom, Boom())

    await reg.cleanup()  # must not raise
    assert survived == ["ok"]


@pytest.mark.asyncio
async def test_cleanup_is_reentrancy_safe() -> None:
    reg = get_service_registry()
    reg._is_cleaning_up = True
    # Should early-exit without modifying state
    reg._cleanup_handlers.append(("X", object()))
    await reg.cleanup()
    assert reg._cleanup_handlers != []


@pytest.mark.asyncio
async def test_cleanup_with_no_handlers_is_noop() -> None:
    reg = get_service_registry()
    await reg.cleanup()  # should not raise


# ── clear() sync behaviour ──────────────────────────────────────────


def test_clear_runs_sync_close_and_wipes_state() -> None:
    reg = get_service_registry()
    closed: list[bool] = []

    class A:
        def close(self) -> None:
            closed.append(True)

    class B: ...

    reg.register(A, A())
    reg.register(B, B())
    reg.register_factory(type("F", (), {}), lambda: object())
    reg.mark_initialized()

    reg.clear()

    assert closed == [True]
    assert reg._services == {}
    assert reg._factories == {}
    assert reg._cleanup_handlers == []
    assert reg.is_initialized is False


def test_clear_skips_async_close_methods() -> None:
    """Sync clear() must not call coroutine-returning close."""
    reg = get_service_registry()
    mock = MagicMock()
    mock.close = AsyncMock()

    class S: ...
    inst = S()
    inst.close = mock.close
    reg.register(S, inst)

    reg.clear()
    mock.close.assert_not_called()


# ── initialization state ────────────────────────────────────────────


def test_mark_initialized_flips_flag() -> None:
    reg = get_service_registry()
    assert reg.is_initialized is False
    reg.mark_initialized()
    assert reg.is_initialized is True
