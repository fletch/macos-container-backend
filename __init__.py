"""macos_container — Hermes terminal backend using Apple container(1)."""

from __future__ import annotations


def register(ctx) -> None:
    try:
        from .provider import MacosContainerProvider
    except ImportError:
        from provider import MacosContainerProvider
    ctx.register_terminal_environment_provider(MacosContainerProvider())
