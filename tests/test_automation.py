"""Tests for the automation API internals (no network calls)."""

import threading
import unittest

import pyte

from claude_tui_automation.automation import Session


class TestScreenParsing(unittest.TestCase):
    """Test screen region parsing with synthetic pyte content."""

    def _session_with_lines(self, lines: list[str], rows=40, cols=120) -> Session:
        s = Session.__new__(Session)
        s._screen_lock = threading.Lock()
        s._screen = pyte.Screen(cols, rows)
        stream = pyte.Stream(s._screen)
        for line in lines:
            stream.feed(line + "\r\n")
        return s

    def test_input_line_finds_chevron(self):
        s = self._session_with_lines([
            "Some conversation text",
            "────────────────────────────",
            "❯ hello world",
            "  bypass permissions on",
        ])
        self.assertEqual(s.input_line(), "hello world")

    def test_input_line_empty_prompt(self):
        s = self._session_with_lines(["text", "❯"])
        result = s.input_line()
        self.assertEqual(result, "")

    def test_input_line_not_found(self):
        s = self._session_with_lines(["no chevron here"])
        self.assertIsNone(s.input_line())

    def test_status_bar_finds_permissions(self):
        s = self._session_with_lines([
            "conversation",
            "────────────────────────────",
            "❯ prompt",
            "  bypass permissions on (shift+tab to cycle)",
        ])
        bar = s.status_bar()
        self.assertIn("bypass", bar.lower())

    def test_find_line_regex(self):
        s = self._session_with_lines(["line 0", "line 1 with keyword", "line 2"])
        self.assertEqual(s.find_line(r"keyword"), 1)
        self.assertIsNone(s.find_line(r"missing"))

    def test_display_text_strips_trailing_whitespace(self):
        s = Session.__new__(Session)
        s._screen_lock = threading.Lock()
        s._screen = pyte.Screen(40, 5)
        stream = pyte.Stream(s._screen)
        stream.feed("hello   \r\nworld")
        text = s.display_text()
        lines = text.split("\n")
        self.assertEqual(lines[0], "hello")
        self.assertEqual(lines[1], "world")

    def test_history_captures_scrolled_lines(self):
        s = Session.__new__(Session)
        s._screen_lock = threading.Lock()
        s._screen = pyte.HistoryScreen(80, 5, history=100)
        stream = pyte.Stream(s._screen)
        for i in range(10):
            stream.feed(f"line {i}\r\n")
        hist = s.history()
        self.assertEqual(len(hist), 6)
        self.assertEqual(hist[0], "line 0")
        self.assertEqual(hist[-1], "line 5")

    def test_full_text_includes_history_and_screen(self):
        s = Session.__new__(Session)
        s._screen_lock = threading.Lock()
        s._screen = pyte.HistoryScreen(80, 5, history=100)
        stream = pyte.Stream(s._screen)
        for i in range(10):
            stream.feed(f"line {i}\r\n")
        text = s.full_text()
        for i in range(10):
            self.assertIn(f"line {i}", text)


class TestMenuDetection(unittest.TestCase):
    """Test overlay/menu detection from screen content."""

    def _session_with_text(self, text: str) -> Session:
        s = Session.__new__(Session)
        s._screen_lock = threading.Lock()
        s._screen = pyte.Screen(120, 40)
        stream = pyte.Stream(s._screen)
        stream.feed(text)
        return s

    def test_detects_esc_to_cancel(self):
        s = self._session_with_text("Some menu\r\nEsc to cancel\r\n")
        self.assertTrue(s._detect_menu())

    def test_no_menu_on_normal_screen(self):
        s = self._session_with_text("Normal output\r\n❯ prompt\r\n")
        self.assertFalse(s._detect_menu())

    def test_detects_approval_prompt(self):
        s = self._session_with_text("Allow tool Bash?\r\nYes / deny\r\n")
        self.assertTrue(s._detect_approval_prompt())

    def test_detects_plan_mode(self):
        s = self._session_with_text("Plan mode active\r\n")
        self.assertTrue(s._detect_plan_mode())

    def test_detects_btw_loading(self):
        s = self._session_with_text("/btw what is this?\r\n  Answering...\r\n")
        self.assertEqual(s._detect_btw(), "loading")

    def test_detects_btw_ready(self):
        s = self._session_with_text(
            "/btw what is this?\r\n"
            "  Some response text\r\n"
            "Press Space, Enter, or Escape to dismiss\r\n"
        )
        self.assertEqual(s._detect_btw(), "ready")

    def test_no_btw_on_normal_screen(self):
        s = self._session_with_text("Normal output\r\n❯ prompt\r\n")
        self.assertIsNone(s._detect_btw())

    def test_btw_response_reads_content(self):
        s = self._session_with_text(
            "/btw what is this?\r\n"
            "\r\n"
            "  This is the response content.\r\n"
            "  Second line of response.\r\n"
            "\r\n"
            "Press Space, Enter, or Escape to dismiss\r\n"
        )
        resp = s.btw_response()
        self.assertIn("response content", resp)
        self.assertIn("Second line", resp)

    def test_btw_response_none_without_overlay(self):
        s = self._session_with_text("Normal output\r\n❯ prompt\r\n")
        self.assertIsNone(s.btw_response())

    def test_btw_response_empty_while_loading(self):
        s = self._session_with_text("/btw what is this?\r\n  Answering...\r\n")
        self.assertEqual(s.btw_response(), "")

    def test_screen_state_detects_btw(self):
        s = self._session_with_text(
            "/btw what is this?\r\n"
            "  Some answer\r\n"
            "Press Space, Enter, or Escape to dismiss\r\n"
        )
        self.assertEqual(s.screen_state(), "btw")


if __name__ == "__main__":
    unittest.main()
