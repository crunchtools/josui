# josui Constitution

> **Version:** 2.1.0
> **Ratified:** 2026-10-11
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.40.0
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

- **Image:** `quay.io/crunchtools/josui` (Quay.io is the only registry),
  public. It holds tooling only; identity and credentials are mounted.
- **Base:** UBI 10 Minimal, pinned by digest. Claude Code
  (`CLAUDE_CODE_VERSION`) comes from Anthropic's signed repository, pinned in
  the Containerfile and bumped by hand; the claude-agent packages come from
  crunchtools' signed repository, because a new Claude Code is tried before
  Josui runs on it. `DISABLE_AUTOUPDATER=1`. Telemetry and error reporting are off
  (`DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`); the broader
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is not set, because it also
  turns off the feature-flag fetch Remote Control refuses to start without.
- **Vulnerability scanning:** none at build time; published images are scanned on a schedule by the fleet CVE check (grype + Nagios), which pages on a new fixable Critical (constitution 1.40.0, RT #1551). SBOM (SPDX)
  per build. cosign signing is not wired yet.
- **Liveness:** there is none to monitor from inside. Remote Control polls
  rather than holding a connection, and its debug file is quiet while idle, so
  neither sockets nor file mtimes say whether the bridge is alive. A session
  that does not appear is diagnosed from the `--debug-file`, not inferred.
  A watchdog built on socket sampling was shipped in 0.6.0 and removed in
  0.7.0 (RT#1556); do not reintroduce one without a signal that was measured.
- **No `schedule:` trigger:** rebuilt on `parent-image-updated`, the fleet's
  weekly pulse.
- **Runtime:** UID 1000, `--read-only`, `--tmpfs /tmp`, `--cap-drop=ALL`,
  `no-new-privileges`, SELinux labels on every mount, an internal network
  with `--dns=none`.
- **User namespace:** on the server the container's UID 1000 is mapped to the
  host's root for file access. Workspaces are written by site containers that
  run as root, and two owners in one git checkout break it for both. The
  process holds no capabilities on either side of the mapping.

### The development image

Built here until 2026-11-06 as `Containerfile.dev`, now
[crunchtools/developer](https://github.com/crunchtools/developer) with its own
constitution. It is a different agent: its own Trentina profile, its own
identity, no production data, and deviations this image does not take.

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

- **An ssh key to the host, as root, on a path the gateway does not see.**
  `josui.crunchtools.com` holds an ssh key to gloval as **root**: no forced
  command, no `restrict`, a pty if it asks for one. The one restriction kept
  is `from="10.89.13.10"`, the agent's address on its own internal bridge,
  which nothing else is on; it reaches sshd at 10.89.13.1:22422 through one
  nftables accept rule ahead of that network's drop, the host's public
  interface is not involved, and there is no sshd in the container.

  **This supersedes the forced-command principal of 1.6.0, and it is a
  retirement of that version's stated principle, not a widening of it.** What
  1.6.0 said, and what remains true as an argument:

  > mediated authority is sized to the job, unmediated authority is sized to
  > the emergency. The operator role is root-equivalent on this host only
  > because the gateway logs every call and judges every byte that comes
  > back; putting that same breadth on a channel with neither would move the
  > agent's widest power onto its least observed path.

  That is exactly what this version does, deliberately, because the premise it
  rested on no longer holds. The principle assumed a mediated path wide enough
  to do the work, with the unmediated one kept for the emergency. RT#1568 built
  that mediated path — linux-mcp-server on the host, reached only through the
  gateway — and its write half does not function: upstream's guarded command
  execution cannot reach its gatekeeper, from inside its own process, with any
  provider, to the internet or the LAN. What survives is read-only diagnostics.
  An agent that can read a production host in detail and change nothing on it
  is not the agent this deployment is for. Operator's decision, 2026-11-07.

  What is lost, plainly, and it is more than 1.6.0 lost. This channel is not
  judged by Trentina and is not in its audit table: output returns to the model
  unjudged. And where the forced command wrote a journal line per invocation
  and could only do five things, root with no forced command leaves sshd's
  record that a session opened and which key opened it, and **no record of
  what was run**. An account with full sudo rather than root would at least
  have put each privileged command in syslog; root does not.

  What is left holding it: the `from=` pin, strict host key checking, the
  absence of any route from the container to the host's public interface, and
  the fact that both `/etc/josui-ssh/config` and `/root/.ssh/authorized_keys`
  are now under `/srv` and watched for drift — the files deciding which
  account the agent authenticates as, and who is root on that host, were
  unversioned before this change.

  The honest summary: the agent's widest power is now on its least observed
  path, the thing 1.6.0 was written to prevent. Reversing it is one line in
  `/root/.ssh/authorized_keys`, and `/root/josui-rescue-removed-*` on gloval
  holds the forced command and sudoers that were taken out.

  Reversible by removing one `authorized_keys` line, one nftables rule, one
  sudoers file, one script and one user.

- **SELinux labelling off in `developer` containers.** The profile requires
  SELinux enforcing with labelled mounts. A `label=disable` container runs
  unconfined for container purposes, so this is a deviation in substance
  whatever the host's `getenforce` says. It is required: with labels on, a
  nested pull succeeds and a nested run fails with `crun: read from sync
  socket: Connection reset by peer` (measured on gloval, 2026-11-05). It is
  set on the development containers only; every other container on the host
  keeps its confinement. Rejected alternatives: the host-wide
  `container_modify_selinux_labels` boolean, which would loosen 56 containers
  to serve three and is not even proven sufficient, and a local policy module,
  which cannot be written without AVCs and is the follow-up item, not the
  blocker.

  What does SELinux's old job instead, in order of weight: UID 1000 mapped to
  an unprivileged host range, so file access is DAC's problem and no mount may
  be root-owned-writable outside the container's own volumes; only its own
  volumes mounted, no `/srv` tree and no production data; `--cap-drop=all`
  plus the five measured capabilities, no `SYS_ADMIN` and never
  `--privileged`; `--read-only` with tmpfs on `/tmp`, `/var/tmp` and `/run`;
  its own internal network with no DNS and no host route; egress only through
  its own Trentina profile, which has a five-host allowlist and no operator
  role; memory and pid limits; and claude-agent's audit log and circuit
  breakers.

  What is lost, stated plainly: a path the container should not reach is now
  stopped by the UID map alone, with no MAC behind it.

## Service

| Attribute | Value |
|-----------|-------|
| Container name | `<service>`, the host's name for this instance |
| Port | none inbound; outbound ssh to the host's address on the agent's own bridge |
| Host directory | `/srv/<service>/` (`config/` identity, `config/ssh` the client config and known_hosts, and env, `data/state`, `data/audit`, `data/josui-skills`) |
| Interface | Claude Code Remote Control, and root over ssh on the agent's own bridge |

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-10-11 | First constitution |
| 1.0.1 | 2026-10-13 | Human in the loop: the hook that holds the no-prompt deviation when the app sets the mode |
| 1.0.2 | 2026-10-14 | Human in the loop: the allow hook answers in plan mode too; plan approval stays the gate; declined calls are logged |
| 1.1.0 | 2026-10-14 | GitHub only through the gateway (no gh, no credential); the allow hook and memory guard ship in the image; managed hooks only; web tools denied in managed settings. Inherits v1.26.0 |
| 1.2.0 | 2026-11-05 | The development image (`developer`): its base, its measured five-capability set, its unprivileged UID mapping, and the recorded `label=disable` deviation with what replaces SELinux and what is lost. Trivy does not scan Fedora OS packages; `dnf -y upgrade` plus the weekly rebuild is that layer's only control. RT#1556 |
| 1.3.0 | 2026-11-06 | Liveness: Remote Control polls and its debug file is quiet while idle, so there is no local signal for bridge liveness; the 0.6.0 socket watchdog is removed and a session that does not appear is diagnosed from the --debug-file. RT#1556 |
| 1.4.0 | 2026-11-06 | The development image is published as `developer`, not `josui-dev`: it is a different agent from Josui Prime, with its own profile, identity and reach, and the name said otherwise. RT#1556 |
| 1.5.0 | 2026-11-06 | The development image moved to crunchtools/developer, with its own constitution; what is left here is a pointer. RT#1556 |
| 1.6.0 | 2026-11-06 | An ssh key to gloval as `josui-rescue`, forced command, over the agent's own internal bridge: the gateway's protected-unit list refuses `unit_restart` on the gateway, and a gateway that is down leaves the agent with no tools to fix it with. Sized to that emergency, not to the operator role; unjudged and unaudited by Trentina, so no pty, no shell, no writing verb. RT#1556 |
| 2.0.0 | 2026-11-07 | Root on gloval over ssh, replacing the `josui-rescue` forced command. MAJOR because it retires 1.6.0's principle that unmediated authority is sized to the emergency: RT#1568's mediated write path does not work (upstream's gatekeeper cannot reach any provider from inside its own process), so the unmediated path is sized to the agent instead. Pinned to `from=10.89.13.10`; no forced command means no per-command record. Operator's decision. RT#1568 |
| 2.1.0 | 2026-11-11 | The blocking Trivy gate and `.trivyignore` are retired with the fleet's per-build scan (constitution 1.40.0): published images are scanned on a schedule by the fleet CVE check (RT #1551) |
