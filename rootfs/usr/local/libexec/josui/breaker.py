#!/usr/bin/env python3
"""PreToolUse hook: the circuit breakers (Autonomous Agent profile IV).

Two limits per conversation, both hard:

- total tool calls (``JOSUI_MAX_TOOL_CALLS``)
- the same tool with the same input, back to back (``JOSUI_MAX_REPEAT``)

A tripped breaker denies the call and every call after it in that
conversation, and writes an audit record, so the stop is visible to the
operator and a new conversation is the only way on.

State is one small file per conversation under ``JOSUI_BREAKER_DIR``. Unlike
the audit hook, a failure here denies: a breaker that cannot count is open.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit  # noqa: E402

DEFAULT_MAX_TOOL_CALLS = 500
DEFAULT_MAX_REPEAT = 5


def limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def fingerprint(event: dict[str, Any]) -> str:
    body = json.dumps([event.get("tool_name"), event.get("tool_input")], sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def check(state: dict[str, Any], call: str, max_calls: int, max_repeat: int) -> str | None:
    """Count one call into ``state``; return why it is refused, or None."""
    if state.get("tripped"):
        return str(state["tripped"])
    state["calls"] = state.get("calls", 0) + 1
    state["repeat"] = state.get("repeat", 0) + 1 if state.get("last") == call else 1
    state["last"] = call
    if state["calls"] > max_calls:
        state["tripped"] = f"more than {max_calls} tool calls in one conversation"
    elif state["repeat"] > max_repeat:
        state["tripped"] = f"the same call {state['repeat']} times in a row"
    return state.get("tripped")


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Circuit breaker: {reason}. This conversation is halted. "
                    "Tell the operator what you were doing; do not retry."
                ),
            }
        },
        sys.stdout,
    )


def run(event: dict[str, Any], directory: str) -> str | None:
    session = "".join(c for c in str(event.get("session_id", "")) if c.isalnum() or c in "-_") or "unknown"
    state_dir = Path(directory)
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / f"{session}.json", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read()
        state = json.loads(raw) if raw else {}
        was_tripped = bool(state.get("tripped"))
        reason = check(
            state,
            fingerprint(event),
            limit("JOSUI_MAX_TOOL_CALLS", DEFAULT_MAX_TOOL_CALLS),
            limit("JOSUI_MAX_REPEAT", DEFAULT_MAX_REPEAT),
        )
        handle.seek(0)
        handle.truncate()
        json.dump(state, handle)
    if reason and not was_tripped:
        audit.append(audit.build_record(event, outcome=f"breaker_tripped: {reason}"))
    return reason


def main() -> int:
    try:
        reason = run(json.load(sys.stdin), os.environ.get("JOSUI_BREAKER_DIR", "/tmp/josui-breaker"))
    except Exception as exc:  # noqa: BLE001 - see module docstring
        reason = f"breaker state unavailable ({type(exc).__name__})"
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
