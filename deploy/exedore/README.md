# Exedore seat quadlets

Copies of the live rootful quadlets for the Exedore Max seats (Exedore 1-4 -
Max), from `/etc/containers/systemd/` on Exedore. Like `deploy/dolza/`, these
follow the live files rather than the other way round: change Exedore, then
copy here.

Each seat differs only in its number, its UID/GID map and its secret names.
Container storage for nested podman lives on the encrypted `onsite` disk at
`/var/mnt/onsite/seats/maxN/containers` (a325p, 2026-11-20), so each unit has
`RequiresMountsFor=/var/mnt/onsite` and does not start against an empty mount
point. Secrets are podman secrets on the host, referenced by name only.
