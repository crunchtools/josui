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

- **Circuit breakers** (`hooks.py breaker`, PreToolUse): `JOSUI_MAX_TOOL_CALLS` per
  conversation (default 500) and `JOSUI_MAX_REPEAT` identical calls in a row
  (default 5). A tripped conversation stays stopped.
- **Audit** (`hooks.py audit`, PostToolUse): timestamp, tool, server, redacted
  arguments, result and conversation, in `audit-YYYY-MM-DD.jsonl`, kept 120 days.
- **Local memory guard** (`deny-local-memory.sh`, PreToolUse): refuses writes into
  Claude Code's file-based memory; the gateway's memory graph is the only store.
- **Prompts off** (`allow-hook.sh`, PreToolUse): with `JOSUI_ANSWER_PERMISSIONS=1`
  it answers every permission question except a question to the person and
  plan approval, in whatever mode the app picked. A deny from any hook wins.
- All of these are in `/etc/claude-code/managed-settings.json`, which also sets
  `allowManagedHooksOnly`: hooks from the mounted `settings.json`, from this
  home, or from a checkout's `.claude/settings.json` do not run. `WebSearch`
  and `WebFetch` are denied there too: they would bring web pages back past the
  gateway's judging.

## Matrix channel

With `JOSUI_MATRIX_HOMESERVER` set, a second Claude process runs beside the
Remote Control server: an interactive session with a
[channel](https://code.claude.com/docs/en/channels-reference) that carries a
Matrix DM into it. On a crunchtools host the homeserver is the agent's own,
and Trentina's RCS bridge puts the operator's texts there, so a text from the
phone lands in that session and Claude answers with the channel's `reply`
tool. The session is not on Remote Control: the operator reaches it by text,
and claude.ai shows only the server's sessions.

- `matrix-channel.py` is the channel, a stdio MCP server using only the
  standard library. It lets through messages, and joins invites, only from the
  senders in `JOSUI_MATRIX_ALLOW`, checked on the sender, never on the room.
  It replies only into rooms those senders wrote from.
- `channel-session` (expect) gives the session the terminal Claude Code needs
  for a channel, and answers the warning that
  `--dangerously-load-development-channels` shows at every start. A custom
  channel is not on Anthropic's allowlist, so it loads only with that flag.
  It logs to stderr each dialog it answers, and ends startup after 60
  seconds with no dialog. It never answers the working
  directory's trust question: if that is still open, it exits and names the
  one-time command (see [Run it](#run-it)).
- Managed settings admit the channel by its exact command. When either Claude
  process ends, the container ends and the unit restarts both.

### Commands by text

Only the input box runs a built-in command; a text reaches Claude as a
channel event. So the channel takes these itself, writes each to a control
FIFO (`~/.config/josui/control`, mode 0600), and `channel-session` types it
into the session after checking it against the same list:

`/clear [name]`, `/compact [instructions]`, `/model <alias or id>`,
`/effort low|medium|high|xhigh|max|auto`, `/fast on|off`,
`/goal <condition>|clear`, `/mcp reconnect <server>|all`, `/reload-skills`,
`/reload-plugins`.

The channel acks each one ("Sent /clear, result to follow"), since a cleared
session has no turn to answer with, and answers a bad argument with its usage
and `/help` with the list. A command sent mid-reply runs when the reply ends.
Then the channel texts the result, which it reads from the channel session's
own transcript in `~/.claude/projects` (its parent's session ID, from
`~/.claude/sessions/<pid>.json`): what the command printed, or for `/compact`
the token counts and the summary, trimmed to 3,000 characters. With no result
in 15 minutes it says so. `/model` and
`/effort` also save the default in `settings.json`. Anything else, skills
included, goes to the session unchanged. Left out: commands that open a
dialog or picker, `/plan`, whose approval would wait for nobody, and `/exit`,
which would end the container and the Remote Control server with it.

## Run it

`deploy/josui.service.example` and `deploy/josui.env.example` show a server
deployment; the real unit and env file live in the host's private config repo. First start needs one thing done by hand, once, because the login
is personal and Remote Control refuses long-lived tokens:

```bash
podman exec -it <service> claude auth login    # claude.ai account; prints a URL, takes a code
```

Then restart the service. A new working directory also needs its trust
question answered once: `podman exec -it -w <dir> <service> claude`.

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
| `JOSUI_MATRIX_HOMESERVER` | unset | The agent's homeserver (`http://<address>:6167`). Set, it turns on the Matrix channel. |
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
