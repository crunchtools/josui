"""The entrypoint, run against a temporary home with a stand-in `claude`."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent.parent / "rootfs/usr/local/bin/josui-entrypoint"

FAKE_CLAUDE = """#!/bin/bash
printf '%s\\n' "$@" > "$HOME/claude-args"
env > "$HOME/claude-env"
pwd > "$HOME/claude-cwd"
"""


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class EntrypointTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.skills = self.root / "josui-skills"
        bin_dir = self.root / "bin"
        for path in (self.home, self.config / "hooks", self.skills / "skills/alpha", self.skills / "skills/beta", self.skills / "templates", bin_dir):
            path.mkdir(parents=True)
        (self.config / "CLAUDE.md").write_text("# Josui\n")
        (self.config / "settings.json").write_text("{}\n")
        (self.config / "hooks/deny-local-memory.sh").write_text("#!/bin/bash\n")
        claude = bin_dir / "claude"
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(0o755)
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "JOSUI_CONFIG_DIR": str(self.config),
            "JOSUI_SKILLS_DIR": str(self.skills),
            "JOSUI_AUDIT_DIR": str(self.root / "audit"),
            "TRENTINA_MCP_URL": "http://192.0.2.2:8019/gateway/agent1/mcp",
            "TRENTINA_MCP_TOKEN": "s3cret-token",
        }

    def run_entrypoint(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ENTRYPOINT), *args], env=env or self.env, capture_output=True, text=True, check=False
        )

    def test_assembles_the_home_and_serves_remote_control(self) -> None:
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        claude_dir = self.home / ".claude"
        self.assertEqual((claude_dir / "CLAUDE.md").read_text(), "# Josui\n")
        self.assertFalse((claude_dir / "settings.json").is_symlink())
        self.assertTrue((claude_dir / "hooks/deny-local-memory.sh").exists())
        self.assertEqual(sorted(p.name for p in (claude_dir / "skills").iterdir()), ["alpha", "beta"])
        self.assertEqual((claude_dir / "skills/alpha").resolve(), (self.skills / "skills/alpha").resolve())
        self.assertEqual((claude_dir / "templates").resolve(), (self.skills / "templates").resolve())
        args = (self.home / "claude-args").read_text().split()
        self.assertEqual(args[0], "remote-control")
        self.assertIn("--remote-control-session-name-prefix", args)
        self.assertEqual(args[-1], "josui")
        self.assertEqual((self.home / "claude-cwd").read_text().strip(), str(self.home / "work"))

    def test_gateway_is_merged_into_existing_state(self) -> None:
        state = self.home / ".claude.json"
        state.write_text(json.dumps({"hasCompletedOnboarding": True, "mcpServers": {"stale": {}}}))
        self.assertEqual(self.run_entrypoint().returncode, 0)
        data = json.loads(state.read_text())
        self.assertTrue(data["hasCompletedOnboarding"])
        self.assertEqual(list(data["mcpServers"]), ["trentina"])
        self.assertEqual(data["mcpServers"]["trentina"]["url"], self.env["TRENTINA_MCP_URL"])
        self.assertEqual(data["mcpServers"]["trentina"]["headers"]["Authorization"], "Bearer s3cret-token")
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)

    def test_token_is_not_handed_to_the_agent_process(self) -> None:
        self.run_entrypoint()
        self.assertNotIn("s3cret-token", (self.home / "claude-env").read_text())

    def test_starts_without_a_gateway_and_says_so(self) -> None:
        env = {k: v for k, v in self.env.items() if not k.startswith("TRENTINA_")}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("starting without a gateway", result.stderr)
        self.assertFalse((self.home / ".claude.json").exists())

    def test_removed_skill_disappears_on_restart(self) -> None:
        self.run_entrypoint()
        shutil.rmtree(self.skills / "skills/beta")
        self.run_entrypoint()
        self.assertEqual([p.name for p in (self.home / ".claude/skills").iterdir()], ["alpha"])

    def test_arguments_run_instead_of_remote_control(self) -> None:
        result = self.run_entrypoint("claude", "auth", "login")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / "claude-args").read_text().split(), ["auth", "login"])

    def test_old_audit_files_are_dropped(self) -> None:
        audit = self.root / "audit"
        audit.mkdir()
        old, new = audit / "audit-2020-01-01.jsonl", audit / "audit-2999-01-01.jsonl"
        for path in (old, new):
            path.write_text("{}\n")
        os.utime(old, (0, 0))
        self.run_entrypoint()
        self.assertEqual([p.name for p in audit.iterdir()], [new.name])


if __name__ == "__main__":
    unittest.main()
