"""tools/screenshot-mirror: never wipes the copy; mirrors adds and deletes."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools/screenshot-mirror/screenshot-mirror"


class ScreenshotMirrorTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.src, self.dst = root / "src", root / "dst"
        self.src.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.mounted(True)

    def mounted(self, yes: bool) -> None:
        # mountpoint(1) stands in for the pCloud FUSE mount.
        (self.bin / "mountpoint").write_text(f"#!/bin/sh\nexit {0 if yes else 1}\n")
        (self.bin / "mountpoint").chmod(0o755)

    def once(self) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "SCREENSHOT_SRC": str(self.src),
               "SCREENSHOT_DST": str(self.dst), "SCREENSHOT_MOUNT": str(self.src)}
        return subprocess.run(["bash", str(SCRIPT), "--once"], env=env, capture_output=True, text=True, check=False)

    def test_adds_and_deletes_are_mirrored_and_hidden_files_are_not(self) -> None:
        (self.src / "a.png").write_bytes(b"a")
        (self.src / ".pcloud-tmp").write_bytes(b"x")
        self.assertEqual(self.once().returncode, 0)
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["a.png"])
        (self.src / "b.png").write_bytes(b"b")
        (self.src / "a.png").unlink()
        self.once()
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["b.png"])

    def test_an_unmounted_source_never_wipes_the_copy(self) -> None:
        (self.src / "a.png").write_bytes(b"a")
        self.once()
        self.mounted(False)
        (self.src / "a.png").unlink()
        result = self.once()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not mounted", result.stderr)
        self.assertEqual([p.name for p in self.dst.iterdir()], ["a.png"])

    def test_an_edited_file_is_mirrored(self) -> None:
        (self.src / "a.png").write_bytes(b"one")
        self.once()
        (self.src / "a.png").write_bytes(b"two!")
        self.once()
        self.assertEqual((self.dst / "a.png").read_bytes(), b"two!")

    def test_a_symlink_in_the_source_is_never_followed(self) -> None:
        secret = self.src.parent / "secret.txt"
        secret.write_text("not a screenshot\n")
        (self.src / "a.png").write_bytes(b"a")
        (self.src / "leak.png").symlink_to(secret)
        self.once()
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["a.png"])

    def test_a_failed_copy_keeps_the_previous_one(self) -> None:
        (self.src / "a.png").write_bytes(b"one")
        self.once()
        (self.src / "a.png").write_bytes(b"two, longer")
        self.dst.chmod(0o555)  # nothing can be written into the copy
        try:
            result = self.once()
        finally:
            self.dst.chmod(0o755)
        self.assertIn("could not copy a.png", result.stderr)
        self.assertNotEqual(result.returncode, 0, "a failed copy must fail the pass so it is retried")
        self.assertEqual((self.dst / "a.png").read_bytes(), b"one")
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["a.png"])

    def test_an_empty_source_never_wipes_the_copy(self) -> None:
        (self.src / "a.png").write_bytes(b"a")
        self.once()
        (self.src / "a.png").unlink()
        result = self.once()
        self.assertIn("missing or empty", result.stderr)
        self.assertEqual([p.name for p in self.dst.iterdir()], ["a.png"])


if __name__ == "__main__":
    unittest.main()
