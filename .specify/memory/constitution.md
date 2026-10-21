# josui Constitution

> **Version:** 1.1.0
> **Ratified:** 2026-10-11
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.26.0
> **Profile:** Autonomous Agent

Claude Code packaged as a container, run as Josui: the agent its operator
works with over Remote Control. One instance runs on a server; copies of the
same image run elsewhere with different mounts.

This file holds what is specific to this deployment. The fleet rules and the
Autonomous Agent profile (the six layers' requirements, quality gates,
naming) apply at the inherited version and are checked against this repo's
files by `constitution.yml`. They are not restated here.

## Trust Boundary

Two boundaries, both outside the model. On disk, the container can reach what
is bind-mounted into it and nothing else: there is no container socket and no
shell on the host. On the network, it sits on an internal network with no DNS
whose only other member is the Trentina gateway. Every MCP tool result is
judged by the gateway before Josui reads it, and Claude Code's own HTTPS
leaves through the gateway's client egress, to an allowlist of hosts. GitHub
is not on it: the API is the gateway's GitHub tools and git is its git route,
which holds the token and limits the repositories and the branches a push may
move. The container holds no `gh` and no GitHub credential.

## Layer 1 — Trust Boundary Architecture

Single agent behind the gateway. The gateway is the deterministic enforcer
and holds the quarantined reader; Josui never fetches from the web itself
(`WebFetch` and `WebSearch` are denied in its settings). Attended mode: the
agent acts only when the operator starts it, either by opening a conversation
over Remote Control, or, with the text session on, by texting from the
operator's phone. That second path is claude-agent's session supervisor and
Matrix bridge. It is not on Remote Control, and it takes a turn only for a
text from a sender in `JOSUI_MATRIX_ALLOW`, the operator's own number, in a
one-to-one room, which reaches it through Trentina's RCS bridge, judged.

## Layer 2 — MCP Server Governance

The `claude-agent-trentina` package's managed settings allow one MCP server,
`trentina`, by name, and turn off claude.ai connectors. The Matrix bridge is
not an MCP server; it talks only to the agent's own homeserver and to the
supervisor's local socket. Which backends and tools that seat holds,
their scorecards and risk tiers are the gateway's configuration and are not
restated here. The entrypoint writes that one server into Claude Code's state
from `TRENTINA_MCP_URL` and `TRENTINA_MCP_TOKEN` at every start, replacing
whatever was there.

## Layer 3 — Container & Supply Chain Security

- **Image:** `quay.io/crunchtools/josui` and `ghcr.io/crunchtools/josui`,
  public. It holds tooling only; identity and credentials are mounted.
- **Base:** UBI 10 Minimal, pinned by digest. Claude Code
  (`CLAUDE_CODE_VERSION`) comes from Anthropic's signed repository, pinned in
  the Containerfile and bumped by hand; the claude-agent packages come from
  crunchtools' signed repository, because a new Claude Code is tried before
  Josui runs on it. `DISABLE_AUTOUPDATER=1`. Telemetry and error reporting are off
  (`DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`); the broader
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is not set, because it also
  turns off the feature-flag fetch Remote Control refuses to start without.
- **Trivy:** blocking on CRITICAL and HIGH with `.trivyignore`; SBOM (SPDX)
  per build. cosign signing is not wired yet.
- **No `schedule:` trigger:** rebuilt on `parent-image-updated`, the fleet's
  weekly pulse.
- **Runtime:** UID 1000, `--read-only`, `--tmpfs /tmp`, `--cap-drop=ALL`,
  `no-new-privileges`, SELinux labels on every mount, an internal network
  with `--dns=none`.
- **User namespace:** on the server the container's UID 1000 is mapped to the
  host's root for file access. Workspaces are written by site containers that
  run as root, and two owners in one git checkout break it for both. The
  process holds no capabilities on either side of the mapping.

## Layer 4 — Runtime Security & Behavioral Controls

