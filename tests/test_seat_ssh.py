"""tools/seat-ssh/seat-ssh: one tagged, restricted line per seat on every host."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools/seat-ssh/seat-ssh"


class SeatSshTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.pubkeys = root / "pubkeys"
        self.pubkeys.mkdir()
        for seat in ("dmax1", "dmax2"):
            (self.pubkeys / f"{seat}.pub").write_text(f"ssh-ed25519 KEY{seat} dolza-{seat}\n")
        self.local = root / "local/authorized_keys"
        self.remote = root / "remote/authorized_keys"
        self.remote.parent.mkdir()
        self.remote.write_text("ssh-ed25519 SCOTT scott@breetai\n")
        # "remote" goes through a shell exactly as ssh would hand it one string.
        self.hosts = f"local|local|{self.local}\nremote|bash -c|{self.remote}"

    def run_tool(self, *args: str, hosts: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "SEAT_SSH_PUBKEYS": str(self.pubkeys), "SEAT_SSH_HOSTS": hosts or self.hosts}
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False)

    def test_grant_writes_one_restricted_tagged_line_per_seat_everywhere(self) -> None:
        result = self.run_tool("grant")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.local, self.remote):
            lines = [line for line in path.read_text().splitlines() if "dolza-" in line]
            self.assertEqual(
                lines,
                [
                    'from="10.44.0.20",no-agent-forwarding,no-X11-forwarding ssh-ed25519 KEYdmax1 dolza-dmax1',
                    'from="10.44.0.20",no-agent-forwarding,no-X11-forwarding ssh-ed25519 KEYdmax2 dolza-dmax2',
                ],
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn("SCOTT scott@breetai", self.remote.read_text())

    def test_granting_twice_or_after_a_rotation_keeps_one_line(self) -> None:
        self.run_tool("grant")
        (self.pubkeys / "dmax1.pub").write_text("ssh-ed25519 ROTATED dolza-dmax1\n")
        self.run_tool("grant")
        text = self.remote.read_text()
        self.assertEqual(text.count("dolza-dmax1"), 1)
        self.assertIn("ROTATED", text)
        self.assertNotIn("KEYdmax1", text)

    def test_revoke_removes_only_that_seat(self) -> None:
        self.run_tool("grant")
        result = self.run_tool("revoke", "dmax1")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.local, self.remote):
            text = path.read_text()
            self.assertNotIn("dolza-dmax1", text)
            self.assertIn("dolza-dmax2", text)
        self.assertIn("SCOTT scott@breetai", self.remote.read_text())

    def test_an_unreachable_host_is_reported_and_the_rest_still_done(self) -> None:
        result = self.run_tool("grant", hosts=f"down|false|/nowhere\nlocal|local|{self.local}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("down: unreachable", result.stdout)
        self.assertIn("dolza-dmax1", self.local.read_text())

    def test_a_seat_name_is_never_a_pattern(self) -> None:
        result = self.run_tool("revoke", ".*")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a seat name", result.stderr)


if __name__ == "__main__":
    unittest.main()
