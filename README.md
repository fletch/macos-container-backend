# macos_container — Hermes terminal backend

Run Hermes’s terminal (and follow-on tools) inside [Apple `container`](https://github.com/apple/container) Linux VMs on this Mac. Native CLI — **not** `HERMES_DOCKER_BINARY=container`.

**Requirements**

- Apple silicon
- macOS 26 or later (developed on 27.2)
- Apple `container` CLI (`brew install container` or the signed pkg) **and** `container system start`
- Hermes `>= 0.21.6`
- `plugins.isolation` left at the default **`in_process`**. `host` isolation makes `exec_prefix` a placeholder; Bot Desktop / `computer_use` will not follow the VM.

**Disclosure:** containers are local Virtualization.framework VMs. No cloud API. Bind mounts (`-v`, `docker_mount_cwd_to_workspace`) expose those host paths to the agent.

## Install

```bash
container system start
hermes plugins install fletch/macos-container-backend --enable
hermes config set terminal.backend macos_container
```

Optional smaller image for smoke tests:

```bash
hermes config set terminal.docker_image docker.io/library/ubuntu:24.04
```

Select the backend **per profile**. Enabling the plugin does not change `terminal.backend` by itself.

## Config

```yaml
terminal:
  backend: macos_container
  docker_image: "nousresearch/hermes-sandbox:desktop"
  container_cpu: 2
  container_memory: 4096
  container_persistent: true      # stop on cleanup, reuse next session
  docker_mount_cwd_to_workspace: true
  docker_forward_env: []
  lifetime_seconds: 300
```

`skip_container_guards` is **false** (host paths can be mounted, so dangerous-command approvals stay on).

Persistent mode: one VM per profile (name `hermes-mc-<profile>`), labeled for reuse. Ephemeral (`container_persistent: false`): delete on cleanup.

Override the binary with `HERMES_CONTAINER_BINARY`. Do **not** set `HERMES_DOCKER_BINARY` to `container`.

## Dev

```bash
python3 -m pytest tests --import-mode=importlib
hermes plugins validate .
```

See `PLAN.md` and `tests/fixtures/SCHEMA.md`.
