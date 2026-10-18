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
echo "status screen"
echo "an error" >&2
env > "$HOME/claude-env"
pwd > "$HOME/claude-cwd"
"""


class EntrypointTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.skills = self.root / "josui-skills"
        bin_dir = self.root / "bin"
        for path in (
            self.home,
            self.config / "hooks",
            self.skills / "skills/alpha",
            self.skills / "skills/beta",
            self.skills / "templates",
            bin_dir,
        ):
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
            "TRENTINA_GIT_URL": "http://192.0.2.2:8019/git/",
        }

    def run_entrypoint(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ENTRYPOINT), *args], env=env or self.env, capture_output=True, text=True, check=False
        )

    def test_assembles_the_home_and_serves_remote_control(self) -> None:
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        claude_dir = self.home / ".claude"
        self.assertEqual((claude_dir / "CLAUDE.md").read_text(), "# Josui\n")
        self.assertFalse((claude_dir / "settings.json").is_symlink())
        # Identity hooks are not copied: only the image's managed hooks run.
        self.assertFalse((claude_dir / "hooks/deny-local-memory.sh").exists())
        self.assertEqual(sorted(p.name for p in (claude_dir / "skills").iterdir()), ["alpha", "beta"])
        self.assertEqual((claude_dir / "skills/alpha").resolve(), (self.skills / "skills/alpha").resolve())
        self.assertEqual((claude_dir / "templates").resolve(), (self.skills / "templates").resolve())
        args = (self.home / "claude-args").read_text().split()
        self.assertEqual(args[0], "remote-control")
        self.assertIn("--remote-control-session-name-prefix", args)
        self.assertEqual(args[-1], "josui")
        self.assertEqual((self.home / "claude-cwd").read_text().strip(), str(self.home / "work"))

    def test_remote_control_status_output_is_dropped_and_errors_are_kept(self) -> None:
        result = self.run_entrypoint()
        self.assertNotIn("status screen", result.stdout)
        self.assertIn("an error", result.stderr)

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

    def git(self, *args: str) -> str:
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"]}
        return subprocess.run(
            ["git", "config", *args], env=env, capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_git_goes_to_the_gateway_route_with_the_gateway_token(self) -> None:
        self.run_entrypoint()
        self.run_entrypoint()  # a restart does not add a second include
        gitconf = self.home / ".config/josui/gateway.gitconfig"
        self.assertEqual(stat.S_IMODE(gitconf.stat().st_mode), 0o600)
        self.assertEqual(self.git("--global", "--get-all", "include.path"), str(gitconf))
        self.assertEqual(
            self.git("--get", "url.http://192.0.2.2:8019/git/github/.insteadOf"), "https://github.com/"
        )
        self.assertEqual(
            self.git("--get", "http.http://192.0.2.2:8019/git/.extraHeader"),
            "Authorization: Bearer s3cret-token",
        )

    def test_git_url_that_is_not_a_plain_url_is_ignored(self) -> None:
        env = {**self.env, "TRENTINA_GIT_URL": 'http://x"]\n[credential]\n\thelper = !touch /tmp/pwned'}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a plain http(s) URL", result.stderr)
        self.assertFalse((self.home / ".config/josui/gateway.gitconfig").exists())

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

    def channel_env(self) -> dict[str, str]:
        libexec = self.root / "libexec"
        libexec.mkdir()
        session = libexec / "channel-session"
        session.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/session-args"\n'
                           'env > "$HOME/session-env"\nexit 7\n')
        session.chmod(0o755)
        claude = self.root / "bin/claude"
        claude.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/claude-args"\n'
                          'env > "$HOME/claude-env"\nexec sleep 30\n')
        return {
            **self.env,
            "JOSUI_LIBEXEC": str(libexec),
            "JOSUI_MATRIX_HOMESERVER": "http://192.0.2.3:6167",
            "JOSUI_MATRIX_TOKEN_FILE": "/etc/josui-matrix/token",
            "JOSUI_MATRIX_ALLOW": "@rcs_owner:josui.local",
            "JOSUI_PERMISSION_MODE": "bypassPermissions",
        }

    def test_channel_runs_beside_remote_control_and_either_ending_stops_both(self) -> None:
        result = self.run_entrypoint(env=self.channel_env())
        # The stand-in session exits 7 at once; the server is stopped too.
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("a claude process ended (status 7)", result.stderr)
        self.assertEqual((self.home / "claude-args").read_text().split()[0], "remote-control")
        config = self.home / ".config/josui/channel-mcp.json"
        self.assertEqual((self.home / "session-args").read_text().split(),
                         [str(config), "josui-rcs", "bypassPermissions"])
        server = json.loads(config.read_text())["mcpServers"]["matrix"]
        self.assertEqual([server["command"], *server["args"]],
                         ["python3", "/usr/local/libexec/josui/matrix-channel.py"])
        self.assertEqual(server["env"]["JOSUI_MATRIX_ALLOW"], "@rcs_owner:josui.local")
        self.assertEqual(server["env"]["JOSUI_MATRIX_TOKEN_FILE"], "/etc/josui-matrix/token")

    def test_channel_session_skips_onboarding_but_not_trust(self) -> None:
        self.run_entrypoint(env=self.channel_env())
        state = json.loads((self.home / ".claude.json").read_text())
        self.assertTrue(state["hasCompletedOnboarding"])
        # Trusting the working directory stays the operator's call.
        self.assertNotIn("projects", state)
        self.assertIn("trentina", state["mcpServers"])

    def test_telemetry_is_off_for_the_server_but_not_the_channel(self) -> None:
        self.run_entrypoint(env=self.channel_env())
        self.assertIn("DISABLE_TELEMETRY=1", (self.home / "claude-env").read_text().splitlines())
        self.assertNotIn("DISABLE_TELEMETRY", (self.home / "session-env").read_text())

    def test_without_a_homeserver_there_is_no_channel(self) -> None:
        self.run_entrypoint()
        self.assertFalse((self.home / ".config/josui/channel-mcp.json").exists())

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
