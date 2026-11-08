"""The entrypoint, run against a temporary home with a stand-in `claude`."""

from __future__ import annotations

import json
import os
import shutil
import socket as socketmod
import stat
import subprocess
import tempfile
import threading
import time
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

    def ready_env(self, socket_path: str, timeout: str = "3") -> dict[str, str]:
        """A session env whose agent stays alive, so readiness can be observed.

        `session_env`'s stand-in agent exits at once, which is right for the
        supervisor tests and wrong here: a dead child short-circuits the
        readiness wait by design.
        """
        # session_env() rewrites both stand-ins, so it has to run FIRST or it
        # clobbers the long-lived ones below with its exit-7 agent.
        env = self.session_env()
        agent = self.root / "bin/claude-agent"
        agent.write_text("#!/bin/bash\nexec sleep 5\n")
        agent.chmod(0o755)
        claude = self.root / "bin/claude"
        claude.write_text("#!/bin/bash\nexec sleep 5\n")
        claude.chmod(0o755)
        return {**env, "JOSUI_AGENT_SOCKET": socket_path, "JOSUI_READY_TIMEOUT": timeout}

    def test_readiness_waits_for_the_socket_then_says_it_is_serving(self) -> None:
        """The line a watcher waits for is printed only once the socket exists.

        A seat exposes no port, so this log line is the whole health signal the
        deploy robot has. Printed at launch it would be a banner, and would
        report ready while the agent was still coming up.
        """
        sock = self.root / "control.sock"
        server = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(str(sock))
        result = self.run_entrypoint(env=self.ready_env(str(sock)))
        self.assertIn("serving: taking commands on", result.stderr)
        self.assertNotIn("gave up", result.stderr)

    def test_readiness_actually_waits_for_a_late_socket(self) -> None:
        """The wait is the point, not the final check.

        A seat is not instant. If readiness only looked once and moved on, a
        socket that appears two seconds later would be reported as a seat that
        never came up, and the deploy robot would roll back a healthy deploy --
        bug 5 of crunchtools/deployer#17, reintroduced from this side.
        """
        sock = self.root / "late.sock"
        server = socketmod.socket(socketmod.AF_UNIX, socketmod.SOCK_STREAM)
        self.addCleanup(server.close)

        def bind_late() -> None:
            server.bind(str(sock))

        timer = threading.Timer(1.5, bind_late)
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.run_entrypoint(env=self.ready_env(str(sock), timeout="10"))
        self.assertIn("serving: taking commands on", result.stderr)
        self.assertNotIn("gave up", result.stderr)

    def test_readiness_gives_up_without_claiming_to_serve(self) -> None:
        """And the failure line shares no substring with the success line.

        "NOT serving" would contain "serving", so a watcher told to wait for
        the success line would be satisfied by the failure one -- a readiness
        check that cannot fail.
        """
        missing = self.root / "never-appears.sock"
        result = self.run_entrypoint(env=self.ready_env(str(missing), timeout="1"))
        self.assertIn("gave up", result.stderr)
        self.assertNotIn("serving:", result.stderr)

    def test_a_dead_supervised_process_stops_the_wait_at_once(self) -> None:
        """Promptness is the claim, so the timeout is set far above the budget.

        Without the liveness check the loop sits out the whole readiness
        timeout waiting for a socket that cannot appear, turning a fast clear
        failure into a slow silent one. A generous timeout here would be
        satisfied by the slow path too, so this asserts the elapsed time.
        """
        # session_env's stand-in agent exits 7 immediately; the socket never
        # appears. A 60s readiness timeout is far longer than this may take.
        env = {
            **self.session_env(),
            "JOSUI_AGENT_SOCKET": str(self.root / "never.sock"),
            "JOSUI_READY_TIMEOUT": "60",
        }
        started = time.monotonic()
        result = self.run_entrypoint(env=env)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("gave up", result.stderr)
        self.assertNotIn("serving:", result.stderr)
        self.assertLess(elapsed, 20.0, f"took {elapsed:.1f}s; the dead process should end the wait at once")

    def test_remote_control_only_does_not_claim_to_be_serving(self) -> None:
        result = self.run_entrypoint()
        self.assertIn("remote control only", result.stderr)
        self.assertNotIn("serving:", result.stderr)

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

    def test_an_explicit_debug_file_is_used(self) -> None:
        explicit = self.root / "somewhere-else.log"
        result = self.run_entrypoint(env={**self.env, "JOSUI_DEBUG_FILE": str(explicit)})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.home / "claude-args").read_text().split()
        self.assertEqual(args[args.index("--debug-file") + 1], str(explicit))

    def test_a_debug_file_that_cannot_be_written_is_reported_and_dropped(self) -> None:
        env = {**self.env, "JOSUI_DEBUG_FILE": str(self.root / "no-such-directory/debug.log")}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("running without a debug file", result.stderr)
        self.assertNotIn("--debug-file", (self.home / "claude-args").read_text())

    # The person's own terminal (JOSUI_TMUX), with a tmux that records its
    # arguments and reports no existing session.
    def fake_tmux(self) -> None:
        tmux = self.root / "bin/tmux"
        tmux.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/tmux-args"\n'
            '[ "$1" = has-session ] && exit 1\nexit 0\n'
        )
        tmux.chmod(0o755)

    def test_tmux_session_is_started_when_asked(self) -> None:
        self.fake_tmux()
        result = self.run_entrypoint(env={**self.env, "JOSUI_TMUX": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.home / "tmux-args").read_text().splitlines()
        self.assertEqual(calls[0], "has-session -t dev")
        self.assertEqual(
            calls[1],
            f"new-session -d -s dev -c {self.home / 'work'} claude --permission-mode acceptEdits",
        )
        self.assertIn("set-option -t dev remain-on-exit on", calls)
        self.assertIn("tmux session 'dev' is up", result.stderr)

    def test_a_tmux_session_that_will_not_start_is_not_reported_as_up(self) -> None:
        tmux = self.root / "bin/tmux"
        tmux.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/tmux-args"\n'
            '[ "$1" = new-session ] && exit 1\n[ "$1" = has-session ] && exit 1\nexit 0\n'
        )
        tmux.chmod(0o755)
        result = self.run_entrypoint(env={**self.env, "JOSUI_TMUX": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not start the tmux session", result.stderr)
        self.assertNotIn("is up", result.stderr)
        # It stopped there rather than carrying on to set-option.
        self.assertNotIn("set-option", (self.home / "tmux-args").read_text())

    def test_an_existing_tmux_session_is_left_alone(self) -> None:
        tmux = self.root / "bin/tmux"
        tmux.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/tmux-args"\nexit 0\n')
        tmux.chmod(0o755)
        self.run_entrypoint(env={**self.env, "JOSUI_TMUX": "1"})
        self.assertEqual((self.home / "tmux-args").read_text().splitlines(), ["has-session -t dev"])

    def test_no_tmux_session_unless_it_is_asked_for(self) -> None:
        self.fake_tmux()
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.home / "tmux-args").exists())

    def test_tmux_missing_from_the_image_is_reported_not_fatal(self) -> None:
        env = {**self.env, "JOSUI_TMUX": "1", "JOSUI_TMUX_CMD": "tmux-that-is-not-installed"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no tmux-that-is-not-installed in this image", result.stderr)

    def fake_ssh(self) -> Path:
        ssh = self.root / "bin/ssh"
        ssh.write_text("#!/bin/bash\nexit 0\n")
        ssh.chmod(0o755)
        return ssh

    def seed_ssh_mount(self, *, known_hosts: bool = True) -> Path:
        src = self.config / "ssh"
        src.mkdir()
        # The mount is the host's: world-readable is exactly what we expect.
        key = src / "id_ed25519"
        key.write_text("PRIVATE KEY\n")
        key.chmod(0o644)
        (src / "config").write_text("Host gloval\n  Port 22422\n")
        if known_hosts:
            (src / "known_hosts").write_text("[gloval]:22422 ssh-ed25519 AAAA\n")
        return src

    def test_no_ssh_identity_unless_one_is_mounted(self) -> None:
        self.fake_ssh()
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.home / ".ssh").exists())

    def test_a_mounted_ssh_identity_is_copied_in_private(self) -> None:
        self.fake_ssh()
        self.seed_ssh_mount()
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.home / ".ssh"
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)
        for name in ("id_ed25519", "config", "known_hosts"):
            self.assertEqual(stat.S_IMODE((dest / name).stat().st_mode), 0o600, name)
        self.assertEqual((dest / "id_ed25519").read_text(), "PRIVATE KEY\n")
        # Copied, not linked: the agent's home must not depend on the mount.
        self.assertFalse((dest / "id_ed25519").is_symlink())

    def test_an_ssh_mount_without_a_key_is_reported_not_fatal(self) -> None:
        self.fake_ssh()
        (self.config / "ssh").mkdir()
        (self.config / "ssh/known_hosts").write_text("host key\n")
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no private key", result.stderr)

    def test_a_key_without_known_hosts_is_called_out(self) -> None:
        self.fake_ssh()
        self.seed_ssh_mount(known_hosts=False)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no known_hosts", result.stderr)

    def test_an_ssh_identity_with_no_ssh_in_the_image_is_reported_not_fatal(self) -> None:
        self.seed_ssh_mount()
        env = {**self.env, "JOSUI_SSH_CMD": "ssh-that-is-not-installed"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("there is no ssh-that-is-not-installed in this image", result.stderr)
        self.assertFalse((self.home / ".ssh/id_ed25519").exists())

    def test_without_a_homeserver_there_is_no_text_session(self) -> None:
        agent = self.root / "bin/claude-agent"
        agent.write_text('#!/bin/bash\ntouch "$HOME/agent-ran"\n')
        agent.chmod(0o755)
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.home / "agent-ran").exists())


if __name__ == "__main__":
    unittest.main()
