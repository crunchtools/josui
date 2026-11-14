# Dolza: the agents pod

Copies of the live quadlets in `~/.config/containers/systemd/` on Dolza. The
live files are the ones that run; when one changes, update the copy here too.

- `agents.pod`: one pod holding Josui Prime and the Vertex workers. Claude Code
  finds its peers through `<pid>.json` files in `~/.claude/sessions` and
  messages them over sockets in `$XDG_RUNTIME_DIR/cc-socks`. Three things
  follow from that:
  - The members share one PID namespace. With separate namespaces every
    container's Claude is pid 1, and they overwrite one another's `1.json`.
  - `~/srv/agents/sessions` and `~/srv/agents/run/cc-socks` are bind-mounted
    into every member at the same paths, along with `/etc/machine-id`.
  - The pod's `ExecStartPre` clears both directories, because pids start again
    from 1 when the pod does.
- `josui.container`: Josui Prime with `JOSUI_MODE=interactive`. Attach with
  `podman exec -it josui tmux attach -t prime`. It drives the host podman
  socket.
- `vertex@.container`: Vertex worker N (`vertex@1` … `vertex@4`). Attach with
  `podman exec -it vertexN tmux attach -t dev`. It needs
  `~/srv/vertexN/data/state/.claude` to exist and be owned by the host user
  before its first start. Otherwise podman creates that directory as root when
  it mounts the sessions directory inside it.

Start the pod before its members. Starting a member with the pod down fails as
a dependency.
