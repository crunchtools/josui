# Breetai gateway quadlet

A copy of the live rootless quadlet for Breetai's local Trentina gateway, from
`~/.config/containers/systemd/trentina.container` on Breetai. Like
`deploy/dolza/` and `deploy/exedore/`, it follows the live file: change
Breetai, then copy here. The one exception is the address pin below, which
landed here first and is live once applied.

The gateway's address on the `crunchtools` network is pinned to `10.89.0.55`
(a868, matching Dolza). The vertex seats reach it as `http://trentina:8019`,
and `developer-entrypoint` resolves that name once at start and writes the
literal address into `gatehouse.env` (developer#89). Unpinned, every gateway
restart moved the address and left each seat's Gatehouse pointing at the old
one until the seat restarted too.

Applying the pin restarts the gateway, then the seats, so each one rewrites
`gatehouse.env` against the fixed address:

```bash
install -m0644 deploy/breetai/trentina.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user restart trentina.service
systemctl --user restart vertex@{1..4}.service
```

`10.89.0.55` must be free when the gateway starts, or it fails to start
rather than taking another address.
