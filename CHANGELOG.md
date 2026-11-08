# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.11.0] - 2026-11-07

### Added
- **A readiness line, so a watcher can tell "started" from "serving".** The
  seat exposes no port, and Nagios watches it with a single check that says the
  container is running. That is all the health signal anything had. The
  entrypoint now waits for the control socket `claude-agent` actually serves on
  and logs `serving: taking commands on <socket> after Ns` once it exists --
  and `gave up: <socket> never appeared in Ns` when it does not.

  The two messages deliberately share no substring. "NOT serving" would contain
  "serving", so a watcher told to wait for the success line would be satisfied
  by the failure one: a readiness check that cannot fail, which is worse than
  none because it gets trusted.

  Every line before this one reports what was *started*, which is a different
  claim. The deploy robot (crunchtools/deployer#17) verifies a seat by waiting
  for this line, so a line printed at launch would report the seat ready while
  the agent was still coming up.

  The wait also stops early if a supervised process has already died, so a
  failure stays fast and clear instead of sitting out the whole timeout waiting
  for a socket that cannot appear. `JOSUI_READY_TIMEOUT` (default 120) and
  `JOSUI_AGENT_SOCKET` are overridable; the socket default was read off the
  live container rather than guessed.

### Changed
- **The ssh fallback is root, not a forced command** (RT #1568). The key into
  gloval authenticated as `josui-rescue`, a forced-command principal with five
  verbs against `*.crunchtools.com.service` and nothing else, sized to one
  emergency: a gateway that is down takes every MCP tool with it, including
  the ones that would restart it. It is now literal root, pinned to
  `from="10.89.13.10"`.

  The mediated write path that was supposed to make the narrow one sufficient
  does not work. linux-mcp-server's guarded command execution cannot reach its
  gatekeeper from inside its own serving process — any provider, internet or
  LAN — so what it offers is read-only diagnostics. An agent that can read a
  production host in detail and change nothing on it is not what this
  deployment is for. Operator's decision.

  The constitution is amended to **2.0.0** rather than quietly contradicted:
  1.6.0 stated that unmediated authority is sized to the emergency and warned
  in terms against moving the agent's widest power onto its least observed
  path. That is what this does, so the amendment keeps the warning in place as
  superseded reasoning and records what is given up — with no forced command,
  sshd logs that a session opened and which key opened it, and nothing logs
  what was run.

  The image is unchanged. Which account the key authorizes was always a
  deployment fact, and the README now says so, with the two shapes and what
  each costs.


### Fixed

- **Logging in a container that is not logged in.** The README told you to
  `podman exec` into the service, which only works when the service is up --
  and a container with no Claude login exits in under a second, so the one
  state that needs the instruction is the one it cannot be used in. It now
  documents the one-off container on the same state volume, the user mapping
  and env file that have to match the unit's, and the read-write fifo that
  gives `claude auth login` a pty and a stdin that does not EOF on a host
  with no terminal multiplexer. Found running it on Josui Prime.

- **A fourth way for a session not to appear.** "When no session appears" now
  covers credentials whose `accessToken` and `refreshToken` are empty strings
  after a failed refresh, which leaves a `.credentials.json` that exists, has
  scopes and a subscription tier, and authenticates nothing.

## [0.10.0] - 2026-11-06

### Added

- **An ssh identity, for when the gateway is down.** Mount a private key, an
  ssh `config` and a `known_hosts` at `/etc/josui/ssh` and the entrypoint
  copies them into `~/.ssh` at mode 0600. Copied rather than pointed at: ssh
  refuses a private key anyone else can read, and the mode on a bind mount is
  the host's. Absent, nothing changes, so a copy run elsewhere is unaffected.

  The reason is a gap this agent cannot otherwise cover. Every tool it has
  arrives through the Trentina gateway, so a gateway that is down leaves it
  with no tools at all — including the systemd tools that would restart the
  gateway — and it cannot report that, because what it would report through is
  what is down. The owner's words: he does not use Josui Prime much *because*
  it cannot fall back to ssh.

  There is **no sshd in the image**; this is a client and outbound only, so
  "nothing listens for inbound connections" still holds. The image carries the
  mechanism only — which host, which account and which key are deployment
  facts and live in the mount. What it costs is in the README and in the
  deployment's constitution: ssh output is not judged by the gateway the way
  every MCP tool result is, and the session is logged by sshd rather than by
  the gateway's audit.

  RT#1556.

## [0.9.0] - 2026-11-06

### Removed

- `Containerfile.dev` and `build-dev.yml`. The development agent image lives in
  [crunchtools/developer](https://github.com/crunchtools/developer) now, which
  has published it once from there, and still publishes the same names. It
  began from this image's entrypoint and mount contract and is expected to
  diverge from them, which is why it left. RT#1556.

## [0.8.0] - 2026-11-06

### Changed

- **The development image is published as `developer`, not `josui-dev`.** It is
  not a Josui variant and should never have read as one: `josui.crunchtools.com`
  is one agent that operates the fleet, and developer1-3 are three that build
  it, with their own Trentina profile, their own identity and no production
  data. Scott created `quay.io/crunchtools/developer`; `ghcr.io/crunchtools/developer`
  follows. The old names stop being published.
- It is still built here, because it still shares this repo's entrypoint and
  mount contract — an implementation detail, not a claim about who it is. That
  sharing is expected to end, and when the image diverges the build moves to
  `crunchtools/developer` with no change to the image name, because the name is
  already right. RT#1556.

## [0.7.0] - 2026-11-06

### Removed

- The egress watchdog from 0.6.0. It measured the wrong thing. Remote Control
  polls -- a connection to the proxy roughly every thirty to two hundred
  seconds, gone in about a second -- so a healthy idle container shows no
  connection at almost any instant: measured on gloval, one hit in ten samples
  over two minutes against a container that was polling normally. As shipped it
  would have ended a healthy container about once per grace period. The debug
  file does not grow while idle either, so there is no cheap local signal for
  bridge liveness, and `JOSUI_WATCHDOG_GRACE`, `JOSUI_WATCHDOG_INTERVAL`,
  `JOSUI_DEBUG_MAX_BYTES` and `JOSUI_NET_TCP` are gone with it.

### Changed

- The `--debug-file` stays, and the README now says to read it first. It earned
  that: on its first read it explained why Josui Prime had shown no session for
  a day -- a work item created days earlier that the server ends with
  `reason=archived` as soon as the bridge picks it up, after which the bridge
  idles with no session. No watchdog would have found that, and restarting
  never fixed it.

## [0.6.0] - 2026-11-05

### Added

- `developer` (`Containerfile.dev`, `quay.io/crunchtools/developer`): the same
  agent with a development toolchain -- python 3.14, `uv`, node, `pre-commit`,
  tmux -- and a nested container engine, so it can run a repo's real gates
  locally. On `quay.io/podman/stable`, because UBI 10 cannot run a nested
  `podman run`. Less contained than the production image, and the README says
  how and why.
- `JOSUI_TMUX=1` starts a detached tmux session `dev` holding an interactive
  Claude Code, reached with `podman exec -it <service> tmux attach -t dev`. It
  follows the attaching terminal's size, which `claude-agent attach` does not,
  and it is not watched: quitting your own shell must not end the container.

### Fixed

- Remote Control losing its connection no longer goes unnoticed. On 2026-11-05
  the Josui Prime container ran for nineteen hours with a live
  `claude remote-control` holding zero sockets, after the gateway restarted
  and the client failed to come back; nothing alerted, because the process was
  alive, its stdout goes to `/dev/null` and `Restart=always` only fires when
  something exits. The entrypoint now watches `/proc/net/tcp` for an
  ESTABLISHED connection to the client egress and ends the container after
  `JOSUI_WATCHDOG_GRACE` (300s) without one. Skipped when `HTTPS_PROXY` is
  unset, so a laptop copy is unaffected.
- Remote Control's `--debug-file` is kept under the audit directory instead of
  being discarded. The session-creation failure diagnosed on 2026-10-11 was
  only ever visible there.

## [0.5.1] - 2026-10-21

### Fixed

- claude-agent 0.2.1, pinned like claude-code: the Matrix bridge no longer
  answers Trentina's "your message was not sent" notices as if they were
  texts from Scott.

## [0.5.0] - 2026-10-21

### Changed

- The agent runtime comes from crunchtools/claude-agent's packages
  (`claude-agent`, `claude-agent-matrix`, `claude-agent-trentina`) and Claude
  Code from Anthropic's signed repository (2.1.304), instead of files copied
  into the image and an npm install.
- The text session is `claude-agent run`: keyboard in, hooks out, with no
  Claude Code channel, no `--dangerously-load-development-channels` and no
  expect. Only each turn's final answer is texted back, with a typing
  indicator while the turn runs; texts sent mid-turn wait; `/stop [text]`
  interrupts; answers go only to a one-to-one room.
- The entrypoint hands `JOSUI_*` settings to claude-agent under its own
  names, so josui.env is unchanged; the bridge keeps the channel's sync
  position.

### Removed

- `matrix-channel.py`, `channel-session`, the hooks and the managed
  settings in `rootfs/`; their tests moved to crunchtools/claude-agent.

## [0.4.1] - 2026-10-20

### Changed

- No ack for a command by text. The channel no longer texts "Sent /clear,
  result to follow"; the command's result (or the 15-minute no-result
  notice) is the only text back.

## [0.4.0] - 2026-10-19

### Added

- Command results by text. After "Sent", the channel texts what the command
  did, read from the session transcript: its output (`/model: Set model to
  …`, `/clear: done`), or for `/compact` the token counts and the summary.
  A command with no result in 15 minutes is reported as such.

## [0.3.0] - 2026-10-19

### Added

- Built-in commands by text. A text of `/clear`, `/compact`, `/model`,
  `/effort`, `/fast`, `/goal`, `/mcp reconnect`, `/reload-skills` or
  `/reload-plugins` is checked by the channel, written to a control FIFO, and
  typed into the session by `channel-session`, which checks it again. The
  channel acks each one; `/help` lists them. Any other text, skills
  included, still reaches the session as before.

### Changed

- `channel-session` takes the control FIFO as a third argument.

### Fixed

- Startup ends after 60 seconds with no dialog. The channel banner never
  shows in the container, so `channel-session` waited for it forever and
  logged a warning every minute.

## [0.2.0] - 2026-10-18

### Changed

- The channel session is no longer on Remote Control. The operator reaches
  it by text, and claude.ai shows only the Remote Control server's sessions
  instead of a second `<JOSUI_NAME>-rcs` one. `channel-session` drops its
  session-name argument.

## [0.1.2] - 2026-10-18

### Fixed

- Channel messages reach the session. With `DISABLE_TELEMETRY` set, Claude
  Code does not load the policy that allows channels, and skips the channel
  ("channels feature is not currently available"). It is no longer in
  managed settings; the entrypoint sets it for the Remote Control server
  only.

## [0.1.1] - 2026-10-18

### Fixed

- The Matrix channel answers requests it does not handle with "method not
  found" instead of an empty result, as JSON-RPC requires. This entry first
  said that change made the channel work; it did not, see 0.1.2.

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
