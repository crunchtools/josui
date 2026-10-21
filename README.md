# josui

Claude Code packaged as a crunchtools autonomous agent. The image is tooling
only; who the agent is and what it may touch are mounted in at run time, so
one image serves a long-running instance on a server and any copy run elsewhere.

The operator reaches it over [Remote Control](https://code.claude.com/docs/en/remote-control)
from claude.ai/code or the phone app. Nothing listens for inbound connections.

## What goes in

| Mount | Container path | What it is |
|-------|----------------|------------|
| state | `/home/josui` | Claude login, sessions, git identity. The only writable home. |
| identity | `/etc/josui` (read-only) | `CLAUDE.md`, `settings.json`. Private; kept in the host's config repo. Hooks here are ignored: only the image's managed hooks run. |
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
  remote MCP server managed settings allow (the local one is the
  [Matrix channel](#matrix-channel)), and claude.ai connectors are off.
- Claude Code's own HTTPS (inference, Remote Control, login refresh):
  `HTTPS_PROXY` pointing at the gateway's client egress, which tunnels to an
  allowlist of hosts and to nothing else. No forge host belongs on it.
- GitHub: the gateway's GitHub tools for the API, and its git route for clone
  and push. With `TRENTINA_GIT_URL` set, the entrypoint writes a git config
  in the state volume (mode 0600) that sends `https://github.com/` to the
  route with the gateway token, and includes it from the global git config.
  There is no `gh` and no GitHub credential in the container; the gateway
  holds the token.

## Controls

The agent runtime is [claude-agent](https://github.com/crunchtools/claude-agent),
installed from its packages: `claude-agent` (breakers, audit, prompts-off, the
session supervisor), `claude-agent-matrix` (the text bridge) and
`claude-agent-trentina` (the crunchtools policy). Every Claude Code session in
the container runs with `CLAUDE_AGENT=1`, so all of them are counted and logged.

- **Circuit breakers:** `JOSUI_MAX_TOOL_CALLS` per conversation (default 500)
  and `JOSUI_MAX_REPEAT` identical calls in a row (default 5). A tripped
  conversation stays stopped.
- **Audit:** one JSON line per tool call in `/var/log/josui`, kept 120 days.
- **Prompts off:** with `JOSUI_ANSWER_PERMISSIONS=1`, every permission question
  is answered except a question to the person and plan approval.
- **Policy:** the gateway is the only MCP server, only managed hooks run,
  `WebSearch` and `WebFetch` are denied, and Claude Code's file memory stays
  empty.

The entrypoint hands the `JOSUI_*` settings to claude-agent under its own
names. See claude-agent's [hooks](https://github.com/crunchtools/claude-agent/blob/main/docs/hooks.md)
and [policy](https://github.com/crunchtools/claude-agent/blob/main/docs/policy.md) pages.

## Text session

With `JOSUI_MATRIX_HOMESERVER` set, `claude-agent run` holds a second Claude
Code session beside the Remote Control server, and its Matrix bridge carries
the operator's texts in and each turn's final answer out. On a crunchtools
host the homeserver is the agent's own, and Trentina's RCS bridge puts the
operator's texts there. The session is not on Remote Control: the operator
reaches it by text, and claude.ai shows only the server's sessions.

- Texts come only from `JOSUI_MATRIX_ALLOW`, in a one-to-one room. A text
  sent while a turn runs waits for it to end; `/stop [text]` interrupts.
  The phone shows a typing indicator for the length of a turn.
- Built-ins by text: `/clear`, `/compact`, `/model`, `/effort`, `/fast`,
  `/goal`, `/mcp reconnect`, `/reload-skills`, `/reload-plugins`; each texts
  back its result. Other `/names` run as skills; `!` is refused.
- `podman exec -it <service> claude-agent attach` shows the session live;
  `--rw` to answer its working-directory trust question.
- When either Claude process ends, the container ends and the unit restarts
  both.

See claude-agent's [supervisor](https://github.com/crunchtools/claude-agent/blob/main/docs/supervisor.md)
and [Matrix](https://github.com/crunchtools/claude-agent/blob/main/docs/matrix.md) pages.

## Run it

`deploy/josui.service.example` and `deploy/josui.env.example` show a server
deployment; the real unit and env file live in the host's private config repo. First start needs one thing done by hand, once, because the login
is personal and Remote Control refuses long-lived tokens:

```bash
podman exec -it <service> claude auth login    # claude.ai account; prints a URL, takes a code
```

Then restart the service. A new working directory also needs its trust
question answered once: `podman exec -it <service> claude-agent attach --rw`.

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRENTINA_MCP_URL`, `TRENTINA_MCP_TOKEN` | unset | The gateway. Without both, Josui starts with no MCP server and says so. |
| `HTTPS_PROXY`, `NO_PROXY` | unset | Client egress through the gateway. |
| `TRENTINA_GIT_URL` | unset | The gateway's git route (`http://<gateway>:8019/git`). Needs `TRENTINA_MCP_TOKEN`. |
| `JOSUI_WORKDIR` | `~/work` | Where Remote Control sessions start. Not a git checkout unless the Claude GitHub App can see its repository: session creation fails otherwise. |
| `JOSUI_NAME` | `josui` | Session name prefix shown in the app. |
| `JOSUI_CAPACITY` | `2` | Concurrent sessions. |
| `JOSUI_PERMISSION_MODE` | `acceptEdits` | Claude Code permission mode for new sessions. `bypassPermissions` asks nothing; hooks and deny rules still apply. The app sets its own mode on a session it opens, which overrides this. See the constitution's human-in-the-loop rule for the hook a deployment that takes that deviation uses there. |
| `JOSUI_ANSWER_PERMISSIONS` | unset | `1` turns on the prompts-off hook (the constitution's deviation). |
| `JOSUI_MATRIX_HOMESERVER` | unset | The agent's homeserver (`http://<private address>:6167`). Set, it turns on the text session. |
| `JOSUI_MATRIX_TOKEN_FILE` | `/etc/josui-matrix/token` | The agent account's access token, in a read-only mount. |
| `JOSUI_MATRIX_ALLOW` | unset | Comma list of the exact Matrix IDs whose messages reach the session. Empty lets nothing through. |
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
