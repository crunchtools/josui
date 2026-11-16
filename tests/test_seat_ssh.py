"""tools/seat-ssh/seat-ssh: one tagged, restricted line per seat on every host."""

from __future__ import annotations

import os
import pty
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
        self.keys = {}
        for seat in ("dmax1", "dmax2"):
            subprocess.run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    f"dolza-{seat}",
                    "-f",
                    str(root / seat),
                ],
                check=True,
            )
            self.keys[seat] = (root / f"{seat}.pub").read_text().split()[1]
            (root / f"{seat}.pub").rename(self.pubkeys / f"{seat}.pub")
        self.local = root / "local/authorized_keys"
        self.remote = root / "remote/authorized_keys"
        self.remote.parent.mkdir()
        self.remote.write_text("ssh-ed25519 SCOTT scott@breetai\n")
        # "remote" goes through a shell exactly as ssh would hand it one string.
        self.hosts = f"local|local|{self.local}\nremote|bash -c|{self.remote}"

    def run_tool(self, *args: str, hosts: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "SEAT_SSH_PUBKEYS": str(self.pubkeys), "SEAT_SSH_HOSTS": hosts or self.hosts}
        return subprocess.run(
            ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
        )

    def test_grant_writes_one_restricted_tagged_line_per_seat_everywhere(self) -> None:
        result = self.run_tool("grant")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (self.local, self.remote):
            lines = [line for line in path.read_text().splitlines() if "dolza-" in line]
            options = 'from="10.44.0.20",no-agent-forwarding,no-X11-forwarding'
            self.assertEqual(
                lines,
                [
                    f"{options} ssh-ed25519 {self.keys['dmax1']} dolza-dmax1",
                    f"{options} ssh-ed25519 {self.keys['dmax2']} dolza-dmax2",
                ],
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn("SCOTT scott@breetai", self.remote.read_text())

    def test_granting_twice_or_after_a_rotation_keeps_one_line(self) -> None:
        self.run_tool("grant")
        old = self.keys["dmax1"]
        (self.pubkeys / "dmax1.pub").unlink()
        rotated = self.pubkeys.parent / "rotated"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(rotated)], check=True)
        new = (self.pubkeys.parent / "rotated.pub").read_text().split()[1]
        (self.pubkeys.parent / "rotated.pub").rename(self.pubkeys / "dmax1.pub")
        self.run_tool("grant")
        text = self.remote.read_text()
        self.assertEqual(text.count("dolza-dmax1"), 1)
        self.assertIn(new, text)
        self.assertNotIn(old, text)

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

    def test_a_from_address_that_is_not_an_address_is_refused(self) -> None:
        env_hosts = self.hosts
        result = subprocess.run(
            ["bash", str(SCRIPT), "grant"],
            env={
                **os.environ,
                "SEAT_SSH_PUBKEYS": str(self.pubkeys),
                "SEAT_SSH_HOSTS": env_hosts,
                "SEAT_SSH_FROM": '10.44.0.20",command="sh',
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an IPv4 address", result.stderr)
        self.assertFalse(self.local.exists())

    def run_from(self, value: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "SEAT_SSH_PUBKEYS": str(self.pubkeys),
            "SEAT_SSH_HOSTS": self.hosts,
            "SEAT_SSH_FROM": value,
        }
        return subprocess.run(
            ["bash", str(SCRIPT), "grant"], env=env, capture_output=True, text=True, check=False
        )

    def test_address_bounds(self) -> None:
        for bad in ("256.0.0.1", "10.44.0.300", "10.44.0.0/33", "10.44.0", "10.44.0.20/"):
            with self.subTest(bad=bad):
                self.assertIn("not an IPv4 address", self.run_from(bad).stderr)
        for good in ("255.255.255.255", "10.44.0.0/32", "10.44.0.0/0", "0.0.0.0"):
            with self.subTest(good=good):
                self.assertEqual(self.run_from(good).returncode, 0, good)

    def test_an_unsupported_key_type_is_refused(self) -> None:
        (self.pubkeys / "dmax1.pub").write_text(f"ssh-dss {self.keys['dmax1']} dolza-dmax1\n")
        result = self.run_tool("grant", "dmax1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected key type", result.stderr)

    def test_base64_that_is_not_a_key_is_refused(self) -> None:
        (self.pubkeys / "dmax1.pub").write_text("ssh-ed25519 AA=== dolza-dmax1\n")
        result = self.run_tool("grant", "dmax1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a valid ssh-ed25519 public key", result.stderr)

    def test_a_filter_failure_leaves_the_file_alone(self) -> None:
        self.remote.write_text("ssh-ed25519 SCOTT scott@breetai\n")
        fake = self.pubkeys.parent / "fakebin"
        fake.mkdir()
        (fake / "grep").write_text("#!/bin/sh\nexit 2\n")
        (fake / "grep").chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake}:{os.environ['PATH']}",
            "SEAT_SSH_PUBKEYS": str(self.pubkeys),
            "SEAT_SSH_HOSTS": f"remote|bash -c|{self.remote}",
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), "grant"], env=env, capture_output=True, text=True, check=False
        )
        self.assertIn("remote: unreachable or failed", result.stdout)
        self.assertEqual(self.remote.read_text(), "ssh-ed25519 SCOTT scott@breetai\n")
        self.assertEqual([p.name for p in self.remote.parent.iterdir()], ["authorized_keys"])

    def test_a_public_key_that_is_not_base64_is_refused(self) -> None:
        (self.pubkeys / "dmax1.pub").write_text("ssh-ed25519 AAAA\nevil dolza-dmax1\n")
        (self.pubkeys / "dmax1.pub").write_text("ssh-ed25519 AA,AA dolza-dmax1\n")
        result = self.run_tool("grant", "dmax1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not base64", result.stderr)

    def test_an_unreadable_file_is_never_replaced(self) -> None:
        # A path that is not a readable regular file is refused, untouched.
        self.remote.unlink()
        self.remote.mkdir()
        (self.remote / "kept").write_text("x\n")
        result = self.run_tool("grant")
        self.assertIn("remote: unreachable or failed", result.stdout)
        self.assertTrue(self.remote.is_dir())
        self.assertEqual(sorted(p.name for p in self.remote.iterdir()), ["kept"])

    def test_a_seat_name_is_never_a_pattern(self) -> None:
        result = self.run_tool("revoke", ".*")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a seat name", result.stderr)


