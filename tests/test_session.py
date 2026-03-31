"""Integration tests using a fake Claude TUI process.

Tests the full Session lifecycle (PTY spawn, read loop, screen parsing,
state detection) without network access. The fake_claude.py script
simulates the Claude Code terminal protocol.
"""

import os
import sys
import time
import unittest

from claude_tui_automation.automation import Session

FAKE_CLAUDE = [
    sys.executable,
    os.path.join(os.path.dirname(__file__), "fake_claude.py"),
]

# Low quiet_ms for fast tests (fake responds instantly)
FAST = dict(command=FAKE_CLAUDE, rows=30, cols=120, quiet_ms=50)


class TestSessionLifecycle(unittest.TestCase):

    def test_starts_and_stops(self):
        s = Session(**FAST)
        s.start()
        self.assertTrue(s._running)
        code = s.stop()
        self.assertFalse(s._running)
        self.assertIsInstance(code, int)

    def test_context_manager(self):
        with Session(**FAST) as s:
            self.assertTrue(s._running)
        self.assertFalse(s._running)


class TestSessionIdle(unittest.TestCase):

    def test_reaches_idle_on_startup(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            self.assertTrue(s.is_idle())

    def test_screen_has_content(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            text = s.display_text()
            self.assertIn("Claude Code", text)

    def test_input_line_found(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            inp = s.input_line()
            self.assertIsNotNone(inp)

    def test_status_bar_found(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            bar = s.status_bar()
            self.assertIsNotNone(bar)
            self.assertIn("bypass", bar.lower())

    def test_permissions_bypassed(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            self.assertTrue(s.permissions_bypassed())

    def test_is_idle(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            self.assertTrue(s.is_idle())
            # screen_state() cannot confirm idle (chevron always visible)
            self.assertNotEqual(s.screen_state(), "busy")


class TestSessionPrompt(unittest.TestCase):

    def test_prompt_and_wait(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            output = s.prompt_and_wait("ECHO: ALPHA_BRAVO", timeout=5)
            self.assertIn("ALPHA_BRAVO", output)

    def test_returns_to_idle_after_prompt(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            s.prompt_and_wait("ECHO: TEST", timeout=5)
            self.assertTrue(s.is_idle())

    def test_send_line(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            s.send_line("ECHO: CHARLIE_DELTA")
            time.sleep(0.5)
            text = s.display_text()
            self.assertIn("CHARLIE_DELTA", text)


class TestSessionSlashCommands(unittest.TestCase):

    def test_status_command(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            output = s.run_command("/status")
            self.assertIn("Version", output)

    def test_clear_command(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            s.run_command("/clear")
            self.assertTrue(s.is_idle())

    def test_model_menu(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            s.send_line("/model")
            time.sleep(0.5)
            self.assertTrue(s._detect_menu())
            self.assertEqual(s.screen_state(), "menu")
            s.send_key("escape")
            time.sleep(0.5)


class TestSessionBtw(unittest.TestCase):

    def test_btw_gets_response(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            response = s.btw("test question", timeout=5)
            self.assertIsNotNone(response)
            self.assertIn("test question", response)

    def test_idle_after_btw(self):
        with Session(**FAST) as s:
            s.wait_for_idle(timeout=5)
            s.btw("test question", timeout=5)
            time.sleep(0.3)
            s.wait_for_idle(timeout=5)
            self.assertTrue(s.is_idle())


if __name__ == "__main__":
    unittest.main()