- **Circuit breakers** (claude-agent's `hooks.py breaker`): 500 tool calls per conversation and 5
  identical calls in a row. The profile's default of 50 calls is a chat
  agent's budget; one code change here is routinely a few hundred. A tripped
  conversation is denied every further call and the trip is audited. A
  breaker that cannot read its state denies.
- **Token budget:** the claude.ai subscription's own limits; there is no API
  key to overspend.
- **Audit** (claude-agent's `hooks.py audit`): the profile's record for every tool call, with
  credential-shaped values replaced and long values cut, kept 120 days.
- **Human in the loop:** sessions start in `acceptEdits` by default. File
  edits inside the mounts proceed; most shell commands wait for the
  operator's approval in the app. A deployment may set
  `JOSUI_PERMISSION_MODE=bypassPermissions`, which removes the approval and
  leaves the container, the gateway, the deny rules and the two hooks as the
  limits. That is a deviation from the profile's approval gate and is the
  operator's to take. The app sets its own mode on a session it opens and
  so undoes the setting there; a deployment that has taken the deviation
  holds it with claude-agent's `allow-hook.sh`, turned on with
  `JOSUI_ANSWER_PERMISSIONS=1`, which answers `permissionDecision: allow`. That hook is the same deviation and no wider
  one: it runs only inside this container, a deny from a rule or another
  hook still wins, and it must leave `AskUserQuestion` and `ExitPlanMode`
  to the operator. `ExitPlanMode` is the gate on a plan: the operator still
  approves every plan, and the hook answers for the other tools in every
  mode, because a plan-mode session can outlive the approval of its plan.
  Each call the hook leaves to the operator is logged in the audit
  directory.
- **Managed hooks only:** managed settings set `allowManagedHooksOnly`, so
  only the image's hooks run. With prompts off, a hook from the home volume
  (which the agent can write) or from a checkout's `.claude/settings.json`
  would run unasked; neither does. `WebSearch` and `WebFetch` are denied in
  managed settings, because they bring web pages back past the gateway's
  judging.

## Layer 5 — Credential & Identity Management

The gateway token comes from the env file passed with `--env-file`. Client
egress has no credential: the gateway identifies the caller by the network
the connection arrives from, so the proxy URL carries none and there is
nothing for the agent to print or leak. The entrypoint unsets the gateway token before starting
Claude Code, though the agent can still read it from its own state file, as
any MCP client can.

One credential cannot be injected: Remote Control refuses long-lived tokens
(`claude setup-token`, `CLAUDE_CODE_OAUTH_TOKEN`) as inference-only, so the
Claude login is an interactive `claude auth login` whose refreshable
credential lives in the state volume, mode 0600. There is no GitHub
credential in the container: the gateway injects the token on its git route
and its GitHub tools. git sends the gateway token, which the entrypoint
writes to a 0600 git config in the state volume rather than the environment.

## Layer 6 — Monitoring, Detection & Response

Kill switches: `systemctl stop` (container), SIGTERM to Claude Code
(application), `podman network disconnect` (network). Host monitoring watches
the unit and the freshness of the audit log. Values for alerting live with
the host's monitoring configuration.

## Deviations

- **System-tier tools in production.** The profile prohibits shell and file
  write in production. They are what this agent is for. They are contained by
  the mounts, the read-only root, the internal network and the approval
  prompt, not by removing them.

## Service

| Attribute | Value |
|-----------|-------|
| Container name | `<service>`, the host's name for this instance |
| Port | none |
| Host directory | `/srv/<service>/` (`config/` identity and env, `data/state`, `data/audit`, `data/josui-skills`) |
| Interface | Claude Code Remote Control |

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-10-11 | First constitution |
| 1.0.1 | 2026-10-13 | Human in the loop: the hook that holds the no-prompt deviation when the app sets the mode |
| 1.0.2 | 2026-10-14 | Human in the loop: the allow hook answers in plan mode too; plan approval stays the gate; declined calls are logged |
| 1.1.0 | 2026-10-14 | GitHub only through the gateway (no gh, no credential); the allow hook and memory guard ship in the image; managed hooks only; web tools denied in managed settings. Inherits v1.26.0 |
