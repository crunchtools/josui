"""channel-session, driving a stand-in `claude` that shows the startup dialogs."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SESSION = Path(__file__).resolve().parent.parent / "rootfs/usr/local/libexec/josui/channel-session"

# Shows each dialog named in DIALOGS, records the keys that answer it, then
# registers the channel (no banner with BANNER=0, as in the container),
# records READS more typed lines, and exits with status 3. Words are split
# by cursor moves, the way the real TUI draws them.
FAKE_CLAUDE = r"""#!/usr/bin/env python3
import os, sys
home = os.environ["HOME"]
open(os.path.join(home, "args"), "w").write("\n".join(sys.argv[1:]))
screens = {
    "trust": "Yes,\x1b[1CI\x1b[1Ctrust\x1b[1Cthis\x1b[1Cfolder",
    "channels": "1.\x1b[1CI\x1b[1Cam\x1b[1Cusing\x1b[1Cthis\x1b[1Cfor\x1b[1Clocal\x1b[1Cdevelopment",
    "bypass": "WARNING:\x1b[1CBypass\x1b[1CPermissions\x1b[1Cmode",
}
with open(os.path.join(home, "keys"), "w") as keys:
    for name in os.environ["DIALOGS"].split():
        sys.stdout.write(screens[name] + "\r\n"); sys.stdout.flush()
        keys.write(name + " " + repr(sys.stdin.readline()) + "\n"); keys.flush()
if os.environ.get("BANNER") != "0":
    sys.stdout.write("messages\x1b[1Cfrom\x1b[1Cserver:matrix\x1b[1Cinject\x1b[1Cdirectly\r\n")
    sys.stdout.flush()
with open(os.path.join(home, "keys"), "a") as keys:
    for _ in range(int(os.environ.get("READS", "0"))):
        keys.write("typed " + repr(sys.stdin.readline()) + "\n"); keys.flush()
sys.exit(3)
"""


@unittest.skipUnless(shutil.which("expect"), "expect is not installed")
class ChannelSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.fifo = self.home / "control"
        claude = self.home / "claude"
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(0o755)

    def command(self, dialogs: str, mode: str = "bypassPermissions", **env: str) -> dict:
        env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.home}:{os.environ['PATH']}",
               "DIALOGS": dialogs, **env}
        return {"args": ["expect", "-f", str(SESSION), "/cfg.json", mode, str(self.fifo)],
                "env": env, "text": True}

    def run_session(self, dialogs: str, mode: str = "bypassPermissions") -> subprocess.CompletedProcess[str]:
        return subprocess.run(**self.command(dialogs, mode), capture_output=True, timeout=60, check=False)

    def send_commands(self, data: bytes, dialogs: str = "channels", **env: str) -> tuple[int, str]:
        """Start a session that reads one typed line, write data to its FIFO."""
        proc = subprocess.Popen(**self.command(dialogs, READS="1", **env),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(proc.kill)
        deadline = time.time() + 20
        while True:
            try:
                fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError:
                # Not made yet, or not open for reading yet.
                self.assertLess(time.time(), deadline, "channel-session never opened its FIFO")
                time.sleep(0.1)
        os.write(fd, data)
        os.close(fd)
        _, stderr = proc.communicate(timeout=30)
        return proc.returncode, stderr

    def keys(self) -> list[str]:
        return (self.home / "keys").read_text().splitlines()

    def test_answers_each_dialog_and_exits_with_claudes_status(self) -> None:
        result = self.run_session("channels bypass")
        self.assertEqual(result.returncode, 3, result.stderr)
        # Enter on the channels warning, whose default is the one we want;
        # Down then Enter on bypass, whose default is "No, exit".
        self.assertEqual(self.keys(), [r"channels '\n'", r"bypass '\x1b[B\n'"])
        self.assertIn("channel registered", result.stderr)
        self.assertIn("claude exited with status 3", result.stderr)

    def test_leaves_the_trust_question_to_the_operator(self) -> None:
        result = self.run_session("trust channels")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not trusted yet", result.stderr)
        self.assertFalse((self.home / "keys").read_text().strip())

    def test_loads_the_channel_without_remote_control(self) -> None:
        # The operator reaches this session by text; claude.ai shows only the
        # Remote Control server's sessions.
        self.run_session("channels")
        args = (self.home / "args").read_text().splitlines()
        self.assertEqual(args, [
            "--mcp-config", "/cfg.json", "--permission-mode", "bypassPermissions",
            "--dangerously-load-development-channels", "server:matrix",
        ])

    def test_types_only_allowed_commands_from_the_fifo(self) -> None:
        code, stderr = self.send_commands(b"/bogus\n!rm -rf /\n/clear\x1b[A\n/clear\n")
        self.assertEqual(code, 3, stderr)
        self.assertEqual(self.keys(), [r"channels '\n'", r"typed '/clear\n'"])
        self.assertEqual(stderr.count("dropped a control line"), 3)
        self.assertIn("sent /clear", stderr)
        self.assertEqual(self.fifo.stat().st_mode & 0o777, 0o600)

    def test_startup_ends_without_the_banner(self) -> None:
        # In the container the banner never shows; a quiet spell ends startup
        # and commands are still taken.
        code, stderr = self.send_commands(b"/compact keep the RT context\n",
                                          BANNER="0", JOSUI_STARTUP_QUIET="1")
        self.assertEqual(code, 3, stderr)
        self.assertIn("startup done", stderr)
        self.assertEqual(self.keys()[-1], r"typed '/compact keep the RT context\n'")

    def test_terminal_output_stays_out_of_the_log(self) -> None:
        result = self.run_session("channels")
        self.assertNotIn("development", result.stdout + result.stderr.replace("development channels", ""))


if __name__ == "__main__":
    unittest.main()