SEAT = Path(__file__).resolve().parent.parent / "tools/seat-ssh/seat"


class SeatAttachTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name)
        for tool in ("podman", "ssh"):
            (self.bin / tool).write_text(f'#!/bin/sh\necho {tool} "$@"\n')
            (self.bin / tool).chmod(0o755)

    def attach(self, link: str, *args: str) -> subprocess.CompletedProcess[str]:
        (self.bin / link).symlink_to(SEAT)
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        return subprocess.run(
            [str(self.bin / link), *args], env=env, capture_output=True, text=True, check=False
        )

    def test_each_kind_of_seat_reaches_its_container(self) -> None:
        style = (
            "tmux -u set -g status-style bg=default,fg=colour244 ; set -g mouse off ; "
            "set -g window-status-current-style fg=colour250,bold ; "
            "set -g terminal-overrides ,*:Tc ; attach -t dev:0"
        )
        self.assertEqual(self.attach("v2").stdout.strip(), f"podman exec -it vertex2 {style}")
        self.assertEqual(self.attach("m4").stdout.strip(), f"podman exec -it dmax4 {style}")
        self.assertEqual(self.attach("prime").stdout.strip(), f"podman exec -it josui {style}")
        # Through ssh the command is quoted for the remote shell.
        remote = self.attach("x1").stdout.strip()
        quoted = " ".join(f"'{word}'" for word in style.split(" "))
        self.assertEqual(remote, f"ssh -t exedore podman exec -it max1 {quoted}")

    def test_the_tab_is_titled_with_the_seat_name_on_a_terminal(self) -> None:
        (self.bin / "v2").symlink_to(SEAT)
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        # A pty stands in for the terminal; piped output gets no title.
        primary, secondary = pty.openpty()
        subprocess.run([str(self.bin / "v2")], env=env, stdout=secondary, check=False)
        os.close(secondary)
        out = os.read(primary, 4096).decode()
        os.close(primary)
        self.assertIn("\033]0;Dolza 2 - Vertex\007", out)
        self.assertNotIn("\033]0;", self.attach("m1").stdout)

    def test_every_kind_of_seat_gets_its_session_name(self) -> None:
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        for link, title in (
            ("v3", "Dolza 3 - Vertex"),
            ("m2", "Dolza 2 - Max"),
            ("x4", "Exedore 4 - Max"),
            ("prime", "Josui Prime"),
        ):
            with self.subTest(link=link):
                (self.bin / link).symlink_to(SEAT)
                primary, secondary = pty.openpty()
                subprocess.run([str(self.bin / link)], env=env, stdout=secondary, check=False)
                os.close(secondary)
                out = os.read(primary, 4096).decode()
                os.close(primary)
                self.assertIn(f"\033]0;{title}\007", out)

    def test_shell_opens_the_shell_window(self) -> None:
        self.assertTrue(self.attach("v3", "shell").stdout.strip().endswith("-t dev:1"))

    def test_an_unknown_name_prints_usage(self) -> None:
        result = self.attach("v9")
        self.assertEqual(result.returncode, 2)
        self.assertIn("link this script", result.stdout)


if __name__ == "__main__":
    unittest.main()
