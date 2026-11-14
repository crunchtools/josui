"""tools/voice/speak: which answers are read aloud, and when it looks for a new session."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SPEAK = Path(__file__).resolve().parent.parent / "tools/voice/speak"


def load_speak():
    loader = importlib.machinery.SourceFileLoader("speak", str(SPEAK))
    spec = importlib.util.spec_from_loader("speak", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


speak = load_speak()


def user(text: str, **extra) -> dict:
    return {"type": "user", "message": {"content": text}, **extra}


def answer(text: str, stop: str = "end_turn") -> dict:
    return {"type": "assistant", "message": {"stop_reason": stop, "content": [{"type": "text", "text": text}]}}


class WatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "projects"
        (self.root / "proj").mkdir(parents=True)
        self.marker = Path(tmp.name) / "voice-turn"
        self.mtime = 1_000_000

    def session(self, name: str, *entries: dict) -> Path:
        path = self.root / "proj" / f"{name}.jsonl"
        with path.open("a") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        # Explicit, increasing mtimes: "newest" must not depend on clock resolution.
        self.mtime += 10
        os.utime(path, (self.mtime, self.mtime))
        return path

    def test_only_the_answer_to_a_spoken_prompt_is_read(self) -> None:
        path = self.session("a")
        watcher = speak.Watcher(self.root, self.marker)
        self.assertEqual(watcher.tick(), [])
        self.marker.write_text("what is running")
        self.session("a", user("typed from the phone"), answer("silent"))
        self.assertEqual(watcher.tick(), [])
        self.session(
            "a",
            user("what is running"),
            answer("checking", stop="tool_use"),
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
            user("<system-reminder>", isMeta=True),
            answer("Five **containers**, see `podman ps`."),
        )
        self.assertEqual(watcher.tick(), ["Five containers, see podman ps."])
        self.assertFalse(self.marker.exists())
        self.assertEqual(path.name, "a.jsonl")

    def test_what_was_said_before_it_started_is_not_read(self) -> None:
        self.marker.write_text("hello")
        self.session("a", user("hello"), answer("old answer"))
        watcher = speak.Watcher(self.root, self.marker)
        self.assertEqual(watcher.tick(), [])

    def test_a_new_session_is_found_at_the_rescan_and_read_from_its_start(self) -> None:
        self.session("a")
        watcher = speak.Watcher(self.root, self.marker)
        watcher.tick()
        self.marker.write_text("status")
        # A respawned pane: the spoken prompt opens a new file.
        self.session("b", user("status"), answer("All green."))
        spoken = []
        for _ in range(speak.Watcher.RESCAN - 1):
            spoken += watcher.tick()
        self.assertEqual(spoken, [], "found before the rescan tick")
        self.assertEqual(watcher.tick(), ["All green."])

    def test_a_first_session_after_an_empty_start_is_read_from_its_start(self) -> None:
        watcher = speak.Watcher(self.root, self.marker)
        self.assertEqual(watcher.tick(), [])
        self.assertEqual(watcher.tick(), [])
        self.marker.write_text("good morning")
        self.session("a", user("good morning"), answer("Morning, Scott."))
        # With nothing found yet it looks every tick, not every RESCAN.
        self.assertEqual(watcher.tick(), ["Morning, Scott."])

    def test_the_current_file_is_followed_every_tick(self) -> None:
        self.session("a")
        watcher = speak.Watcher(self.root, self.marker)
        watcher.tick()
        self.marker.write_text("one")
        self.session("a", user("one"))
        self.assertEqual(watcher.tick(), [])
        self.session("a", answer("Done."))
        self.assertEqual(watcher.tick(), ["Done."])

    def test_a_partial_line_waits_for_the_rest(self) -> None:
        path = self.session("a")
        watcher = speak.Watcher(self.root, self.marker)
        watcher.tick()
        self.marker.write_text("go")
        line = json.dumps(user("go")) + "\n" + json.dumps(answer("Gone."))
        with path.open("a") as handle:
            handle.write(line[:-5])
        self.assertEqual(watcher.tick(), [])
        with path.open("a") as handle:
            handle.write(line[-5:] + "\n")
        self.assertEqual(watcher.tick(), ["Gone."])


class SpeakableTest(unittest.TestCase):
    def test_markdown_code_and_links_are_dropped(self) -> None:
        text = "## Done\n- see [the PR](https://x/y) and https://z\n```\nrm -rf\n```\nok"
        self.assertEqual(speak.speakable(text), "Done see the PR and a link (code on screen) ok")

    def test_a_long_answer_is_cut_at_a_sentence(self) -> None:
        text = "One sentence here. " * 100
        out = speak.speakable(text)
        self.assertLessEqual(len(out), speak.MAX_CHARS + len(" The rest is on screen."))
        self.assertTrue(out.endswith("here. The rest is on screen."))


if __name__ == "__main__":
    unittest.main()
