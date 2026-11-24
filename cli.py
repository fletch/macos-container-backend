"""Apple ``container`` CLI adapter. Stdlib only — no Hermes imports.

Argv is built here so tests can assert we never emit Docker-only flags
(``ps --format``, Go templates, ``--pids-limit``, Docker tmpfs option strings).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

SEARCH_PATHS = (
    "/opt/homebrew/bin/container",
    "/usr/local/bin/container",
)

FORBIDDEN_FRAGMENTS = (
    "--pids-limit",
    "--format={{",
    "--format '{{",
    "ps",
)

RunFn = Callable[..., subprocess.CompletedProcess]


class ContainerError(RuntimeError):
    """A ``container`` subprocess failed or returned unusable output."""


def find_container() -> Optional[str]:
    """Locate the Apple ``container`` binary. Cheap: PATH + known prefixes. No system start."""
    override = os.getenv("HERMES_CONTAINER_BINARY")
    if override and _is_executable(override):
        return override
    found = shutil.which("container")
    if found:
        return found
    return next((p for p in SEARCH_PATHS if _is_executable(p)), None)


def _is_executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def default_run(argv: list[str], *, timeout: float, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, stdin=subprocess.DEVNULL, env=env,
    )


def parse_json_array(stdout: str, *, what: str) -> list[Any]:
    """``list --format json`` and ``inspect`` both return a JSON array."""
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ContainerError(f"{what}: not JSON: {e}") from e
    if not isinstance(data, list):
        raise ContainerError(f"{what}: expected a JSON array, got {type(data).__name__}")
    return data


def container_id(obj: dict) -> str:
    return str(obj.get("id") or "")


def container_state(obj: dict) -> str:
    status = obj.get("status") if isinstance(obj, dict) else None
    if not isinstance(status, dict):
        return ""
    return str(status.get("state") or "")


def container_labels(obj: dict) -> dict:
    conf = obj.get("configuration") if isinstance(obj, dict) else None
    if not isinstance(conf, dict):
        return {}
    labels = conf.get("labels") or {}
    return labels if isinstance(labels, dict) else {}


class ContainerCLI:
    def __init__(self, exe: Optional[str] = None, runner: Optional[RunFn] = None):
        self.exe = exe or find_container() or "container"
        self._run = runner or default_run

    def _checked(self, argv: list[str], *, timeout: float, what: str,
                 env: dict | None = None) -> subprocess.CompletedProcess:
        try:
            result = self._run(argv, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as e:
            raise ContainerError(f"{what} timed out after {timeout}s") from e
        except OSError as e:
            raise ContainerError(f"{what} failed to spawn: {e}") from e
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()[:400]
            raise ContainerError(f"{what} exited {result.returncode}: {err}")
        return result

    def version(self) -> str:
        result = self._checked([self.exe, "--version"], timeout=5, what="container --version")
        return (result.stdout or result.stderr or "").strip()

    def plugins_up(self) -> bool:
        """True when the apiserver is up enough that ``list`` works."""
        try:
            self._checked([self.exe, "list", "-q"], timeout=5, what="container list")
            return True
        except ContainerError:
            return False

    def ensure_system(self) -> None:
        """Start apiserver if plugins are down. Not for cheap UI probes."""
        if self.plugins_up():
            return
        self._checked([self.exe, "system", "start"], timeout=120, what="container system start")
        if not self.plugins_up():
            raise ContainerError("container system start completed but list still fails")

    def image_inspect(self, ref: str) -> list[dict]:
        result = self._run([self.exe, "image", "inspect", ref], timeout=30)
        if result.returncode != 0:
            return []
        data = parse_json_array(result.stdout, what="image inspect")
        return [x for x in data if isinstance(x, dict)]

    def image_pull(self, ref: str) -> None:
        self._checked([self.exe, "image", "pull", ref], timeout=900, what=f"image pull {ref}")

    def image_ensure(self, ref: str) -> None:
        if self.image_inspect(ref):
            return
        self.image_pull(ref)

    def run_detached(
        self,
        *,
        name: str,
        image: str,
        workdir: str = "/",
        extra_args: Optional[list[str]] = None,
        env: Optional[dict] = None,
    ) -> str:
        argv = [
            self.exe, "run", "-d",
            "--name", name,
            "-w", workdir,
            "--cap-drop", "ALL",
            "--cap-add", "DAC_OVERRIDE",
            "--cap-add", "CHOWN",
            "--cap-add", "FOWNER",
            "--tmpfs", "/tmp",
            "--tmpfs", "/var/tmp",
            *(extra_args or []),
            image,
            "sleep", "infinity",
        ]
        _assert_safe_argv(argv)
        result = self._checked(argv, timeout=120, what="container run", env=env)
        cid = (result.stdout or "").strip().splitlines()
        return cid[-1] if cid else name

    def exec_argv(
        self,
        container_id: str,
        argv: list[str],
        *,
        cwd: str = "/",
        interactive: bool = False,
    ) -> list[str]:
        cmd = [self.exe, "exec", "-w", cwd]
        if interactive:
            cmd.append("-i")
        cmd.append(container_id)
        cmd.extend(argv)
        return cmd

    def exec_sh(self, container_id: str, command: str, *, cwd: str = "/", timeout: float = 120) -> subprocess.CompletedProcess:
        argv = self.exec_argv(container_id, ["sh", "-c", command], cwd=cwd)
        return self._run(argv, timeout=timeout)

    def list_all(self) -> list[dict]:
        result = self._checked(
            [self.exe, "list", "-a", "--format", "json"], timeout=15, what="container list",
        )
        data = parse_json_array(result.stdout, what="container list")
        return [x for x in data if isinstance(x, dict)]

    def inspect(self, cid: str) -> dict:
        result = self._checked([self.exe, "inspect", cid], timeout=15, what=f"inspect {cid}")
        data = parse_json_array(result.stdout, what="inspect")
        if not data or not isinstance(data[0], dict):
            raise ContainerError(f"inspect {cid}: empty result")
        return data[0]

    def start(self, cid: str) -> None:
        self._checked([self.exe, "start", cid], timeout=30, what=f"start {cid}")

    def stop(self, cid: str) -> None:
        try:
            self._checked([self.exe, "stop", cid], timeout=30, what=f"stop {cid}")
        except ContainerError as e:
            if "not found" in str(e).lower() or "no such" in str(e).lower():
                return
            raise

    def delete_force(self, cid: str) -> None:
        try:
            self._checked([self.exe, "delete", "-f", cid], timeout=30, what=f"delete {cid}")
        except ContainerError as e:
            if "not found" in str(e).lower() or "no such" in str(e).lower():
                return
            raise

    def find_by_labels(self, want: dict[str, str]) -> Optional[dict]:
        """First listed container whose configuration.labels contain *want*."""
        for obj in self.list_all():
            labels = container_labels(obj)
            if all(str(labels.get(k, "")) == v for k, v in want.items()):
                return obj
        return None



def _assert_safe_argv(argv: list[str]) -> None:
    joined = " ".join(argv)
    if "--pids-limit" in argv or "--pids-limit" in joined:
        raise AssertionError("Apple container has no --pids-limit")
    if "{{" in joined:
        raise AssertionError("Do not pass Docker Go templates to Apple container")
    for i, tok in enumerate(argv):
        if tok == "--tmpfs" and i + 1 < len(argv) and ":" in argv[i + 1]:
            raise AssertionError(f"Apple --tmpfs is path-only, not {argv[i + 1]!r}")
    if len(argv) > 1 and argv[1] == "ps":
        raise AssertionError("Use list, not ps")
