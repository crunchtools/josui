"""tools/seat-ssh/seat-ssh: one tagged, restricted line per seat on every host.

Every seat key is root on gloval and exedore; a Vertex-billing seat is also
smccarty and root on dolza and breetai (a1769). This tool writes only the files
it reaches unprivileged -- root on gloval and exedore, smccarty on the two
desktops -- so these tests cover those. The Vertex root lines on dolza and
breetai go in through the seat-fence sudo paste (seat-fence README S7), not from
this tool, so there is nothing here to exercise for them.
"""

from __future__ import annotations

import os
import pty
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools/seat-ssh/seat-ssh"


HOSTS = ("gloval", "exedore", "breetai", "dolza")
TAGS = {
    "dmax1": "dolza-dmax1",
    "vertex1": "dolza-vertex1",
    "max1": "exedore-max1",
    "bmax1": "breetai-bmax1",
    "bvertex1": "breetai-bvertex1",
}
FROM = {  # (seat host, target) -> from=, as ssh-from.tsv
    ("dolza", "gloval"): "10.44.0.20",
    ("dolza", "exedore"): "10.44.0.20,10.99.0.1",
    ("dolza", "dolza"): "10.44.0.20",
    ("dolza", "breetai"): "10.44.0.20",
    ("breetai", "gloval"): "10.44.0.10",
    ("breetai", "exedore"): "10.44.0.10",
    ("breetai", "dolza"): "10.44.0.10",
    ("breetai", "breetai"): "10.44.0.10",
    ("exedore", "gloval"): "10.44.0.24",
    ("exedore", "exedore"): "10.88.0.0/16",
}


class SeatSshTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.pubkeys = root / "pubkeys"
        self.pubkeys.mkdir()
        self.keys: dict[str, str] = {}
        for seat, tag in {**TAGS, "josui": "dolza-josui"}.items():
            self.keygen(root, seat, tag)
        self.files = {host: root / host / "authorized_keys" for host in HOSTS}
        for host in HOSTS:
            self.files[host].parent.mkdir()
        for host in ("exedore", "dolza"):
            self.files[host].write_text("ssh-ed25519 SCOTT scott@breetai\n")
        # gloval and exedore go through a shell exactly as ssh would hand it
        # one string; breetai and dolza are local files.
        self.hosts = "\n".join(
            f"{host}|{'bash -c' if host in ('gloval', 'exedore') else 'local'}|{path}"
            for host, path in self.files.items()
        )
        self.root = root

    def keygen(self, root: Path, seat: str, tag: str) -> None:
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", tag, "-f", str(root / seat)], check=True
        )
        self.keys[seat] = (root / f"{seat}.pub").read_text().split()[1]
        (root / f"{seat}.pub").rename(self.pubkeys / f"{seat}.pub")

    def run_tool(self, *args: str, hosts: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "SEAT_SSH_PUBKEYS": str(self.pubkeys), "SEAT_SSH_HOSTS": hosts or self.hosts}
        return subprocess.run(
            ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
        )

    def seat_lines(self, host: str) -> list[str]:
        text = self.files[host].read_text()
        return sorted(x for x in text.splitlines() if re.search(r" (dolza|exedore|breetai)-\S+$", x))

    def line(self, seat: str, target: str) -> str:
        host = TAGS[seat].split("-")[0]
        return f'from="{FROM[(host, target)]}",restrict,pty ssh-ed25519 {self.keys[seat]} {TAGS[seat]}'

    def test_every_seat_is_root_on_gloval_and_exedore_with_its_own_pin(self) -> None:
        result = self.run_tool("grant")
        self.assertEqual(result.returncode, 0, result.stderr)
        for host in ("gloval", "exedore"):
            with self.subTest(host=host):
                want = sorted(self.line(seat, host) for seat in TAGS)
                got = [x for x in self.seat_lines(host) if "dolza-josui" not in x]
                self.assertEqual(got, want)
                self.assertEqual(self.files[host].stat().st_mode & 0o777, 0o600)
        self.assertIn("SCOTT scott@breetai", self.files["exedore"].read_text())

    def test_only_vertex_billing_seats_and_prime_reach_dolza_and_breetai(self) -> None:
        # The desktop files this tool writes are smccarty's: the Vertex seats
        # (and Prime on dolza). The Vertex seats are also root on dolza and
        # breetai (a1769), but that /root file is installed by the seat-fence
        # sudo paste, not here, so it is not asserted.
        self.run_tool("grant")
        for host in ("dolza", "breetai"):
            with self.subTest(host=host):
                got = [x for x in self.seat_lines(host) if "-josui" not in x]
                self.assertEqual(got, sorted(self.line(seat, host) for seat in ("vertex1", "bvertex1")))
        for host in ("gloval", "exedore", "dolza"):
            with self.subTest(host=host):
                prime_line = (
                    f'from="{FROM[("dolza", host)]}",no-agent-forwarding,no-X11-forwarding'
                    f" ssh-ed25519 {self.keys['josui']} dolza-josui"
                )
                self.assertIn(prime_line, self.seat_lines(host))
        self.assertNotIn("dolza-josui", self.files["breetai"].read_text())

    def test_a_grant_takes_prime_off_breetai_if_it_is_there(self) -> None:
        self.files["breetai"].write_text("ssh-ed25519 OLD dolza-josui\nssh-ed25519 SCOTT scott@x\n")
        self.run_tool("grant")
        text = self.files["breetai"].read_text()
        self.assertNotIn("dolza-josui", text)
        self.assertIn("SCOTT", text)

    def test_a_max_seat_grant_removes_the_line_dolza_should_not_have(self) -> None:
        self.files["dolza"].write_text('from="10.44.0.20",restrict ssh-ed25519 OLD dolza-dmax1\n')
        result = self.run_tool("grant", "dmax1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("dolza-dmax1", self.files["dolza"].read_text())
        self.assertIn("dolza-dmax1", self.files["gloval"].read_text())

    def test_the_old_breetai_vertex_comment_is_cleared_by_a_grant(self) -> None:
        self.files["breetai"].write_text(
            "ssh-ed25519 OLD vertex1@breetai\nssh-ed25519 OLD2 breetai-vertex1\nssh-ed25519 SCOTT scott@x\n"
        )
        self.run_tool("grant", "vertex1")
        text = self.files["breetai"].read_text()
        self.assertNotIn("vertex1@breetai", text)
        self.assertNotIn("breetai-vertex1", text)
        self.assertIn("SCOTT", text)
        self.assertIn("dolza-vertex1", text)

    def test_granting_twice_or_after_a_rotation_keeps_one_line(self) -> None:
        self.run_tool("grant")
        old = self.keys["dmax1"]
        (self.pubkeys / "dmax1.pub").unlink()
        (self.root / "dmax1").unlink()
        self.keygen(self.root, "dmax1", "dolza-dmax1")
        self.run_tool("grant")
        text = self.files["gloval"].read_text()
        self.assertEqual(text.count("dolza-dmax1"), 1)
        self.assertIn(self.keys["dmax1"], text)
        self.assertNotIn(old, text)

    def test_revoke_removes_only_that_seat(self) -> None:
        self.run_tool("grant")
        result = self.run_tool("revoke", "vertex1")
        self.assertEqual(result.returncode, 0, result.stderr)
        for host in HOSTS:
            text = self.files[host].read_text()
            self.assertNotIn("dolza-vertex1", text)
        self.assertIn("dolza-dmax1", self.files["gloval"].read_text())
        self.assertIn("breetai-bvertex1", self.files["breetai"].read_text())
        self.assertIn("SCOTT scott@breetai", self.files["exedore"].read_text())

    def test_an_unreachable_host_is_reported_and_the_rest_still_done(self) -> None:
        result = self.run_tool("grant", hosts=f"gloval|false|/nowhere\nbreetai|local|{self.files['breetai']}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gloval: unreachable", result.stdout)
        self.assertIn("breetai-bvertex1", self.files["breetai"].read_text())

    def test_a_comment_that_is_not_the_seats_tag_is_refused(self) -> None:
        (self.pubkeys / "max1.pub").write_text(f"ssh-ed25519 {self.keys['max1']} dolza-max1\n")
        result = self.run_tool("grant", "max1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("want 'exedore-max1'", result.stderr)
        self.assertFalse(self.files["gloval"].exists())

    def test_an_unknown_seat_is_refused(self) -> None:
        result = self.run_tool("revoke", "vertex9")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown seat", result.stderr)

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
        fake = self.root / "fakebin"
        fake.mkdir()
        (fake / "grep").write_text("#!/bin/sh\nexit 2\n")
        (fake / "grep").chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake}:{os.environ['PATH']}",
            "SEAT_SSH_PUBKEYS": str(self.pubkeys),
            "SEAT_SSH_HOSTS": f"exedore|bash -c|{self.files['exedore']}",
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), "grant"], env=env, capture_output=True, text=True, check=False
        )
        self.assertIn("exedore: unreachable or failed", result.stdout)
        self.assertEqual(self.files["exedore"].read_text(), "ssh-ed25519 SCOTT scott@breetai\n")
        self.assertEqual([p.name for p in self.files["exedore"].parent.iterdir()], ["authorized_keys"])

    def test_a_public_key_that_is_not_base64_is_refused(self) -> None:
        (self.pubkeys / "dmax1.pub").write_text("ssh-ed25519 AA,AA dolza-dmax1\n")
        result = self.run_tool("grant", "dmax1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not base64", result.stderr)

    def test_an_unreadable_file_is_never_replaced(self) -> None:
        # A path that is not a readable regular file is refused, untouched.
        target = self.files["exedore"]
        target.unlink()
        target.mkdir()
        (target / "kept").write_text("x\n")
        result = self.run_tool("grant")
        self.assertIn("exedore: unreachable or failed", result.stdout)
        self.assertTrue(target.is_dir())
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["kept"])

    def test_a_seat_name_is_never_a_pattern(self) -> None:
        result = self.run_tool("revoke", ".*")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a seat name", result.stderr)

    def test_list_shows_the_seats_each_host_accepts(self) -> None:
        self.run_tool("grant", "vertex1", "max1")
        rows = dict(line.split(":", 1) for line in self.run_tool("list").stdout.splitlines())
        self.assertEqual(sorted(rows["gloval"].split()), ["max1", "vertex1"])
        self.assertEqual(rows["dolza"].split(), ["vertex1"])


