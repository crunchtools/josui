# josui

Claude Code packaged as a crunchtools autonomous agent. The image is tooling
only; who the agent is and what it may touch are mounted in at run time, so
one image serves a long-running instance on a server and any copy run elsewhere.

The operator reaches it over [Remote Control](https://code.claude.com/docs/en/remote-control)
from claude.ai/code or the phone app. Nothing listens for inbound connections.

## What goes in

| Mount | Container path | What it is |
|-------|----------------|------------|
| state | `/home/josui` | Claude and gh logins, sessions, git identity. The only writable home. |
| identity | `/etc/josui` (read-only) | `CLAUDE.md`, `settings.json`, `hooks/`. Private; kept in the host's config repo. |
| skills | `/var/lib/josui/josui-skills` | A git checkout of the skills repo, pulled at start. |
| audit | `/var/log/josui` | One JSON line per tool call. |
| workspaces | same path as the host | What this copy may work on. One `-v` line each. |

A copy can touch what is mounted into it and nothing else: no container
socket, no host shell. Dev sites are mounted whole (`/srv/<site>`), and a site
exposes its own inner loop through files: edit the checkout, `touch
data/control/<name>` to restart a unit inside the site's container, read
`data/logs/<name>.log`.

## Network

The container sits on an internal network with no DNS. Its only peer is the
[Trentina](https://github.com/crunchtools/trentina) gateway, by address:

- MCP: `TRENTINA_MCP_URL` and `TRENTINA_MCP_TOKEN`. The gateway is the only
  MCP server managed settings allow, and claude.ai connectors are off.
- Claude Code's own HTTPS (inference, Remote Control, login refresh) and
  git/gh: `HTTPS_PROXY` pointing at the gateway's client egress, which tunnels
  to an allowlist of hosts and to nothing else.

## Controls

- **Circuit breakers** (`hooks.py breaker`, PreToolUse): `JOSUI_MAX_TOOL_CALLS` per
  conversation (default 500) and `JOSUI_MAX_REPEAT` identical calls in a row
  (default 5). A tripped conversation stays stopped.
- **Audit** (`hooks.py audit`, PostToolUse): timestamp, tool, server, redacted
  arguments, result and conversation, in `audit-YYYY-MM-DD.jsonl`, kept 120 days.
- Both hooks are in `/etc/claude-code/managed-settings.json`, which the
  mounted `settings.json` cannot override.

## Run it

`deploy/josui.service.example` and `deploy/josui.env.example` show a server
deployment; the real unit and env file live in the host's private config repo. First start needs two things done by hand, once, because the login
is personal and Remote Control refuses long-lived tokens:

```bash
podman exec -it <service> claude auth login    # claude.ai account; prints a URL, takes a code
podman exec -it <service> gh auth login
```

Then restart the service. A new working directory also needs its trust
question answered once: `podman exec -it -w <dir> <service> claude`.

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRENTINA_MCP_URL`, `TRENTINA_MCP_TOKEN` | unset | The gateway. Without both, Josui starts with no MCP server and says so. |
| `HTTPS_PROXY`, `NO_PROXY` | unset | Client egress through the gateway. |
| `JOSUI_WORKDIR` | `~/work` | Where Remote Control sessions start. Not a git checkout unless the Claude GitHub App can see its repository: session creation fails otherwise. |
| `JOSUI_NAME` | `josui` | Session name prefix shown in the app. |
| `JOSUI_CAPACITY` | `2` | Concurrent sessions. |
| `JOSUI_PERMISSION_MODE` | `acceptEdits` | Claude Code permission mode for new sessions. `bypassPermissions` asks nothing; hooks and deny rules still apply. The app sets its own mode on a session it opens, which overrides this; to have no prompts there too, mount a `PreToolUse` hook in the identity that answers `permissionDecision: allow`. |
| `JOSUI_MAX_TOOL_CALLS`, `JOSUI_MAX_REPEAT` | `500`, `5` | Circuit breakers. |
| `JOSUI_AUDIT_RETENTION_DAYS` | `120` | Age at which an audit file is deleted at start. |
| `JOSUI_AUDIT_DIR` | set in the image | Where audit files are written; mount a volume there. |
| `JOSUI_BREAKER_DIR` | set in the image | Per-conversation breaker state, on the tmpfs. |
| `JOSUI_CONFIG_DIR`, `JOSUI_SKILLS_DIR` | set in the image | Where the identity and skills mounts are read from. |

## Development

```bash
python3 -m unittest discover -s tests -v
```

The image is built by GitHub Actions only.

## License

AGPL-3.0-or-later
