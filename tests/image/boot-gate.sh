#!/usr/bin/env bash
# Boot gate for the Josui image: run the real entrypoint in the image the way
# production does (read-only root, no capabilities, HOME on a writable volume)
# and prove what production needs is there. Usage: IMAGE=<ref> boot-gate.sh
#
# The entrypoint given arguments assembles the Claude home, writes the gateway
# into Claude Code's state (python3), then execs the arguments. So one run
# covers: entrypoint parses and runs, python3 and jq are present, the claude
# and claude-agent binaries load, and the gateway state is written.
set -euo pipefail

image=${IMAGE:-${1:-}}
[ -n "$image" ] || { echo "boot-gate: IMAGE is not set" >&2; exit 2; }

run() {
  podman run --rm --read-only --tmpfs /tmp:rw,nosuid,size=64m \
    --tmpfs /home/josui:rw,mode=1777 \
    --cap-drop=ALL --security-opt no-new-privileges \
    -e TRENTINA_MCP_URL=http://192.0.2.2:8019/gateway/agent1/mcp \
    -e TRENTINA_MCP_TOKEN=dummy-token \
    "$image" "$@"
}

out=$(run bash -c '
  set -e
  claude --version
  claude-agent --version
  command -v git jq python3 node >/dev/null
  python3 -c "import json,os;s=json.load(open(os.environ[\"HOME\"]+\"/.claude.json\"));assert s[\"mcpServers\"][\"trentina\"][\"url\"].endswith(\"/mcp\")"
  test -z "${TRENTINA_MCP_TOKEN:-}"
  echo boot-gate-ok
' 2>&1) || { echo "$out" >&2; echo "boot-gate: FAIL" >&2; exit 1; }
echo "$out"
echo "$out" | grep -qx boot-gate-ok || { echo "boot-gate: FAIL (no ok marker)" >&2; exit 1; }
echo "boot-gate: PASS"
