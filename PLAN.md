# Plan: Hermes terminal backend for Apple `container`

**Goal.** A first-class Hermes *terminal environment plugin* that drives Apple’s `container` CLI natively — not a Docker-CLI shim via `HERMES_DOCKER_BINARY`.

**Non-goal.** Teaching core `docker.py` about Apple. That file is Docker/Podman argv. A plugin is the supported extension point (same as Singularity/Apptainer).

**Repo home.** `~/projects/macos-container-backend/` (this directory). Later: optional PR to `NousResearch/hermes-official-plugins`.

**Local facts.** Apple `container` 1.7.1 at `/opt/homebrew/bin/container`. Host is macOS 27.2 (Apple silicon). `container system` was started 2026-11-24 for Phase 0 and left running. JSON fixtures: `tests/fixtures/` (see `SCHEMA.md`). GitHub: `fletch/macos-container-backend` (3a local done; 3b push blocked — PAT cannot `createRepository`).

---

## 1. Why a plugin, not a shim

Hermes `terminal.backend: docker` issues Docker-shaped argv (`ps --format '{{.ID}}'`, `inspect --format`, `image inspect --format`, `pull`, `--pids-limit`, `--tmpfs /tmp:rw,nosuid,size=512m`). Apple’s CLI:

| Need | Apple CLI |
|---|---|
| list | `container list --format json` (`ps` is a plugin, only after `container system start`) |
| inspect | JSON dump, **no** `--format` |
| images | `container image pull` / `container image inspect` |
| delete | `container delete -f` (`rm -f` exists as alias once plugins are up) |
| version probe | `container --version` |
| tmpfs | path only |
| pids | **no `--pids-limit`** |

A plugin talks those commands directly and maps Hermes lifecycle onto them.

