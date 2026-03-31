"""Integration tests against the real Claude Code binary with a mock API.

Launches the actual `claude` CLI but intercepts all API calls with a local
mock server. Tests the full TUI automation pipeline without network access.

These tests validate that our automation correctly handles the real Claude
Code Ink TUI. Run after Claude Code CLI updates to catch regressions.
"""

import shutil
import time
import unittest

from claude_tui_automation.automation import Session
from claude_tui_automation.tests.mock_api import MockAPIServer

TIMEOUT = 20


def has_claude():
    return shutil.which("claude") is not None


class RealTUITestCase(unittest.TestCase):
    """Base class for real TUI tests. Each test class gets its own mock server."""

    @classmethod
    def setUpClass(cls):
        cls.api = MockAPIServer()
        cls.api.start()

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()

    def session(self, **kw):
        return Session(
            model="haiku",
            env=self.api.env(),
            rows=40, cols=120,
            quiet_ms=100,
            **kw,
        )

    def wait_ready(self, s, timeout=TIMEOUT):
        """Wait for the session to be ready, dismissing any startup dialogs."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if s.is_idle():
                return
            # Dismiss any dialogs (API key prompt, etc.)
            if s._detect_menu():
                s.send_key("escape")
                time.sleep(0.5)
            time.sleep(0.2)
        s.wait_for_idle(timeout=1)


@unittest.skipUnless(has_claude(), "claude CLI not installed")
class TestRealTUIStartup(RealTUITestCase):

    def test_reaches_idle(self):
        with self.session() as s:
            self.wait_ready(s)
            self.assertTrue(s.is_idle())

    def test_screen_shows_claude_code(self):
        with self.session() as s:
            self.wait_ready(s)
            self.assertIn("Claude Code", s.display_text())

    def test_input_field_visible(self):
        with self.session() as s:
            self.wait_ready(s)
            self.assertIsNotNone(s.input_line())

    def test_status_bar_visible(self):
        with self.session() as s:
            self.wait_ready(s)
            self.assertIsNotNone(s.status_bar())

    def test_permissions_bypassed(self):
        with self.session() as s:
            self.wait_ready(s)
            self.assertTrue(s.permissions_bypassed())

    def test_osc_title_set(self):
        with self.session() as s:
            self.wait_ready(s)
            self.assertIsNotNone(s.title)
            self.assertIn("Claude", s.title)

    def test_is_idle(self):
        with self.session() as s:
            self.wait_ready(s)
            self.assertTrue(s.is_idle())
            self.assertNotEqual(s.screen_state(), "busy")


@unittest.skipUnless(has_claude(), "claude CLI not installed")
class TestRealTUIPrompt(RealTUITestCase):

    def test_prompt_and_wait(self):
        with self.session() as s:
            self.wait_ready(s)
            output = s.prompt_and_wait("ECHO: ALPHA_BRAVO", timeout=TIMEOUT)
            self.assertIn("ALPHA_BRAVO", output)

    def test_returns_to_idle(self):
        with self.session() as s:
            self.wait_ready(s)
            s.prompt_and_wait("ECHO: TEST", timeout=TIMEOUT)
            self.assertTrue(s.is_idle())

    def test_conversation_visible(self):
        with self.session() as s:
            self.wait_ready(s)
            s.prompt_and_wait("ECHO: VISIBLE", timeout=TIMEOUT)
            text = "\n".join(s.conversation_lines())
            self.assertIn("VISIBLE", text)

    def test_multi_turn(self):
        with self.session() as s:
            self.wait_ready(s)
            s.prompt_and_wait("ECHO: FIRST", timeout=TIMEOUT)
            output = s.prompt_and_wait("ECHO: SECOND", timeout=TIMEOUT)
            self.assertIn("SECOND", output)


@unittest.skipUnless(has_claude(), "claude CLI not installed")
class TestRealTUISlashCommands(RealTUITestCase):

    def test_clear_returns_idle(self):
        with self.session() as s:
            self.wait_ready(s)
            s.run_command("/clear")
            self.assertTrue(s.is_idle())

    def test_status_shows_info(self):
        with self.session() as s:
            self.wait_ready(s)
            output = s.run_command("/status")
            self.assertTrue(
                "Version" in output or "Model" in output or "haiku" in output.lower(),
                f"Unexpected /status output: {output[:200]}",
            )

    def test_model_menu(self):
        with self.session() as s:
            self.wait_ready(s)
            s.send_line("/model")
            time.sleep(2)
            self.assertTrue(s._detect_menu())
            s.send_key("escape")
            time.sleep(1)
            self.assertTrue(s.is_idle())


if __name__ == "__main__":
    unittest.main()
