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

    def session_env(self) -> dict[str, str]:
        agent = self.root / "bin/claude-agent"
        agent.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/agent-args"\nenv > "$HOME/agent-env"\nexit 7\n'
        )
        agent.chmod(0o755)
        claude = self.root / "bin/claude"
        claude.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/claude-args"\n'
            'env > "$HOME/claude-env"\nexec sleep 30\n'
        )
        return {
            **self.env,
            "JOSUI_MATRIX_HOMESERVER": "http://192.0.2.3:6167",
            "JOSUI_MATRIX_TOKEN_FILE": "/etc/josui-matrix/token",
            "JOSUI_MATRIX_ALLOW": "@alice:example.org",
            "JOSUI_PERMISSION_MODE": "bypassPermissions",
        }

    def agent_env(self) -> dict[str, str]:
        lines = (self.home / "agent-env").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def test_text_session_runs_beside_remote_control_and_either_ending_stops_both(self) -> None:
        result = self.run_entrypoint(env=self.session_env())
        # The stand-in claude-agent exits 7 at once; the server is stopped too.
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("a supervised process ended (status 7)", result.stderr)
        self.assertEqual((self.home / "claude-args").read_text().split()[0], "remote-control")
        self.assertEqual(
            (self.home / "agent-args").read_text().split(),
            ["run", "--", "--permission-mode", "bypassPermissions"],
        )

    def test_the_bridge_gets_the_matrix_settings_under_its_own_names(self) -> None:
        self.run_entrypoint(env=self.session_env())
        env = self.agent_env()
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_HOMESERVER"], "http://192.0.2.3:6167")
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_TOKEN_FILE"], "/etc/josui-matrix/token")
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_ALLOW"], "@alice:example.org")
        # The old channel's sync position, so texts sent across the upgrade arrive.
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_STATE"], str(self.home / ".config/josui/matrix-sync"))
        self.assertNotIn("s3cret-token", (self.home / "agent-env").read_text())

    def test_josui_settings_reach_claude_agent(self) -> None:
        env = {
            **self.session_env(),
            "JOSUI_ANSWER_PERMISSIONS": "1",
            "JOSUI_MAX_TOOL_CALLS": "900",
            "JOSUI_MAX_REPEAT": "7",
        }
        self.run_entrypoint(env=env)
        agent = self.agent_env()
        self.assertEqual(
            (
                agent["CLAUDE_AGENT_ANSWER_PERMISSIONS"],
                agent["CLAUDE_AGENT_MAX_TOOL_CALLS"],
                agent["CLAUDE_AGENT_MAX_REPEAT"],
            ),
            ("1", "900", "7"),
        )

    def test_text_session_skips_onboarding_but_not_trust(self) -> None:
        self.run_entrypoint(env=self.session_env())
        state = json.loads((self.home / ".claude.json").read_text())
        self.assertTrue(state["hasCompletedOnboarding"])
        # Trusting the working directory stays the operator's call.
        self.assertNotIn("projects", state)
        self.assertIn("trentina", state["mcpServers"])

    # The client egress watchdog. The proxy in these tests is 192.0.2.2:8021,
    # which /proc/net/tcp writes as 020200C0:1F55.
    PROXY = "http://192.0.2.2:8021"
    PROXY_HEX = "020200C0:1F55"
    NET_TCP_HEADER = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    )

    def net_tcp(self, *rows: str) -> str:
        path = self.root / "net-tcp"
        path.write_text(self.NET_TCP_HEADER + "".join(f"   0: 0100007F:A1B2 {row}\n" for row in rows))
        return str(path)

    def claude_that_runs_for(self, seconds: int) -> None:
        claude = self.root / "bin/claude"
        claude.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/claude-args"\nexec sleep {seconds}\n')
        claude.chmod(0o755)

    def test_debug_file_is_asked_for_and_emptied_when_there_is_an_audit_dir(self) -> None:
        audit = self.root / "audit"
        audit.mkdir()
        stale = audit / "remote-control.log"
        stale.write_text("from the last container\n")
        result = self.run_entrypoint(env={**self.env, "CLAUDE_AGENT_AUDIT_DIR": str(audit)})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.home / "claude-args").read_text().split()
        self.assertEqual(args[args.index("--debug-file") + 1], str(stale))
        # The session name prefix stays last, and the stale log is gone.
        self.assertEqual(args[-1], "josui")
        self.assertEqual(stale.read_text(), "")

    def test_no_debug_file_when_there_is_nowhere_to_put_it(self) -> None:
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertNotIn("--debug-file", (self.home / "claude-args").read_text())

    def test_nothing_connected_to_the_egress_stops_the_container(self) -> None:
        self.claude_that_runs_for(30)
        env = {
            **self.env,
            "HTTPS_PROXY": self.PROXY,
            "JOSUI_NET_TCP": self.net_tcp("020D590A:1F53 01"),  # the gateway's MCP port, not the egress
            "JOSUI_WATCHDOG_INTERVAL": "1",
            "JOSUI_WATCHDOG_GRACE": "1",
        }
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Remote Control is not connected", result.stderr)

    def test_an_established_egress_connection_keeps_the_container_running(self) -> None:
        self.claude_that_runs_for(3)
        env = {
            **self.env,
            "HTTPS_PROXY": self.PROXY,
            "JOSUI_NET_TCP": self.net_tcp(f"{self.PROXY_HEX} 01"),
            "JOSUI_WATCHDOG_INTERVAL": "1",
            "JOSUI_WATCHDOG_GRACE": "1",
        }
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("client egress", result.stderr)

    def test_a_connection_that_is_not_established_does_not_count(self) -> None:
        self.claude_that_runs_for(30)
        env = {
            **self.env,
            "HTTPS_PROXY": self.PROXY,
            "JOSUI_NET_TCP": self.net_tcp(f"{self.PROXY_HEX} 06"),  # TIME_WAIT
            "JOSUI_WATCHDOG_INTERVAL": "1",
            "JOSUI_WATCHDOG_GRACE": "1",
        }
        self.assertEqual(self.run_entrypoint(env=env).returncode, 1)

    def test_without_a_proxy_there_is_no_watchdog(self) -> None:
        self.claude_that_runs_for(2)
        env = {
            **self.env,
            "JOSUI_NET_TCP": self.net_tcp(),
            "JOSUI_WATCHDOG_INTERVAL": "1",
            "JOSUI_WATCHDOG_GRACE": "1",
        }
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("client egress", result.stderr)

    def test_a_proxy_that_is_not_an_address_is_reported_and_skipped(self) -> None:
        self.claude_that_runs_for(2)
        env = {**self.env, "HTTPS_PROXY": "http://gateway.internal:8021", "JOSUI_WATCHDOG_INTERVAL": "1"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a plain address", result.stderr)

    def test_without_a_homeserver_there_is_no_text_session(self) -> None:
        agent = self.root / "bin/claude-agent"
        agent.write_text('#!/bin/bash\ntouch "$HOME/agent-ran"\n')
        agent.chmod(0o755)
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.home / "agent-ran").exists())


if __name__ == "__main__":
    unittest.main()
