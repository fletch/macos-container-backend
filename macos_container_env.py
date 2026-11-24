"""Apple-container environment with Phase 2 Hermes-parity.

Duck-typed BaseEnvironment (execute / cleanup / exec_prefix). Persistent
containers are reused by labels; ephemeral ones are deleted on cleanup.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

try:
    from .cli import ContainerCLI, ContainerError, container_id, container_state
except ImportError:
    from cli import ContainerCLI, ContainerError, container_id, container_state

logger = logging.getLogger(__name__)

LABEL_BACKEND = "hermes.backend"
LABEL_PROFILE = "hermes.profile"
LABEL_PERSIST = "hermes.persist"
LABEL_TASK = "hermes.task"
LABEL_FP = "hermes.fp"
BACKEND_VALUE = "macos_container"
WORKSPACE = "/workspace"


def profile_name() -> str:
    home = (os.getenv("HERMES_HOME") or "").replace("\\", "/")
    if "/profiles/" in home:
        return Path(home).name
    return os.getenv("HERMES_PROFILE") or "default"


def sanitize_name(value: str, *, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return (cleaned or "default")[:max_len]


def fingerprint(image: str, volumes: list[str]) -> str:
    raw = image + "\n" + "\n".join(volumes)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def resource_args(*, cpu: float = 0, memory_mb: int = 0, shm_size: str = "") -> list[str]:
    args: list[str] = []
    if cpu and cpu > 0:
        args.extend(["--cpus", str(cpu)])
    if memory_mb and memory_mb > 0:
        args.extend(["--memory", f"{int(memory_mb)}M"])
    shm = str(shm_size or "").strip()
    if shm and shm != "0":
        args.extend(["--shm-size", shm])
    return args


def volume_args(
    volumes: list[str],
    *,
    host_cwd: str = "",
    mount_cwd: bool = False,
) -> tuple[list[str], str]:
    """Return (argv fragments, in-container cwd)."""
    args: list[str] = []
    for spec in volumes:
        if not isinstance(spec, str) or not spec.strip():
            continue
        args.extend(["-v", spec.strip()])
    cwd_inside = "/"
    host = os.path.abspath(host_cwd) if host_cwd else ""
    if mount_cwd and host and os.path.isdir(host):
        args.extend(["-v", f"{host}:{WORKSPACE}"])
        cwd_inside = WORKSPACE
    return args, cwd_inside


def label_args(labels: dict[str, str]) -> list[str]:
    args: list[str] = []
    for k, v in labels.items():
        args.extend(["--label", f"{k}={v}"])
    return args


def env_args(docker_env: dict[str, str], forward_names: list[str]) -> tuple[list[str], dict[str, str]]:
    """Name-only ``-e KEY`` plus KEY=value pairs. Values for name-only keys go in subprocess env."""
    args: list[str] = []
    extra_env: dict[str, str] = {}
    for key, value in (docker_env or {}).items():
        if not key:
            continue
        args.extend(["-e", f"{key}={value}"])
    for name in forward_names or []:
        if not name or name not in os.environ:
            continue
        args.extend(["-e", name])
        extra_env[name] = os.environ[name]
    return args, extra_env


class MacosContainerEnvironment:
    def __init__(
        self,
        image: str,
        cwd: str = "/",
        timeout: int = 120,
        task_id: str = "default",
        cli: Optional[ContainerCLI] = None,
        *,
        persistent: bool = True,
        cpu: float = 0,
        memory_mb: int = 0,
        shm_size: str = "",
        volumes: Optional[list[str]] = None,
        mount_cwd: bool = False,
        docker_env: Optional[dict[str, str]] = None,
        forward_env: Optional[list[str]] = None,
        extra_args: Optional[list[str]] = None,
        profile: Optional[str] = None,
    ):
        self.timeout = timeout
        self.task_id = task_id
        self.image = image
        self._cli = cli or ContainerCLI()
        self._persistent = bool(persistent)
        self._profile = sanitize_name(profile or profile_name())
        vols = list(volumes or [])
        vol_argv, mounted_cwd = volume_args(vols, host_cwd=cwd, mount_cwd=mount_cwd)
        self.cwd = mounted_cwd if mount_cwd else (cwd or "/")
        if self.cwd.startswith("/Users/") or self.cwd.startswith("/home/"):
            # Host path leaked in; prefer workspace if we mounted it.
            self.cwd = mounted_cwd if mounted_cwd == WORKSPACE else "/"
        self._vol_argv = vol_argv
        self._labels = {
            LABEL_BACKEND: BACKEND_VALUE,
            LABEL_PROFILE: self._profile,
            LABEL_PERSIST: "1" if self._persistent else "0",
            LABEL_TASK: "shared" if self._persistent else sanitize_name(task_id, max_len=32),
            LABEL_FP: fingerprint(image, vols + ([f"{os.path.abspath(cwd)}:{WORKSPACE}"] if mount_cwd else [])),
        }
        extra = list(extra_args or [])
        extra += resource_args(cpu=cpu, memory_mb=memory_mb, shm_size=shm_size)
        extra += vol_argv
        extra += label_args(self._labels)
        env_argv, extra_env = env_args(docker_env or {}, forward_env or [])
        extra += env_argv
        self._extra = extra
        self._extra_env = extra_env
        self._id: Optional[str] = None
        self._cli.ensure_system()
        self._cli.image_ensure(self.image)
        self._attach_or_run()
        logger.info(
            "macos_container %s image=%s persist=%s cwd=%s",
            self._id, self.image, self._persistent, self.cwd,
        )

    def _want_labels(self) -> dict[str, str]:
        return {
            LABEL_BACKEND: self._labels[LABEL_BACKEND],
            LABEL_PROFILE: self._labels[LABEL_PROFILE],
            LABEL_PERSIST: self._labels[LABEL_PERSIST],
            LABEL_TASK: self._labels[LABEL_TASK],
            LABEL_FP: self._labels[LABEL_FP],
        }

    def _attach_or_run(self) -> None:
        if self._persistent:
            found = self._cli.find_by_labels(self._want_labels())
            if found:
                cid = container_id(found)
                state = container_state(found)
                if state != "running":
                    self._cli.start(cid)
                self._id = cid
                logger.info("macos_container reused %s (was %s)", cid, state)
                return
        suffix = self._profile if self._persistent else f"{self._profile}-{uuid.uuid4().hex[:8]}"
        name = f"hermes-mc-{suffix}"[:40]
        run_env = None
        if self._extra_env:
            run_env = {**os.environ, **self._extra_env}
        self._id = self._cli.run_detached(
            name=name, image=self.image, workdir=self.cwd or "/",
            extra_args=self._extra, env=run_env,
        )

    def execute(self, command: str, timeout: Optional[int] = None, **kwargs) -> dict:
        if not self._id:
            raise ContainerError("container not started")
        t = float(timeout if timeout is not None else self.timeout)
        env = os.environ.copy()
        env.update(self._extra_env)
        argv = self._cli.exec_argv(self._id, ["sh", "-c", command], cwd=self.cwd or "/")
        result = self._cli._run(argv, timeout=t, env=env)
        out = (result.stdout or "") + (result.stderr or "")
        return {"output": out, "exit_code": int(result.returncode)}

    def exec_prefix(self, *, user: Any = None, interactive: bool = True) -> Optional[list[str]]:
        if not self._id:
            return None
        return self._cli.exec_argv(self._id, [], cwd="/", interactive=interactive)

    def cleanup(self) -> None:
        cid = self._id
        self._id = None
        if not cid:
            return
        try:
            if self._persistent:
                self._cli.stop(cid)
                logger.info("macos_container stopped %s (kept for reuse)", cid)
            else:
                self._cli.delete_force(cid)
                logger.info("macos_container deleted %s", cid)
        except Exception as e:
            logger.warning("macos_container cleanup %s: %s", cid, e)
