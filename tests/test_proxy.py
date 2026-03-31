"""Tests for the PTY proxy.

These tests spawn real child processes to verify PTY forwarding
and message injection.
"""

import os
import tempfile
import unittest
from pathlib import Path

from claude_tui_automation.inbox import send
from claude_tui_automation.proxy import run


class TestProxyBasic(unittest.TestCase):
    """Basic PTY proxy behavior with simple commands."""

    def test_exit_code_forwarded(self):
        """Child's exit code is returned."""
        code = run(["bash", "-c", "exit 42"])
        self.assertEqual(code, 42)

    def test_zero_exit(self):
        code = run(["true"])
        self.assertEqual(code, 0)

    def test_child_output_reaches_stdout(self):
        """Verify child output is forwarded (captured via pipe)."""
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            os.dup2(w, 1)
            os.close(w)
            code = run(["echo", "hello proxy"])
            os._exit(code)
        os.close(w)
        output = b""
        while True:
            chunk = os.read(r, 4096)
            if not chunk:
                break
            output += chunk
        os.close(r)
        _, status = os.waitpid(pid, 0)
        self.assertIn(b"hello proxy", output)


class TestProxyInjection(unittest.TestCase):
    """Message injection via inbox."""

    def test_inject_into_cat(self):
        """Inject a message into a process reading stdin.

        The child emits a fake OSC 0 idle title to trigger IDLE state,
        then reads from stdin. The proxy detects idle state, injects
        the inbox message, and cat echoes it back.
        """
        tmpdir = tempfile.mkdtemp()
        base = Path(tmpdir)

        # Pre-populate inbox
        send(base, "1", "test", "injected-message")

        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            os.dup2(w, 1)
            os.close(w)
            # Emit fake idle title (✳), then cat reads injected stdin
            script = (
                r'printf "\033]0;\xe2\x9c\xb3 Test\007"; '
                r'timeout 3 head -n1 || true'
            )
            code = run(
                ["bash", "-c", script],
                inbox_base=base,
                agent_id="1",
                quiet_ms=0,
                poll_interval_ms=100,
            )
            os._exit(code)
        os.close(w)
        output = b""
        while True:
            chunk = os.read(r, 4096)
            if not chunk:
                break
            output += chunk
        os.close(r)
        os.waitpid(pid, 0)
        self.assertIn(b"injected-message", output)


if __name__ == "__main__":
    unittest.main()
