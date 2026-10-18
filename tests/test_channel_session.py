"""channel-session, driving a stand-in `claude` that shows the startup dialogs."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SESSION = Path(__file__).resolve().parent.parent / "rootfs/usr/local/libexec/josui/channel-session"

# Shows each dialog named in DIALOGS, records the keys that answer it, then
# registers the channel and exits with status 3. Words are split by cursor
# moves, the way the real TUI draws them.
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
sys.stdout.write("messages\x1b[1Cfrom\x1b[1Cserver:matrix\x1b[1Cinject\x1b[1Cdirectly\r\n")
sys.stdout.flush()
sys.exit(3)
"""


@unittest.skipUnless(shutil.which("expect"), "expect is not installed")
class ChannelSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        claude = self.home / "claude"
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(0o755)

    def run_session(self, dialogs: str, mode: str = "bypassPermissions") -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.home}:{os.environ['PATH']}",
               "DIALOGS": dialogs}
        return subprocess.run(
            ["expect", "-f", str(SESSION), "/cfg.json", "josui-rcs", mode],
            env=env, capture_output=True, text=True, timeout=60, check=False,
        )

    def keys(self) -> list[str]:
        return (self.home / "keys").read_text().splitlines()

    def test_answers_each_dialog_and_exits_with_claudes_status(self) -> None:
        result = self.run_session("channels bypass")
        self.assertEqual(result.returncode, 3, result.stderr)
        # Enter on the channels warning, whose default is the one we want;
        # Down then Enter on bypass, whose default is "No, exit".
        self.assertEqual(self.keys(), [r"channels '\n'", r"bypass '\x1b[B\n'"])
        self.assertIn("session josui-rcs is live", result.stderr)
        self.assertIn("claude exited with status 3", result.stderr)

    def test_leaves_the_trust_question_to_the_operator(self) -> None:
        result = self.run_session("trust channels")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not trusted yet", result.stderr)
        self.assertFalse((self.home / "keys").read_text().strip())

    def test_passes_the_channel_and_remote_control_flags(self) -> None:
        self.run_session("channels")
        args = (self.home / "args").read_text().splitlines()
        self.assertEqual(args, [
            "--mcp-config", "/cfg.json", "--remote-control", "josui-rcs", "--permission-mode",
            "bypassPermissions", "--dangerously-load-development-channels", "server:matrix",
        ])

    def test_terminal_output_stays_out_of_the_log(self) -> None:
        result = self.run_session("channels")
        self.assertNotIn("development", result.stdout + result.stderr.replace("development channels", ""))


if __name__ == "__main__":
    unittest.main()
