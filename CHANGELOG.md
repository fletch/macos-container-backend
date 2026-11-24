# Changelog

## 0.2.0 — 2026-11-24

Phase 2 Hermes-parity + first git-installable release.

- Persistent reuse by labels (`hermes.backend/profile/persist/task/fp`)
- cpu/memory/shm, bind mounts, cwd → `/workspace`, env forwarding
- `exec_prefix` / `supports_streams`
- Default image `nousresearch/hermes-sandbox:desktop`

## 0.1.0 — 2026-11-24

Phase 1 MVP: `container run` / `exec` / `delete` against Apple `container` 1.7.1 JSON.
