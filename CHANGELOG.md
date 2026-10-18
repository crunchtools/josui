# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-18

### Changed

- No `gh` in the image. GitHub is reached through the gateway: its GitHub
  tools for the API and its git route for clone and push.
- Managed settings set `allowManagedHooksOnly`: the mounted identity's hooks,
  hooks in the home volume and hooks in a checkout's `.claude/settings.json`
  no longer run. The prompts-off hook (`allow-hook.sh`, opt-in with
  `JOSUI_ANSWER_PERMISSIONS=1`) and the local memory guard
  (`deny-local-memory.sh`) ship in the image instead.
- Managed settings deny `WebSearch` and `WebFetch` and turn off auto memory.
- The git route is configured before the skills checkout is pulled, so the
  pull at start goes through the gateway.

### Fixed

- File writes no longer prompt the person when `JOSUI_ANSWER_PERMISSIONS=1`.
  A PreToolUse allow does not answer a prompt the session's mode still calls
  for, so `allow-hook.sh` now also answers the `PermissionRequest` event.

### Added

- Matrix channel (RT #1531). With `JOSUI_MATRIX_HOMESERVER` set, an
  interactive Remote Control session, `<JOSUI_NAME>-rcs`, runs beside the
  server with `matrix-channel.py` loaded as a Claude Code channel. Messages
  from the senders in `JOSUI_MATRIX_ALLOW` arrive in it, and Claude answers
  with the channel's `reply` tool. `channel-session` (expect) gives the
  session its terminal and answers the development channels warning at start.
- `expect` in the image.
- The image: UBI 10 Minimal with Claude Code, git, jq, python3 and nodejs,
  running as a non-root user on a read-only root filesystem.
- `josui-entrypoint`: builds the Claude Code home from the mounted identity
  (`CLAUDE.md`, `settings.json`, hooks) and skills checkout, registers the
  Trentina gateway as the one MCP server, and serves Remote Control.
- Managed settings: the gateway is the only MCP server allowed besides the
  Matrix channel, which is admitted by its exact command; claude.ai
  connectors are off, and two hooks run on every tool call.
- `hooks.py audit`: one redacted JSON record per tool call, kept 120 days.
- `hooks.py breaker`: per-conversation circuit breakers on total calls and on the
  same call repeated back to back.
- Remote Control's once-a-second status redraw on stdout is dropped, so it
  does not fill the journal; errors still reach stderr.
- Managed settings turn off telemetry and error reporting only. The broader
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` also disables the feature-flag
  fetch, and Remote Control will not start without it.
- `nodejs-full-i18n`, so locale-dependent tests behave here as they do in the
  containers the code runs in.
- Build workflow: hook tests, Trivy (blocking), SPDX SBOM, Quay and GHCR.
