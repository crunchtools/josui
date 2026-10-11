"""The audit and circuit-breaker hooks, run the way Claude Code runs them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "rootfs/usr/local/libexec/josui"
sys.path.insert(0, str(HOOKS))

import audit  # noqa: E402
import breaker  # noqa: E402


def run_hook(name: str, event: object, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    payload = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=payload,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=False,
    )


class AuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {"JOSUI_AUDIT_DIR": self.dir.name}

    def records(self) -> list[dict[str, object]]:
        return [json.loads(line) for path in sorted(Path(self.dir.name).glob("audit-*.jsonl")) for line in path.read_text().splitlines()]

    def test_record_has_the_profile_fields(self) -> None:
        event = {
            "session_id": "abc",
            "tool_name": "mcp__trentina__memory_search",
            "tool_input": {"query": "rotv"},
            "tool_response": {"content": "..."},
        }
        self.assertEqual(run_hook("audit.py", event, self.env).returncode, 0)
        (record,) = self.records()
        self.assertEqual(
            set(record), {"timestamp", "tool", "server", "args", "result_summary", "token_cost", "conversation_id"}
        )
        self.assertEqual((record["server"], record["tool"]), ("trentina", "memory_search"))
        self.assertEqual(record["result_summary"], "success")
        self.assertEqual(record["conversation_id"], "abc")

    def test_builtin_tool_and_error_result(self) -> None:
        event = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "false"}, "tool_response": {"is_error": True}}
        run_hook("audit.py", event, self.env)
        (record,) = self.records()
        self.assertEqual((record["server"], record["tool"], record["result_summary"]), ("claude-code", "Bash", "error"))

    def test_credentials_never_reach_the_log(self) -> None:
        event = {
            "session_id": "s",
            "tool_name": "Bash",
            "tool_input": {
                "command": "curl -H 'Authorization: Bearer abcdefghijklmnop123456' https://example.com",
                "api_key": "plain-value",
                "nested": {"password": "hunter2", "note": "ghp_" + "a" * 36},
            },
        }
        run_hook("audit.py", event, self.env)
        text = "".join(path.read_text() for path in Path(self.dir.name).glob("*.jsonl"))
        for secret in ("abcdefghijklmnop123456", "plain-value", "hunter2", "ghp_" + "a" * 36):
            self.assertNotIn(secret, text)
        self.assertIn("Bearer [REDACTED]", text)

    def test_long_values_are_cut(self) -> None:
        self.assertEqual(len(audit.redact("x" * 5000)), audit.MAX_VALUE_CHARS + len("... [5000 chars]"))

    def test_bad_input_does_not_fail_the_agent(self) -> None:
        result = run_hook("audit.py", "not json", self.env)
        self.assertEqual(result.returncode, 0)
        self.assertIn("josui audit hook", result.stderr)
        self.assertEqual(self.records(), [])


class BreakerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {
            "JOSUI_BREAKER_DIR": str(Path(self.dir.name) / "breaker"),
            "JOSUI_AUDIT_DIR": str(Path(self.dir.name) / "audit"),
            "JOSUI_MAX_TOOL_CALLS": "6",
            "JOSUI_MAX_REPEAT": "3",
        }

    def call(self, command: str, session: str = "s1") -> dict[str, object] | None:
        event = {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command}}
        result = run_hook("breaker.py", event, self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def assertDenied(self, output: dict[str, object] | None, fragment: str) -> None:
        self.assertIsNotNone(output)
        decision = output["hookSpecificOutput"]  # type: ignore[index]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn(fragment, decision["permissionDecisionReason"])

    def test_different_calls_pass_until_the_total(self) -> None:
        for n in range(6):
            self.assertIsNone(self.call(f"echo {n}"))
        self.assertDenied(self.call("echo 6"), "more than 6 tool calls")

    def test_same_call_in_a_row_trips(self) -> None:
        for _ in range(3):
            self.assertIsNone(self.call("ls"))
        self.assertDenied(self.call("ls"), "4 times in a row")

    def test_a_different_call_resets_the_run(self) -> None:
        for command in ("ls", "ls", "ls", "pwd", "ls", "ls"):
            self.assertIsNone(self.call(command))

    def test_tripped_stays_tripped_and_is_audited_once(self) -> None:
        for _ in range(4):
            self.call("ls")
        self.assertDenied(self.call("something else"), "4 times in a row")
        lines = [
            json.loads(line)
            for path in (Path(self.dir.name) / "audit").glob("*.jsonl")
            for line in path.read_text().splitlines()
        ]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0]["result_summary"].startswith("breaker_tripped"))

    def test_conversations_are_counted_apart(self) -> None:
        for _ in range(4):
            self.call("ls", session="a")
        self.assertIsNone(self.call("ls", session="b"))

    def test_unreadable_state_denies(self) -> None:
        state = Path(self.env["JOSUI_BREAKER_DIR"])
        state.mkdir(parents=True)
        (state / "s1.json").write_text("{not json")
        self.assertDenied(self.call("ls"), "breaker state unavailable")

    def test_session_id_cannot_leave_the_state_directory(self) -> None:
        self.call("ls", session="../../escape")
        self.assertEqual([p.name for p in Path(self.env["JOSUI_BREAKER_DIR"]).iterdir()], ["escape.json"])

    def test_check_is_pure_counting(self) -> None:
        state: dict[str, object] = {}
        self.assertIsNone(breaker.check(state, "a", 10, 2))
        self.assertIsNone(breaker.check(state, "a", 10, 2))
        self.assertIsNotNone(breaker.check(state, "a", 10, 2))


if __name__ == "__main__":
    unittest.main()
