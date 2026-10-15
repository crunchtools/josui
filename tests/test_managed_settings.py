"""The managed settings the image ships, checked for what must and must not be there."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "rootfs/etc/claude-code/managed-settings.json"
LIBEXEC = "/usr/local/libexec/josui/"


class ManagedSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = json.loads(SETTINGS.read_text())

    def commands(self, event: str) -> dict[str, str]:
        """Each hook command of an event, mapped to its matcher."""
        return {
            hook["command"]: entry["matcher"]
            for entry in self.settings["hooks"][event]
            for hook in entry["hooks"]
        }

    def test_the_gateway_is_the_only_mcp_server(self) -> None:
        self.assertEqual(self.settings["allowedMcpServers"], [{"serverName": "trentina"}])
        self.assertTrue(self.settings["disableClaudeAiConnectors"])

    def test_breaker_and_audit_run_on_every_tool_call(self) -> None:
        self.assertEqual(self.commands("PreToolUse")[LIBEXEC + "hooks.py breaker"], "")
        self.assertEqual(self.commands("PostToolUse")[LIBEXEC + "hooks.py audit"], "")

    def test_only_managed_hooks_run(self) -> None:
        # A hook in the home volume or in a checkout's .claude/settings.json
        # would otherwise run with prompts off.
        self.assertIs(self.settings["allowManagedHooksOnly"], True)

    def test_every_hook_is_shipped_in_the_image(self) -> None:
        for event in ("PreToolUse", "PostToolUse", "PermissionRequest"):
            for command in self.commands(event):
                self.assertTrue(command.startswith(LIBEXEC), command)
                script = command.removeprefix("/").split()[0]
                self.assertTrue((ROOT / "rootfs" / script).is_file(), script)

    def test_memory_guard_and_allow_hook_are_registered(self) -> None:
        pre = self.commands("PreToolUse")
        self.assertEqual(pre[LIBEXEC + "allow-hook.sh"], "")
        self.assertIn("Bash", pre[LIBEXEC + "deny-local-memory.sh"].split("|"))
        self.assertIn("Write", pre[LIBEXEC + "deny-local-memory.sh"].split("|"))

    def test_allow_hook_answers_the_prompt_itself(self) -> None:
        # A PreToolUse allow does not answer a prompt the session's mode
        # still calls for; PermissionRequest is that prompt.
        self.assertEqual(self.commands("PermissionRequest")[LIBEXEC + "allow-hook.sh"], "")

    def test_web_tools_denied_and_auto_memory_off(self) -> None:
        self.assertEqual(sorted(self.settings["permissions"]["deny"]), ["WebFetch", "WebSearch"])
        self.assertIs(self.settings["autoMemoryEnabled"], False)

    def test_remote_control_can_fetch_its_feature_flags(self) -> None:
        # Remote Control exits at start when this is set: it disables the
        # feature-flag fetch along with telemetry.
        self.assertNotIn("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", self.settings["env"])
        self.assertEqual(self.settings["env"]["DISABLE_TELEMETRY"], "1")


if __name__ == "__main__":
    unittest.main()
