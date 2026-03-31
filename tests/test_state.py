"""Tests for Claude Code terminal state detection.

State is derived from OSC 0 (terminal title) sequences emitted by Claude Code:
  - Busy:  title prefix is '⠂' or '⠐' (animated spinner, 960ms cycle)
  - Idle:  title prefix is '✳' (static)

Safe to inject when: idle title + no output for quiet_ms + no user input for quiet_ms.
"""

import time
import unittest

from claude_tui_automation.state import StateMachine, State


class TestOSCParsing(unittest.TestCase):
    """OSC 0 title extraction from raw terminal output."""

    def test_osc0_bel_terminated(self):
        sm = StateMachine()
        # OSC 0 ; ⠂ Claude Code BEL
        sm.feed(b"\x1b]0;\xe2\xa0\x82 Claude Code\x07")
        self.assertEqual(sm.last_title, "\u2802 Claude Code")

    def test_osc0_st_terminated(self):
        sm = StateMachine()
        # OSC 0 ; <title> ST (ESC \)
        sm.feed(b"\x1b]0;\xe2\x9c\xb3 Claude Code\x1b\\")
        self.assertEqual(sm.last_title, "✳ Claude Code")

    def test_osc0_among_other_output(self):
        sm = StateMachine()
        sm.feed(b"Hello world\x1b]0;\xe2\xa0\x82 My Session\x07more text")
        self.assertEqual(sm.last_title, "⠂ My Session")

    def test_multiple_osc0_keeps_latest(self):
        sm = StateMachine()
        sm.feed(b"\x1b]0;\xe2\xa0\x82 Claude Code\x07")
        sm.feed(b"\x1b]0;\xe2\x9c\xb3 Claude Code\x07")
        self.assertEqual(sm.last_title, "✳ Claude Code")

    def test_non_osc0_ignored(self):
        sm = StateMachine()
        # OSC 2 (set window title only) - should also be parsed
        sm.feed(b"\x1b]2;some title\x07")
        self.assertEqual(sm.last_title, "some title")

    def test_osc8_hyperlink_ignored(self):
        sm = StateMachine()
        sm.feed(b"\x1b]8;;https://example.com\x07click\x1b]8;;\x07")
        self.assertIsNone(sm.last_title)

    def test_partial_osc_buffered(self):
        """OSC split across two feed() calls."""
        sm = StateMachine()
        sm.feed(b"\x1b]0;\xe2\xa0")
        self.assertIsNone(sm.last_title)
        sm.feed(b"\x82 Claude Code\x07")
        self.assertEqual(sm.last_title, "⠂ Claude Code")


class TestStateDetection(unittest.TestCase):
    """State machine transitions based on title prefix."""

    def _busy_title(self):
        return b"\x1b]0;\xe2\xa0\x82 Claude Code\x07"

    def _idle_title(self):
        return b"\x1b]0;\xe2\x9c\xb3 Claude Code\x07"

    def test_initial_state_is_unknown(self):
        sm = StateMachine()
        self.assertEqual(sm.state, State.UNKNOWN)

    def test_busy_prefix_sets_busy(self):
        sm = StateMachine()
        sm.feed(self._busy_title())
        self.assertEqual(sm.state, State.BUSY)

    def test_idle_prefix_sets_idle(self):
        sm = StateMachine()
        sm.feed(self._idle_title())
        self.assertEqual(sm.state, State.IDLE)

    def test_second_spinner_frame(self):
        sm = StateMachine()
        sm.feed(b"\x1b]0;\xe2\xa0\x90 Claude Code\x07")  # ⠐
        self.assertEqual(sm.state, State.BUSY)

    def test_transition_busy_to_idle(self):
        sm = StateMachine()
        sm.feed(self._busy_title())
        self.assertEqual(sm.state, State.BUSY)
        sm.feed(self._idle_title())
        self.assertEqual(sm.state, State.IDLE)

    def test_no_title_prefix_treated_as_unknown(self):
        sm = StateMachine()
        sm.feed(b"\x1b]0;Claude Code\x07")
        # No recognized prefix - unknown
        self.assertEqual(sm.state, State.UNKNOWN)


class TestInjectSafety(unittest.TestCase):
    """Injection is only safe when idle + quiet."""

    def _idle_title(self):
        return b"\x1b]0;\xe2\x9c\xb3 Claude Code\x07"

    def _busy_title(self):
        return b"\x1b]0;\xe2\xa0\x82 Claude Code\x07"

    def test_not_safe_when_busy(self):
        sm = StateMachine(quiet_ms=0)
        sm.feed(self._busy_title())
        self.assertFalse(sm.safe_to_inject())

    def test_not_safe_when_unknown(self):
        sm = StateMachine(quiet_ms=0)
        self.assertFalse(sm.safe_to_inject())

    def test_safe_when_idle_and_quiet(self):
        sm = StateMachine(quiet_ms=0)
        sm.feed(self._idle_title())
        self.assertTrue(sm.safe_to_inject())

    def test_not_safe_when_idle_but_recent_output(self):
        sm = StateMachine(quiet_ms=500)
        sm.feed(self._idle_title())
        # Just received output, not quiet yet
        self.assertFalse(sm.safe_to_inject())

    def test_not_safe_when_idle_but_recent_user_input(self):
        sm = StateMachine(quiet_ms=0)
        sm.feed(self._idle_title())
        sm.record_user_input()
        self.assertFalse(sm.safe_to_inject(user_quiet_ms=500))

    def test_safe_after_quiet_period(self):
        sm = StateMachine(quiet_ms=50)
        sm.feed(self._idle_title())
        time.sleep(0.06)
        self.assertTrue(sm.safe_to_inject())

    def test_output_after_idle_resets_quiet(self):
        sm = StateMachine(quiet_ms=50)
        sm.feed(self._idle_title())
        time.sleep(0.06)
        self.assertTrue(sm.safe_to_inject())
        sm.feed(b"some output")
        self.assertFalse(sm.safe_to_inject())


if __name__ == "__main__":
    unittest.main()