SEAT = Path(__file__).resolve().parent.parent / "tools/seat-ssh/seat"


class SeatAttachTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name)
        # podman and ssh say how they were called, then run the command they
        # carry, so the target picked inside the container is what tmux
        # prints. tmux has a claude-agent session only when CLAUDE_AGENT is set.
        stubs = {
            "podman": 'echo podman "$1" "$2" "$3" >&2; shift 3; exec "$@"',
            "ssh": 'echo ssh "$1" "$2" >&2; shift 2; exec sh -c "$*"',
            "tmux": 'case "$*" in "-L claude-agent has-session -t =claude-agent") '
            '[ -n "${CLAUDE_AGENT:-}" ]; exit;; esac; echo tmux "$@"',
        }
        for tool, body in stubs.items():
            (self.bin / tool).write_text(f"#!/bin/sh\n{body}\n")
            (self.bin / tool).chmod(0o755)

    def attach(self, link: str, *args: str, claude_agent: bool = False) -> subprocess.CompletedProcess[str]:
        (self.bin / link).symlink_to(SEAT)
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        env.pop("CLAUDE_AGENT", None)
        if claude_agent:
            env["CLAUDE_AGENT"] = "1"
        return subprocess.run(
            [str(self.bin / link), *args], env=env, capture_output=True, text=True, check=False
        )

    STYLE = (
        "set -g status-style bg=default,fg=colour244 ; set -g mouse off ; "
        "set -g window-status-current-style fg=colour250,bold ; "
        "set -g terminal-overrides ,*:Tc ; attach -t"
    )

    def test_each_kind_of_seat_reaches_its_container(self) -> None:
        for link, container in (("v2", "vertex2"), ("m4", "dmax4"), ("prime", "josui")):
            with self.subTest(link=link):
                result = self.attach(link)
                self.assertEqual(result.stderr.strip(), f"podman exec -it {container}")
                self.assertEqual(result.stdout.strip(), f"tmux -u {self.STYLE} =dev:0")
        # Through ssh the command survives the remote shell's word splitting.
        remote = self.attach("x1")
        self.assertEqual(remote.stderr.split("\n")[:2], ["ssh -t exedore", "podman exec -it max1"])
        self.assertEqual(remote.stdout.strip(), f"tmux -u {self.STYLE} =dev:0")

    def test_claude_agent_is_attached_on_its_own_socket(self) -> None:
        for link in ("prime", "m2", "x3"):
            with self.subTest(link=link):
                result = self.attach(link, claude_agent=True)
                self.assertEqual(
                    result.stdout.strip(), f"tmux -u -L claude-agent {self.STYLE} =claude-agent:0"
                )

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
        self.assertTrue(self.attach("v3", "shell").stdout.strip().endswith("-t =dev:1"))

    def test_shell_under_claude_agent_says_there_is_none(self) -> None:
        result = self.attach("prime", "shell", claude_agent=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no shell window under claude-agent", result.stderr)
        self.assertNotIn("attach", result.stdout)

    def test_an_unknown_name_prints_usage(self) -> None:
        result = self.attach("v9")
        self.assertEqual(result.returncode, 2)
        self.assertIn("link this script", result.stdout)


if __name__ == "__main__":
    unittest.main()
