"""tools/voice/ptt, second press: where the transcript is sent.

The recorder, whisper, notify-send and podman are stand-ins on PATH. podman
logs every call and answers `claude-agent status` with PTT_TEST_STATUS (or
fails when PTT_TEST_STATUS_FAIL is set).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PTT = Path(__file__).resolve().parent.parent / "tools/voice/ptt"

PODMAN = """#!/bin/bash
printf '%s\\n' "$*" >> "$PTT_TEST_LOG"
if [ "$3" = claude-agent ] && [ "$4" = status ]; then
  [ -n "${PTT_TEST_STATUS_FAIL:-}" ] && exit 125
  printf '%b' "$PTT_TEST_STATUS"
fi
exit 0
"""
CURL = "#!/bin/bash\necho ' list the containers '\n"
NOTIFY = "#!/bin/bash\nexit 0\n"

LIVE = "hooks:                 present\\nsession:               idle, 0 held, pid 42\\n"
NONE = "hooks:                 present\\nsession:               none running\\n"
NO_LINE = "claude-agent 0.2.1\\n"


class PttRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in (("podman", PODMAN), ("curl", CURL), ("notify-send", NOTIFY)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.runtime = root / "run"
        (self.runtime / "ptt").mkdir(parents=True)
        self.log = root / "podman.log"
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "XDG_RUNTIME_DIR": str(self.runtime),
            "PTT_TEST_LOG": str(self.log),
        }

    def second_press(self, **extra: str) -> list[str]:
        # A recording in progress: a live "recorder" and a non-empty clip.
        recorder = subprocess.Popen(["sleep", "30"])
        self.addCleanup(recorder.kill)
        (self.runtime / "ptt/record.pid").write_text(str(recorder.pid))
        (self.runtime / "ptt/utterance.wav").write_bytes(b"RIFF")
        result = subprocess.run(
            ["bash", str(PTT)], env={**self.env, **extra}, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.runtime / "ptt/voice-turn").read_text(), "list the containers")
        return self.log.read_text().splitlines()

    def test_a_live_supervisor_takes_the_prompt(self) -> None:
        calls = self.second_press(PTT_TEST_STATUS=LIVE)
        self.assertEqual(calls.count("exec josui claude-agent status"), 1)
        self.assertIn("exec josui claude-agent send list the containers", calls)
        self.assertFalse(any("send-keys" in c for c in calls))

    def assert_typed_into_tmux(self, calls: list[str]) -> None:
        self.assertIn("exec josui tmux send-keys -t dev -l list the containers", calls)
        self.assertIn("exec josui tmux send-keys -t dev Enter", calls)
        self.assertFalse(any("claude-agent send" in c for c in calls))

    def test_no_running_session_types_into_tmux(self) -> None:
        self.assert_typed_into_tmux(self.second_press(PTT_TEST_STATUS=NONE))

    def test_status_without_a_session_line_types_into_tmux(self) -> None:
        self.assert_typed_into_tmux(self.second_press(PTT_TEST_STATUS=NO_LINE))

    def test_a_failed_status_call_types_into_tmux(self) -> None:
        self.assert_typed_into_tmux(self.second_press(PTT_TEST_STATUS="", PTT_TEST_STATUS_FAIL="1"))


if __name__ == "__main__":
    unittest.main()
