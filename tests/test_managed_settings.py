"""The managed settings the image ships, checked for what must and must not be there."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent.parent / "rootfs/etc/claude-code/managed-settings.json"


class ManagedSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = json.loads(SETTINGS.read_text())

    def test_the_gateway_is_the_only_mcp_server(self) -> None:
        self.assertEqual(self.settings["allowedMcpServers"], [{"serverName": "trentina"}])
        self.assertTrue(self.settings["disableClaudeAiConnectors"])

    def test_both_hooks_run_on_every_tool_call(self) -> None:
        for event, mode in (("PreToolUse", "breaker"), ("PostToolUse", "audit")):
            (entry,) = self.settings["hooks"][event]
            self.assertEqual(entry["matcher"], "")
            self.assertTrue(entry["hooks"][0]["command"].endswith(f"hooks.py {mode}"))

    def test_remote_control_can_fetch_its_feature_flags(self) -> None:
        # Remote Control exits at start when this is set: it disables the
        # feature-flag fetch along with telemetry.
        self.assertNotIn("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", self.settings["env"])
        self.assertEqual(self.settings["env"]["DISABLE_TELEMETRY"], "1")


if __name__ == "__main__":
    unittest.main()
