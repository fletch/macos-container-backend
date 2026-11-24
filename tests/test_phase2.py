"""Phase 2: labels, volumes, resources, reuse (fake CLI)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli  # noqa: E402
from macos_container_env import (  # noqa: E402
    LABEL_BACKEND, LABEL_FP, LABEL_PERSIST, LABEL_PROFILE, LABEL_TASK,
    env_args, fingerprint, label_args, resource_args, volume_args,
    MacosContainerEnvironment, WORKSPACE,
)


class FakeRun:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.list_payload = "[]"
        self.run_stdout = "hermes-mc-test"
        self.exec_rc = 0
        self.exec_out = "ok\n"

    def __call__(self, argv, *, timeout, env=None):
        self.calls.append(list(argv))
        verb = argv[1] if len(argv) > 1 else ""
        stdout, rc = "", 0
        if verb == "list":
            stdout = self.list_payload
        elif verb == "run":
            stdout = self.run_stdout
        elif verb == "exec":
            stdout, rc = self.exec_out, self.exec_rc
        elif verb == "inspect":
            stdout = "[]"
        elif verb == "image":
            if len(argv) > 2 and argv[2] == "inspect":
                stdout, rc = "[]", 1
            else:
                stdout, rc = "", 0
        elif verb in {"start", "stop", "delete", "system"}:
            stdout = ""
        elif argv[-1:] == ["--version"] or "--version" in argv:
            stdout = "container CLI version 1.7.1\n"
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")


def test_resource_and_volume_argv(tmp_path):
    assert resource_args(cpu=2, memory_mb=4096, shm_size="1G") == [
        "--cpus", "2", "--memory", "4096M", "--shm-size", "1G",
    ]
    host = tmp_path / "ws"
    host.mkdir()
    argv, inside = volume_args(["/data:/data:ro"], host_cwd=str(host), mount_cwd=True)
    assert argv == ["-v", "/data:/data:ro", "-v", f"{host}:{WORKSPACE}"]
    assert inside == WORKSPACE


def test_labels_and_fingerprint():
    args = label_args({LABEL_BACKEND: "macos_container", LABEL_PROFILE: "orac"})
    assert args[:2] == ["--label", "hermes.backend=macos_container"]
    assert fingerprint("img", ["a"]) != fingerprint("img", ["b"])


def test_env_args_name_only(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    args, extra = env_args({"FOO": "bar"}, ["GITHUB_TOKEN"])
    assert "-e" in args and "FOO=bar" in args
    assert "GITHUB_TOKEN" in args
    assert extra["GITHUB_TOKEN"] == "secret"


def test_run_includes_labels_volumes_cpus():
    fake = FakeRun()
    c = cli.ContainerCLI(exe="container", runner=fake)
    extra = (
        resource_args(cpu=2, memory_mb=1024)
        + ["-v", "/tmp/ws:/workspace"]
        + label_args({LABEL_BACKEND: "macos_container"})
    )
    c.run_detached(name="n", image="ubuntu", extra_args=extra)
    argv = fake.calls[0]
    assert "--cpus" in argv and "--memory" in argv
    assert "-v" in argv and "--label" in argv
    assert "--pids-limit" not in argv


def test_reuse_starts_stopped_match():
    fake = FakeRun()
    fake.list_payload = json.dumps([{
        "id": "hermes-mc-orac",
        "configuration": {"labels": {
            LABEL_BACKEND: "macos_container",
            LABEL_PROFILE: "orac",
            LABEL_PERSIST: "1",
            LABEL_TASK: "shared",
            LABEL_FP: fingerprint("ubuntu", []),
        }},
        "status": {"state": "stopped"},
    }])
    c = cli.ContainerCLI(exe="container", runner=fake)
    env = MacosContainerEnvironment(
        image="ubuntu", persistent=True, profile="orac", cli=c, volumes=[],
    )
    verbs = [call[1] for call in fake.calls]
    assert "start" in verbs
    assert "run" not in verbs
    assert env._id == "hermes-mc-orac"
    prefix = env.exec_prefix()
    assert prefix is not None and prefix[1] == "exec" and "-i" in prefix


def test_ephemeral_deletes_on_cleanup():
    fake = FakeRun()
    c = cli.ContainerCLI(exe="container", runner=fake)
    env = MacosContainerEnvironment(
        image="ubuntu", persistent=False, profile="orac", cli=c, task_id="s1",
    )
    env.cleanup()
    verbs = [call[1] for call in fake.calls]
    assert "run" in verbs
    assert "delete" in verbs
