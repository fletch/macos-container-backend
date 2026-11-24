"""macos_container terminal backend provider."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from agent.terminal_env_provider import TerminalEnvironmentProvider

try:
    from .cli import ContainerCLI, find_container
except ImportError:
    from cli import ContainerCLI, find_container

logger = logging.getLogger(__name__)

# Phase 2 default; override with terminal.docker_image / macos_container_image.
_DEFAULT_IMAGE = "nousresearch/hermes-sandbox:desktop"


def _terminal_config() -> dict:
    try:
        from hermes_cli.config import load_config_readonly
        terminal = load_config_readonly().get("terminal")
        return terminal if isinstance(terminal, dict) else {}
    except Exception:
        return {}


def configured_image() -> str:
    cfg = _terminal_config()
    for key in ("macos_container_image", "docker_image"):
        value = cfg.get(key)
        if value and str(value).strip():
            return str(value).strip()
    env = os.getenv("TERMINAL_MACOS_CONTAINER_IMAGE") or os.getenv("TERMINAL_DOCKER_IMAGE") or ""
    return env.strip() or _DEFAULT_IMAGE


class MacosContainerProvider(TerminalEnvironmentProvider):
    is_remote = True
    is_container = True
    session_isolated_when_nonpersistent = True
    supports_streams = True

    @property
    def name(self) -> str:
        return "macos_container"

    @property
    def display_name(self) -> str:
        return "macOS Container"

    @property
    def description(self) -> str:
        return "Apple container(1) Linux VMs on this Mac (Virtualization.framework)."

    @property
    def env_description(self) -> str:
        return "an Apple container Linux VM"

    @property
    def skip_container_guards(self) -> bool:
        return False

    @property
    def cache_path_base(self) -> Optional[str]:
        return "/root/.hermes"

    @property
    def default_cwd(self) -> Optional[str]:
        return "/workspace"

    def is_available(self) -> bool:
        return find_container() is not None

    def check_requirements(self, config: dict[str, Any]) -> bool:
        exe = find_container()
        if not exe:
            logger.error("macos_container: Apple 'container' CLI not found. Install: https://github.com/apple/container")
            return False
        cli = ContainerCLI(exe=exe)
        try:
            cli.version()
            cli.ensure_system()
        except Exception as e:
            logger.error("macos_container: CLI present but not usable: %s", e)
            return False
        return True

    def probe(self) -> tuple[str, str]:
        if not self.is_available():
            return ("unavailable", "Apple 'container' CLI not found on PATH.")
        cli = ContainerCLI()
        try:
            ver = cli.version()
        except Exception as e:
            return ("needs_setup", f"container --version failed: {e}")
        if cli.plugins_up():
            return ("ready", ver)
        return ("needs_setup", "run: container system start")

    def setup_instructions(self) -> list[str]:
        exe = find_container() or "container"
        return [
            f"Binary: {exe}" if find_container() else "Install Apple container: https://github.com/apple/container/releases",
            "Start the apiserver: container system start",
            f"Image: {configured_image()}",
            "Select: hermes config set terminal.backend macos_container",
            "Optional: hermes config set terminal.docker_image docker.io/library/ubuntu:24.04  # smaller smoke image",
        ]

    def doctor_checks(self) -> list[tuple[bool, str, str]]:
        exe = find_container()
        rows = [(bool(exe), "Apple container CLI", f"({exe})" if exe else "(not found)")]
        if not exe:
            return rows
        cli = ContainerCLI(exe=exe)
        up = False
        try:
            up = cli.plugins_up()
        except Exception:
            up = False
        rows.append((up, "container apiserver", "(running)" if up else "(run: container system start)"))
        image = configured_image()
        local = False
        if up:
            try:
                local = bool(cli.image_inspect(image))
            except Exception:
                local = False
        rows.append((local, f"image {image}", "(present)" if local else "(will pull on first run)"))
        return rows

    def create_environment(
        self, *, cwd: str, timeout: int, task_id: str = "default",
        image: Optional[str] = None, container_config: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ):
        try:
            from .macos_container_env import MacosContainerEnvironment
        except ImportError:
            from macos_container_env import MacosContainerEnvironment
        cfg = {**_terminal_config(), **(container_config or {})}
        cpu = cfg.get("container_cpu") or 0
        memory = cfg.get("container_memory") or 0
        try:
            cpu = float(cpu)
        except (TypeError, ValueError):
            cpu = 0
        try:
            memory = int(memory)
        except (TypeError, ValueError):
            memory = 0
        persist = cfg.get("container_persistent", True)
        if isinstance(persist, str):
            persist = persist.lower() not in {"0", "false", "no"}
        volumes = cfg.get("docker_volumes") or []
        if not isinstance(volumes, list):
            volumes = []
        extra = cfg.get("docker_extra_args") or []
        if not isinstance(extra, list):
            extra = []
        docker_env = cfg.get("docker_env") or {}
        if not isinstance(docker_env, dict):
            docker_env = {}
        forward = cfg.get("docker_forward_env") or []
        if not isinstance(forward, list):
            forward = []
        mount_cwd = bool(cfg.get("docker_mount_cwd_to_workspace"))
        shm = str(cfg.get("docker_shm_size") or "")
        return MacosContainerEnvironment(
            image=(image or "").strip() or configured_image(),
            cwd=cwd or "/",
            timeout=timeout,
            task_id=task_id,
            persistent=bool(persist),
            cpu=cpu,
            memory_mb=memory,
            shm_size=shm,
            volumes=volumes,
            mount_cwd=mount_cwd,
            docker_env=docker_env,
            forward_env=forward,
            extra_args=[str(x) for x in extra if isinstance(x, str)],
        )
