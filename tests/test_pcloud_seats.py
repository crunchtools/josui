"""tools/pcloud-seats: two folders only, never an unmounted or empty pCloud, rebinds when pCloud changes."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools/pcloud-seats/pcloud-seats"


class PcloudSeatsTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.drive, self.seats, self.bin = root / "drive", root / "seats", root / "bin"
        (self.drive / "Downloads").mkdir(parents=True)
        (self.drive / "Downloads" / "a.pdf").write_bytes(b"%PDF-")
        self.bin.mkdir()
        self.calls = root / "calls"
        self.mounted_flag = root / "mounted"
        self.mounted_flag.touch()
        # mountpoint(1) stands in for pCloud's FUSE mount; bindfs runs until
        # killed; every tool records how it was called.
        self.fake("mountpoint", f'[ -e "{self.mounted_flag}" ]')
        self.bindfs_pid = root / "bindfs.pid"
        self.fake("bindfs", f'echo $$ > "{self.bindfs_pid}"; exec sleep 30')
        self.fake("fusermount3", "exit 0")
        self.fake("findmnt", "exit 0")
        # stat -c %d reads pCloud's device number from a file; anything else is real stat.
        self.device = root / "device"
        self.device.write_text("41\n")
        real_stat = subprocess.run(
            ["sh", "-c", "command -v stat"], capture_output=True, text=True, check=True
        )
        self.fake(
            "stat", f'[ "$1 $2" = "-c %d" ] && exec cat "{self.device}"\nexec {real_stat.stdout.strip()} "$@"'
        )
        self.fake("chcon", "exit 0")

    def fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n{body}\n')
        path.chmod(0o755)

    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "PCLOUD_DRIVE": str(self.drive),
            "PCLOUD_SEATS": str(self.seats),
            "PCLOUD_EVERY": "0.1",
        }

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def called(self, name: str) -> list[str]:
        if not self.calls.exists():
            return []
        return [line for line in self.calls.read_text().splitlines() if line.split()[0] == name]

    def test_any_other_folder_is_refused_and_nothing_is_unmounted(self) -> None:
        for args in (["../../pCloudDrive"], ["--stop", "../../pCloudDrive"], ["--stop", ""], ["Documents"]):
            result = self.run_script(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("refusing", result.stderr)
        self.assertEqual(self.called("fusermount3"), [])
        self.assertEqual(self.called("bindfs"), [])

    def test_stop_unmounts_only_the_folder_mount(self) -> None:
        self.assertEqual(self.run_script("--stop", "Downloads").returncode, 0)
        self.assertEqual(self.called("fusermount3"), [f"fusermount3 -uz {self.seats}/pCloudDrive/Downloads"])

    def test_a_failed_unmount_fails_stop_and_nothing_is_said_when_none_is_mounted(self) -> None:
        self.fake("fusermount3", "exit 1")
        result = self.run_script("--stop", "Downloads")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not unmount", result.stderr)
        self.fake("findmnt", "exit 1")
        result = self.run_script("--stop", "Downloads")
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_an_unmounted_pcloud_is_never_bound(self) -> None:
        self.mounted_flag.unlink()
        result = self.run_script("Downloads")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not mounted", result.stderr)
        self.assertEqual(self.called("bindfs"), [])

    def test_an_empty_folder_is_never_bound(self) -> None:
        (self.drive / "Downloads" / "a.pdf").unlink()
        result = self.run_script("Downloads")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing or empty", result.stderr)
        self.assertEqual(self.called("bindfs"), [])

    def test_the_layout_matches_the_host(self) -> None:
        self.mounted_flag.unlink()  # stop before binding; the layout is made first
        self.run_script("Downloads")
        self.assertEqual(os.readlink(self.seats / "Downloads"), "pCloudDrive/Downloads")
        self.assertEqual(
            os.readlink(self.seats / "Pictures/Screenshots"), "../pCloudDrive/Autosync/Screenshots"
        )
        self.assertTrue((self.seats / "pCloudDrive/Downloads").is_dir())

    def start_bound(self, folder: str = "Downloads") -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            ["bash", str(SCRIPT), folder], env=self.env(), stderr=subprocess.PIPE, text=True
        )
        self.addCleanup(proc.kill)
        deadline = time.monotonic() + 10
        while not self.called("bindfs") and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(
            self.called("bindfs"),
            [
                "bindfs -f -r --perms=og-rwx -o allow_other "
                f"{self.drive}/{folder} {self.seats}/pCloudDrive/{folder}"
            ],
        )
        return proc

    def assert_rebinds(self, proc: subprocess.Popen[str]) -> None:
        _, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 1, "a non-zero exit is what makes Restart= bind again")
        self.assertIn("went away or changed", stderr)
        self.assertEqual(
            len(self.called("fusermount3")), 2, "a stale mount is cleared before binding and after"
        )

    def test_both_folders_bind_and_stop_at_their_host_paths(self) -> None:
        (self.drive / "Autosync/Screenshots").mkdir(parents=True)
        (self.drive / "Autosync/Screenshots/s.png").write_bytes(b"png")
        for folder in ("Downloads", "Autosync/Screenshots"):
            with self.subTest(folder=folder):
                self.calls.unlink(missing_ok=True)
                proc = self.start_bound(folder)
                proc.terminate()
                proc.communicate(timeout=10)
                self.calls.unlink()
                self.assertEqual(self.run_script("--stop", folder).returncode, 0)
                self.assertEqual(
                    self.called("fusermount3"), [f"fusermount3 -uz {self.seats}/pCloudDrive/{folder}"]
                )

    def test_pcloud_going_away_ends_the_bind_so_it_is_bound_again(self) -> None:
        proc = self.start_bound()
        self.mounted_flag.unlink()  # pCloud quits under the running bindfs
        self.assert_rebinds(proc)

    def test_pcloud_restarting_ends_the_bind_so_it_is_bound_again(self) -> None:
        proc = self.start_bound()
        self.device.write_text("42\n")  # still mounted, but a new FUSE mount
        self.assert_rebinds(proc)

    def test_stopping_the_unit_ends_bindfs_and_unmounts(self) -> None:
        proc = self.start_bound()
        proc.terminate()
        proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(self.called("fusermount3")), 2)
        pid = int(self.bindfs_pid.read_text())
        with self.assertRaises(ProcessLookupError, msg="bindfs must not outlive the script"):
            os.kill(pid, 0)

    def test_bindfs_dying_ends_the_unit_so_it_is_bound_again(self) -> None:
        self.fake("bindfs", "exit 3")
        proc = subprocess.run(
            ["bash", str(SCRIPT), "Downloads"],
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("bindfs exited (3)", proc.stderr)


if __name__ == "__main__":
    unittest.main()
