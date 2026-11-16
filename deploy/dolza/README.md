# Dolza: the agents pod

Copies of the live quadlets in `~/.config/containers/systemd/` on Dolza. The
live files are the ones that run; when one changes, update the copy here too.

- `agents.pod`: one pod holding Josui Prime and the Vertex workers. Claude Code
  finds its peers through `<pid>.json` files in `~/.claude/sessions` and
  messages them over sockets in `$XDG_RUNTIME_DIR/cc-socks`. Three things
  follow from that:
  - The members share one PID namespace. With separate namespaces every
    container's Claude is pid 1, and they overwrite one another's `1.json`.
  - `~/Projects/dolza-services/agents/sessions` and `~/Projects/dolza-services/agents/run/cc-socks` are bind-mounted
    into every member at the same paths, along with `/etc/machine-id`.
  - The pod's `ExecStartPre` clears both directories, because pids start again
    from 1 when the pod does.
- `josui.container`: Josui Prime, a supervised worker (`JOSUI_MODE=worker`
  with `JOSUI_MATRIX_HOMESERVER`): `claude-agent run` holds its one
  conversation, reached by text, Remote Control and voice. Attach with
  `podman exec -it josui claude-agent attach --rw` from outside tmux. SELinux labels stay on: its
  own mounts are private (`:Z`), and the two shared directories carry the
  shared label (`:z`). Measured 2026-11-14: Prime, confined as `container_t`,
  and the label-disabled Vertex workers connect to each other's sockets in both
  directions. There is no container socket in it (constitution). Prime directs
  the workers by message, and systemd keeps them running.
- `vertex@.container`: Vertex worker N (`vertex@1` … `vertex@4`). Attach with
  `podman exec -it vertexN tmux attach -t dev`. It needs
  `~/Projects/dolza-services/vertexN/data/state/.claude` to exist and be owned by the host user
  before its first start. Otherwise podman creates that directory as root when
  it mounts the sessions directory inside it.
- `dmax@.container`: Max worker N (`dmax@1` … `dmax@4`, named `Dolza N - Max`).
  The same worker as Vertex, plus `JOSUI_RC=1`, which puts its conversation on
  Remote Control too. Attach with `podman exec -it dmaxN tmux attach -t dev`. It needs
  `~/Projects/dolza-services/dmaxN/data/state/.claude` to exist and be owned by the
  host user before its first start, for the same reason as Vertex.

Start the pod before its members. Starting a member with the pod down fails as
a dependency.
