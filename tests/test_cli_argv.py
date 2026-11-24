"""Argv builder never emits Docker-only flags."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class FakeRun:
    def __init__(self, stdout="", returncode=0):
        self.calls: list[list[str]] = []
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, argv, *, timeout, env=None):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr="")


def test_run_detached_argv_is_apple_native(tmp_path, monkeypatch):
    fake = FakeRun(stdout="hermes-mc-deadbeef\n")
    c = cli.ContainerCLI(exe="/opt/homebrew/bin/container", runner=fake)
    cid = c.run_detached(name="hermes-mc-deadbeef", image="docker.io/library/ubuntu:24.04")
    assert cid == "hermes-mc-deadbeef"
    argv = fake.calls[0]
    assert argv[0].endswith("container")
    assert argv[1:4] == ["run", "-d", "--name"]
    assert "--tmpfs" in argv
    tmpfs_paths = [argv[i + 1] for i, t in enumerate(argv) if t == "--tmpfs"]
    assert tmpfs_paths == ["/tmp", "/var/tmp"]
    assert "--pids-limit" not in argv
    assert not any("{{" in t for t in argv)
    assert "ps" not in argv
    assert argv[-3:] == ["docker.io/library/ubuntu:24.04", "sleep", "infinity"]


def test_list_uses_json_not_go_templates():
    fake = FakeRun(stdout=(FIX / "list-running-spike.json").read_text())
    c = cli.ContainerCLI(exe="container", runner=fake)
    rows = c.list_all()
    assert fake.calls[0][1:4] == ["list", "-a", "--format"]
    assert fake.calls[0][4] == "json"
    assert rows[0]["id"] == "hermes-spike"
    assert cli.container_state(rows[0]) == "running"


def test_inspect_returns_first_object():
    fake = FakeRun(stdout=(FIX / "inspect-hermes-spike.json").read_text())
    c = cli.ContainerCLI(exe="container", runner=fake)
    obj = c.inspect("hermes-spike")
    assert obj["id"] == "hermes-spike"
    assert fake.calls[0][1] == "inspect"
    assert not any("{{" in t for t in fake.calls[0])


def test_image_inspect_empty_on_missing():
    fake = FakeRun(stdout="no such image", returncode=1)
    c = cli.ContainerCLI(exe="container", runner=fake)
    assert c.image_inspect("missing:tag") == []
    assert fake.calls[0][1:3] == ["image", "inspect"]


def test_exec_argv():
    c = cli.ContainerCLI(exe="/bin/container", runner=FakeRun())
    argv = c.exec_argv("cid", ["sh", "-c", "uname -a"], cwd="/")
    assert argv == ["/bin/container", "exec", "-w", "/", "cid", "sh", "-c", "uname -a"]


def test_find_container_honors_override(tmp_path, monkeypatch):
    bin_path = tmp_path / "container"
    bin_path.write_text("#!/bin/sh\n")
    bin_path.chmod(0o755)
    monkeypatch.setenv("HERMES_CONTAINER_BINARY", str(bin_path))
    monkeypatch.delenv("PATH", raising=False)
    assert cli.find_container() == str(bin_path)


def test_parse_fixtures_stopped_has_no_started_date():
    rows = cli.parse_json_array((FIX / "list-all.json").read_text(), what="list")
    by_id = {cli.container_id(r): r for r in rows}
    assert cli.container_state(by_id["buildkit"]) == "stopped"
    assert "startedDate" not in by_id["buildkit"]["status"]
    assert cli.container_state(by_id["hermes-spike"]) == "running"


def test_parse_image_inspect_fixture():
    rows = cli.parse_json_array((FIX / "image-inspect-alpine.json").read_text(), what="img")
    assert rows[0]["configuration"]["name"] == "docker.io/library/alpine:latest"
