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
  `podman exec -it josui claude-supervisor attach --rw` from outside tmux. SELinux labels stay on: its
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
- `trentina.container`: Dolza's own gateway, which every member reaches as
  `trentina` on the `crunchtools` network. Its address is pinned
  (`ip=10.89.0.55`) because `gatehouse.env` names it as a literal
  (crunchtools/claude#89), and an address that moves on a gateway restart
  breaks every seat's pre-push Gatehouse. The network was created by hand, not
  by a quadlet, so its dynamic range lives in podman's
  `~/.local/share/containers/storage/networks/crunchtools.json`:
  `lease_range` 10.89.0.100-10.89.0.254. Pinned addresses (.20, .55) sit below
  it, so no other container can be handed one. Recreating the network must keep
  it (`podman network create --ip-range 10.89.0.100-10.89.0.254 ...`). Its
  `profiles.yaml` is live state the gateway's config tool rewrites, so it is
  not copied here. The seats' own profile, `dolza-vertex`, holds no
  `openrouter-gatehouse` key: a Vertex seat holds Red Hat content, and with
  that key its token would carry anything on the seat to OpenRouter.
- `vertex@.container.d/gatehouse.conf`: the Vertex seats' `gatehouse.env`,
  Gatehouse's own gateway token (profile `dolza-gatehouse`) instead of the
  seat's. That profile has no tools, no `git` route and no `/proxy/llm` route;
  its one way out is the guarded egress route
  `/proxy/http/openrouter-gatehouse`, under block, with a semantic request
  guard that refuses Red Hat content. The token is a file any process on the
  seat can read, so it is not a boundary by itself; the gateway guard is.
  Delivered from OpenBao `kv dolza/gatehouse-vertex/gatehouse.env` by
  `srv-secrets@gatehouse-vertex`, mounted at `/etc/josui/mcp-env/gatehouse.env`
  (developer-entrypoint prefers a mounted file); the mountpoint
  `~/Projects/dolza-services/agents/developer-identity/mcp-env` must exist,
  empty. Known gap (a813): Trentina 6.25.0's L2 classifier flags Gatehouse's
  own review prompts, so under block this route currently refuses every
  review and Vertex-seat pushes stop at the Gatehouse gate.
- `pcloud.conf`, with `tools/pcloud-seats/pcloud-seats@.service` (user unit,
  `~/.config/systemd/user/`, enabled as `pcloud-seats@Downloads`, `pcloud-seats@Documents` and
  `pcloud-seats@Autosync-Screenshots`), the drop-in on each container: those three pCloud folders, live and
  read-write, at their host paths plus `~/Downloads`, `~/Documents` and
  `~/Pictures/Screenshots` inside every seat; nothing else of the drive.
  pCloud's own FUSE mount has no `allow_other`, so bindfs re-exports each
  folder with it; root steps on the host:
  `user_allow_other` in `/etc/fuse.conf`, `setsebool -P virt_use_fusefs on`,
  and the `bindfs` package. The seats bind the plain directory
  `~/.pcloud-seats` (the bindfs mounts inside it) with `rslave`, which is what
  lets a bindfs restart reach running seats: a bind of the FUSE mount itself
  goes dead when bindfs does. bindfs does not exit when pCloud quits or
  restarts under it, so the script watches pCloud's mount and exits for a
  rebind when it goes away or changes device.

Start the pod before its members. Starting a member with the pod down fails as
a dependency.