Reference implementation: [singularity plugin](https://github.com/NousResearch/hermes-official-plugins/tree/main/singularity) — `plugin.yaml` + `register(ctx)` + `TerminalEnvironmentProvider` + environment class.

Contract: `agent/terminal_env_provider.py` and [terminal-environment-plugin.md](https://hermes-agent.nousresearch.com/docs/developer-guide/terminal-environment-plugin).

---

## 2. Naming and classification

| Field | Value | Why |
|---|---|---|
| Plugin / backend `name` | `macos_container` | `[a-z0-9_]`. Not `docker`/`local`/`ssh` (reserved). Not `container` (collides with the binary in docs and PATH). |
| `display_name` | `macOS Container` | Setup / doctor / dashboard picker |
| `is_remote` | `True` | Commands run in a Linux VM, not host macOS. Suppress host OS/home/cwd in the system prompt. |
| `is_container` | `True` | Container path/cwd handling, `container_*` config passed through |
| `skip_container_guards` | **`False`** | We will bind-mount host paths (`-v`). Default `is_container→True` would skip dangerous-command approvals; wrong if `/Users/fletch` is visible. |
| `supports_streams` | `True` (phase 2) | Bot Desktop / `computer_use` / browser follow via `exec_prefix()` |
| `session_isolated_when_nonpersistent` | `True` | Match Docker: `container_persistent: false` → one sandbox per session |
| `cache_path_base` | `"/root/.hermes"` | Same as Docker sandbox image convention |
| `default_cwd` | `"/workspace"` | When `terminal.cwd` is unset/unusable |
| `strip_env_keys` | empty (unless we add a vendor token later) | No Apple API key |
| Hosted scopes | **out of v1** | `serves_hosted_scopes = False` |

`create_environment(..., **kwargs)` must ignore unknown keys.

---

## 3. Layout

```
macos-container-backend/
├── PLAN.md                 # this file
├── README.md
├── plugin.yaml
├── pyproject.toml
├── __init__.py             # register(ctx)
├── provider.py             # MacosContainerProvider
├── macos_container_env.py  # MacosContainerEnvironment
├── cli.py                  # thin wrapper around the `container` binary
└── tests/
    ├── test_provider.py
    ├── test_cli_argv.py    # argv builder, no live VM
    └── test_env_fake.py    # fake CLI
```

`plugin.yaml`:

```yaml
name: macos_container
label: macOS Container
kind: backend
version: 0.1.0
description: >
  Terminal backend that runs Hermes tools inside Apple container(1)
  Linux VMs (Virtualization.framework). Select with
  terminal.backend: macos_container.
requires_hermes: ">=0.21.6"
provides_tools: []
provides_hooks: []
```

Install (local, no catalog):

```bash
hermes plugins install /Users/fletch/projects/macos-container-backend --enable
hermes -p <profile> config set terminal.backend macos_container
```

Do **not** flip orac/default onto this until the fake-CLI tests pass and a live alpine smoke test passes.

---

## 4. CLI adapter (`cli.py`)

One module owns every subprocess. Environment code never concatenates argv ad hoc.

**Binary discovery** (cached):

1. `HERMES_CONTAINER_BINARY` if executable
2. `shutil.which("container")`
3. `/opt/homebrew/bin/container`, `/usr/local/bin/container`

**Required operations** (native, not Docker aliases):

| Method | argv |
|---|---|
| `version()` | `[exe, "--version"]` |
| `system_status()` | `[exe, "system", "status"]` or equivalent; treat “plugins unavailable” as needs_setup |
| `ensure_system()` | `[exe, "system", "start"]` — **only** from `check_requirements` / `post_setup` / first `create_environment`, never from cheap `is_available()` |
| `image_inspect(ref)` | `[exe, "image", "inspect", ref, "--format", "json"]` if supported, else parse default JSON |
| `image_pull(ref)` | `[exe, "image", "pull", ref]` |
| `run_detached(...)` | `[exe, "run", "-d", "--name", name, ...image, "sleep", "infinity"]` |
| `exec(id, argv, *, cwd, env, interactive)` | `[exe, "exec", "-w", cwd, ...]` |
| `list_all()` | `[exe, "list", "-a", "--format", "json"]` |
| `inspect(id)` | `[exe, "inspect", id]` → `json.loads` |
| `start` / `stop` / `delete_force` | `start` / `stop` / `delete -f` |

**Run flags we *will* pass** (confirmed present on 1.7.1):

- `--name`, `-d`, `-w`, `-e KEY` (name-only, values via subprocess env — same secret-hygiene as docker.py #96268)
- `--cpus`, `--memory` (from `container_cpu` / `container_memory`)
- `--cap-drop ALL` + `--cap-add DAC_OVERRIDE,CHOWN,FOWNER`
- `--tmpfs /tmp` and `--tmpfs /var/tmp` (**path only** — drop Docker size/nosuid options)
- `--shm-size` from `terminal.docker_shm_size` if set
- `-v src:dst[:ro]` from `docker_volumes` + optional cwd→`/workspace`
- `--label hermes.backend=macos_container`, `hermes.profile=`, `hermes.task=`
- `--init` unless we detect an s6-overlay image (skip if `image inspect` JSON entrypoint looks like `/init`; if inspect is too weak, skip `--init` when image name contains `hermes-sandbox`)

**Flags we will *not* pass:** `--pids-limit`, Docker tmpfs option strings, `docker info`, Go templates.

Parse `list --format json` / `inspect` ourselves. If JSON shape drifts, isolate it in `cli.py`.

---

## 5. Environment lifecycle (`macos_container_env.py`)

Subclass `tools.environments.base.BaseEnvironment` so we inherit file-sync and background-process plumbing.

**Identity / reuse (v1, Docker-like):**

- Persistent (`container_persistent: true`, default): one container per **profile** (plus workspace key if `docker_mount_cwd_to_workspace`).
- Non-persistent: one container per **session** (`task_id` / session id).
- Reuse: `list -a --format json`, filter labels, `inspect` for running vs stopped, `start` if stopped, else `run -d`.
- Idle: honor `terminal.lifetime_seconds` — `stop` (persistent) or `delete -f` (ephemeral) on `cleanup()` / idle reaper. Do not `system stop` (that kills *all* Apple containers).

**`execute()`:** `container exec -w <cwd>` wrapping `bash -lc`. Return `{output, exit_code}`.

**`cleanup()`:** detach; do not raise if the VM is already gone. Persistent: leave the container unless idle-expired. Ephemeral: `delete -f`.

**`exec_prefix()` (phase 2):** ` [exe, "exec", "-i", "-w", "/", container_id] ` so Bot Desktop can follow. Requires **in-process** plugin isolation (default). Document that `plugins.isolation: host` disables streams.

**Image:** reuse `terminal.docker_image` (default `nousresearch/hermes-sandbox:desktop`) so existing profile configs keep working. Optional override `terminal.macos_container_image` / `TERMINAL_MACOS_CONTAINER_IMAGE` wins if set.

**Env forwarding:** reuse `terminal.docker_forward_env` + `docker_env`. Same blocklist as Docker.

---

## 6. Provider (`provider.py`)

```
is_available()     → binary on PATH. No `system start`. No network.
check_requirements → binary + `container --version` + `system start` if plugins down.
probe()            → ready | needs_setup ("run: container system start") | unavailable (not Apple silicon / no binary)
setup_instructions → install pkg, `container system start`, set backend
doctor_checks      → binary, version, system running, image present (pull is not cheap; skip in doctor)
create_environment → MacosContainerEnvironment(...)
```

`is_available` must stay UI-cheap (docs: no network, runs during paints).

---

## 7. Phases

### Phase 0 — spike (no Hermes)

Manual, on this Mac:

1. `container system start`
2. `container run -d --name hermes-spike alpine sleep infinity`
3. `container exec hermes-spike echo ok`
4. `container list -a --format json` — capture schema
5. `container inspect hermes-spike` — capture schema
6. `container image inspect alpine` — capture schema
7. `container delete -f hermes-spike`

Write the captured JSON samples into `tests/fixtures/`. This unblocks the parser without guessing.

### Phase 1 — MVP plugin — done 2026-11-24

Plugin lives in this directory; enabled as `macos_container` under `~/.hermes/plugins/macos_container`. Fake-CLI tests pass. Live smoke used `ubuntu:24.04` (`uname` Linux aarch64, `exit 7` preserved). Throwaway profile `macos-ct` has `terminal.backend: macos_container` (cloned default plugins; `hermes -p macos-ct` currently hangs on plugin dep resolution — use the Python env smoke, not that profile, until slimmed).

- Provider + CLI adapter + environment: run / exec / delete
- Fake-CLI unit tests (no VM)
- Local `hermes plugins install … --enable`
- One throwaway profile **or** a dedicated test profile — **not** orac — `terminal.backend: macos_container`, image `alpine`, `hermes -p … -z 'echo hello && uname -a'`

Exit criteria: one-shot command returns Linux `uname` and nonzero commands surface `exit_code`.

### Phase 2 — Hermes-parity — done 2026-11-24

Labels (`hermes.backend/profile/persist/task/fp`), persistent stop+start reuse, cpu/memory/shm, `-v` + cwd→`/workspace`, `-e` env forwarding, `exec_prefix` / `supports_streams=True`, doctor image check. Default image `nousresearch/hermes-sandbox:desktop` (override with `terminal.docker_image`). Live ubuntu smoke: host bind-mount, write `/workspace/ctr.txt`, stop, reuse, `exit 9`, `exec_prefix` argv.

- Label-based reuse, persistent vs per-session
- cpu/memory, volumes, cwd→`/workspace`, env forwarding
- `exec_prefix` / `supports_streams`
- `hermes doctor` / setup copy
- Default image = Hermes sandbox (expect first pull to be slow)

Exit criteria: `hermes pets doctor`-style terminal round-trip, file read/write inside `/workspace`, idle stop/reuse.

### Phase 3 — polish (optional, not started)

Phase 0–2 made a backend that works on this Mac. Phase 3 is making it installable, discoverable, and honest in the UI — **no new runtime features unless a gap blocks shipping**.

#### 3a. Repo hygiene (must before anyone else installs)

- Real git remote (GitHub). `plugin.yaml` `homepage` is currently a local path; catalogs want an https URL.
- LICENSE, CHANGELOG, version bump past `0.1.0` when tagged.
- README: requirements (Apple silicon, macOS 26+, `container system start`), config keys, `skip_container_guards: false`, persistent = one VM per profile, `plugins.isolation` must stay **in_process** for `exec_prefix` / Bot Desktop.
- `.gitignore` for `__pycache__`, `.pytest_cache`.
- `hermes plugins validate .` + `hermes plugins doctor` on a clean clone (not a symlink).

#### 3b. Install from git (replaces the symlink)

Today: `ln -s ~/projects/macos-container-backend ~/.hermes/plugins/macos_container`.

Target:

```bash
hermes plugins install <owner>/macos-container-backend --enable
hermes config set terminal.backend macos_container
```

That needs a public (or at least cloneable) repo, `plugin.yaml` that passes catalog CI, and a provenance row so `hermes plugins update` works. Local path install is *not* a first-class `plugins install` identifier — git URL or `owner/repo`.

Do **not** clone-from default into a smoke profile (that is how `macos-ct` hung on plugin deps). Smoke profile: empty/`--no-skills`, enable only this plugin.

#### 3c. Catalog vs official-plugins

Two distribution lanes, pick one:

| Lane | What it is | Fit |
|---|---|---|
| **Standalone + catalog** | Own repo; submit via [catalog-submission](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission); users `hermes plugins install …` | Default. Apple `container` is someone else's product; Hermes policy prefers standalone for third-party backends. |
| **hermes-official-plugins** | Same tree as Singularity | Only if Nous wants it next to Singularity. Requires their review, layout match, and they own updates. |

Do not merge into `hermes-agent` core (`tools/environments/`). Plugins extend; they don't shadow `docker`.

Catalog extras: disclosure line (local VMs, no cloud API, bind mounts see host files), `requires_hermes`, maybe `capabilities` if we ever need extra host surfaces.

#### 3d. Desktop / setup picker (mostly free, two nits)

`probe()`, `display_name`, `setup_instructions()`, `doctor_checks()` already feed:

- `hermes setup` backend picker
- Dashboard terminal-backend picker
- `hermes doctor` / `hermes status`

Nits before calling it done:

- Confirm the picker shows **macOS Container** only on Darwin/arm64 (`is_available` is “binary exists”; could also refuse on Intel).
- `plugins.isolation: host` makes `supports_streams` a placeholder — Desktop will refuse to follow. README + setup text must say keep default **in_process**.
- First-run copy: `container system start`, then image pull of `hermes-sandbox:desktop` (large). Offer ubuntu override in setup_instructions (already there).

#### 3e. Gaps that are *not* Phase 3 unless they block a user

Leave these until someone hits them (were “out of v1”):

- Subclass `BaseEnvironment` (login snapshot, cwd markers, file-tool fetch). Duck type is enough for `execute`; Hermes file tools may still be awkward.
- `--init` vs s6-overlay on `hermes-sandbox:desktop` (called out in risks; untested live).
- `lifetime_seconds` idle reaper (we stop on cleanup; no timer).
- Hosted scopes, Docker socket, Compose, cross-profile shared VM, Windows.
- `@pytest.mark.live` in CI — GitHub is not macOS 26 + Apple `container`.

#### Phase 3 exit criteria

1. Fresh machine (or fresh profile): `hermes plugins install <git>` → enable → `terminal.backend: macos_container` → one-shot Linux `uname` without a manual symlink.
2. `hermes doctor` names CLI, apiserver, image.
3. Dashboard/setup lists **macOS Container** with a useful needs_setup string when `container system` is down.
4. README is enough that Fletch is not the on-call parser.

**Out of v1:** hosted scopes, Docker socket API, Compose, sharing one Apple VM across profiles, Windows.

---

## 8. Tests

No live VM in CI (GitHub runners are not macOS 26 + Apple silicon with `container`).

- **Fake executable** that records argv and returns fixture JSON (same pattern as `tests/hermes_cli/test_terminal_backend_docker_probe.py`).
- Assert we never emit `ps --format`, `inspect --format '{{…}}'`, `--pids-limit`, or Docker tmpfs option strings.
- Assert `skip_container_guards is False`.
- Assert `create_environment` accepts extra kwargs.
- Optional `@pytest.mark.live` skip-unless `container system status` is healthy, for local smoke.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| `container system` not running | `probe`/`doctor` say `container system start`; first create may start it |
| `hermes-sandbox:desktop` assumes Docker/cgroup | Alpine first; sandbox image second. `--init` vs s6 is the likely footgun |
| Bind mounts + TCC | Document that the gateway’s TCC responsible process still applies; mounts are host paths |
| One VM per container (heavier than Docker) | Persistent reuse is mandatory for UX; warn in README |
| JSON schema drift (Apple 1.x) | Parser isolated; fixtures pinned to 1.7.1 |
| Plugin isolation `host` | Streams/Bot Desktop won’t follow; README: keep default in-process |
| Accidentally sandboxes orac | Install plugin globally if wanted; **select backend per profile** |

---

## 10. Config surface (v1)

```yaml
terminal:
  backend: macos_container
  docker_image: "nousresearch/hermes-sandbox:desktop"   # or alpine for smoke
  container_cpu: 2
  container_memory: 4096
  container_persistent: true
  docker_mount_cwd_to_workspace: true
  docker_forward_env: []
  lifetime_seconds: 300
```

Optional later: `terminal.macos_container_image`, `HERMES_CONTAINER_BINARY`.

Do not set `HERMES_DOCKER_BINARY=container`. That path stays Docker/Podman.

---

## 11. Suggested first implementation slice

1. Capture Phase 0 JSON fixtures.
2. `cli.py` + tests for argv + JSON parse.
3. `provider.py` + `__init__.py` + `plugin.yaml`.
4. `macos_container_env.py` execute/cleanup only.
5. Install plugin, smoke on a throwaway profile with `alpine`.

Stop after that and review before Phase 2 (reuse, mounts, streams).
