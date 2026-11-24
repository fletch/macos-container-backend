"""Provider classification flags (no VM)."""
from __future__ import annotations

import pytest

pytest.importorskip("agent.terminal_env_provider")

from provider import MacosContainerProvider  # noqa: E402


def test_flags():
    p = MacosContainerProvider()
    assert p.name == "macos_container"
    assert p.is_remote is True
    assert p.is_container is True
    assert p.skip_container_guards is False
    assert p.supports_streams is True


def test_create_environment_accepts_unknown_kwargs():
    p = MacosContainerProvider()
    # Should not TypeError on extra factory keys.
    import inspect
    sig = inspect.signature(p.create_environment)
    assert any(v.kind == inspect.Parameter.VAR_KEYWORD for v in sig.parameters.values())
