# Apple `container` 1.7.1 JSON — captured 2026-11-24

Host: macOS 27.2.1 arm64, Homebrew `container` 1.7.1 (`/opt/homebrew/bin/container`).
Spike: `container run -d --name hermes-spike alpine sleep infinity` → stdout is the **name** (`hermes-spike`), not a hex id.

## Commands that produced these files

| File | Command |
|---|---|
| `version.txt` | `container --version` |
| `system-status.txt` | `container system status` (table, not JSON) |
| `list-all.json` | `container list -a --format json` (while spike running; includes leftover `buildkit`) |
| `list-running-spike.json` | same list, filtered to `id == hermes-spike` |
| `inspect-hermes-spike.json` | `container inspect hermes-spike` |
| `image-inspect-alpine.json` | `container image inspect alpine` |

## Parser facts (do not guess past these)

1. **`list --format json` and `inspect` both return a JSON array of objects** with the same shape. Inspect of one id is `[ { … } ]`, not a bare object. `inspect` has **no `--format`**.
2. Top-level keys: `id`, `configuration`, `status`.
3. **`id` is the `--name` string** when one was set (`hermes-spike`), not a Docker-style hex.
4. **Life-cycle state is `status.state`**: `"running"` or `"stopped"`. Stopped entries (see `buildkit` in `list-all.json`) omit `startedDate`.
5. Labels live at `configuration.labels` (object; empty `{}` if none).
6. Image ref on a container: `configuration.image.reference` (e.g. `docker.io/library/alpine:latest`).
7. Workdir / argv: `configuration.initProcess.workingDirectory`, `.executable`, `.arguments`.
8. Resources: `configuration.resources.cpus` (number), `.memoryInBytes` (int). Defaults observed: 4 CPUs, 1 GiB.
9. Caps: `configuration.capAdd` / `capDrop` (arrays of strings). `--init` is `configuration.useInit` (bool).
10. **`image inspect` is also a JSON array.** Item keys: `id` (digest without `sha256:` prefix), `configuration.name`, `variants[]`. Each variant has `platform.architecture` / `os` and OCI `config.config.Entrypoint`/`Cmd`. Multi-arch alpine has many variants including `unknown`.
11. `container image inspect` has **no `--format`**.
12. `container list -q` prints one id per line (the name).
13. **`container exec` exit codes pass through** (`sh -c 'exit 7'` → 7).
14. `container system status` is a two-column table. `status running` means apiserver is up. Plugins (`ps`, `pull`, `version` subcommand) fail until this is running (`container system start`).
15. Top-level `container pull` is a plugin; native pull is `container image pull`.

## Cleanup

`hermes-spike` was `container delete -f`'d after capture. A pre-existing stopped `buildkit` container was left untouched. `container system` was left **running**.
