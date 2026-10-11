# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The image: UBI 10 Minimal with Claude Code, git, gh, jq, python3 and nodejs,
  running as a non-root user on a read-only root filesystem.
- `josui-entrypoint`: builds the Claude Code home from the mounted identity
  (`CLAUDE.md`, `settings.json`, hooks) and skills checkout, registers the
  Trentina gateway as the one MCP server, and serves Remote Control.
- Managed settings: the gateway is the only MCP server allowed, claude.ai
  connectors are off, and two hooks run on every tool call.
- `hooks.py audit`: one redacted JSON record per tool call, kept 120 days.
- `hooks.py breaker`: per-conversation circuit breakers on total calls and on the
  same call repeated back to back.
- Build workflow: hook tests, Trivy (blocking), SPDX SBOM, Quay and GHCR.
