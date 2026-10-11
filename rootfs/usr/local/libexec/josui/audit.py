#!/usr/bin/env python3
"""PostToolUse hook: one JSON line per tool call, on a persistent volume.

The record is the one the Autonomous Agent profile asks for (IV, audit
logging). Argument values are cut short and anything that looks like a
credential is replaced, because the log outlives the session and is read by
people who should not learn a token from it.

A hook that fails must not take the agent down with it, so every error ends
in exit 0 with a line on stderr.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_VALUE_CHARS = 200
SECRET_KEY = re.compile(r"token|secret|passw|api[_-]?key|authorization|credential|cookie", re.I)
SECRET_VALUE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"
    r"|\b(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"
)


def redact(value: Any, key: str = "") -> Any:
    """Return ``value`` safe to write down: secrets replaced, long text cut."""
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        if SECRET_KEY.search(key):
            return "[REDACTED]"
        text = SECRET_VALUE.sub(lambda m: (m.group(1) or "") + "[REDACTED]", value)
        if len(text) > MAX_VALUE_CHARS:
            return f"{text[:MAX_VALUE_CHARS]}... [{len(text)} chars]"
        return text
    return value


def split_tool(name: str) -> tuple[str, str]:
    """Split ``mcp__server__tool`` into (server, tool); built-ins have no server."""
    if name.startswith("mcp__"):
        _, _, rest = name.partition("mcp__")
        server, _, tool = rest.partition("__")
        return server, tool or name
    return "claude-code", name


def result_summary(response: Any) -> str:
    if isinstance(response, dict) and (response.get("is_error") or response.get("isError") or "error" in response):
        return "error"
    return "success"


def build_record(event: dict[str, Any], outcome: str | None = None) -> dict[str, Any]:
    server, tool = split_tool(str(event.get("tool_name", "")))
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": tool,
        "server": server,
        "args": redact(event.get("tool_input", {})),
        "result_summary": outcome or result_summary(event.get("tool_response")),
        "token_cost": None,
        "conversation_id": event.get("session_id", ""),
    }


def append(record: dict[str, Any], directory: str | None = None) -> None:
    audit_dir = Path(directory or os.environ.get("JOSUI_AUDIT_DIR", "/var/log/josui"))
    audit_dir.mkdir(parents=True, exist_ok=True)
    day = record["timestamp"][:10]
    line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
    # O_APPEND keeps concurrent sessions' lines whole.
    fd = os.open(audit_dir / f"audit-{day}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def main() -> int:
    try:
        append(build_record(json.load(sys.stdin)))
    except Exception as exc:  # noqa: BLE001 - see module docstring
        print(f"josui audit hook: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
