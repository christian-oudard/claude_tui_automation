"""Tests for the file-based message inbox."""

import tempfile
import unittest
from pathlib import Path

from claude_tui_automation.inbox import inbox_dir, send, receive


class TestInbox(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.base = Path(self.tmpdir)

    def test_send_creates_message_file(self):
        send(self.base, "2", "cave@1", "hello")
        d = inbox_dir(self.base, "2")
        files = [f for f in d.iterdir() if not f.name.startswith(".")]
        self.assertEqual(len(files), 1)
        self.assertIn("[from cave@1] hello", files[0].read_text())

    def test_receive_reads_and_removes(self):
        send(self.base, "3", "cave@1", "msg1")
        send(self.base, "3", "cave@2", "msg2")
        msgs = receive(self.base, "3")
        self.assertEqual(len(msgs), 2)
        self.assertIn("[from cave@1] msg1", msgs[0])
        self.assertIn("[from cave@2] msg2", msgs[1])
        # Inbox should be empty now
        self.assertEqual(receive(self.base, "3"), [])

    def test_receive_empty_inbox(self):
        self.assertEqual(receive(self.base, "99"), [])

    def test_send_to_nonexistent_agent_creates_dir(self):
        send(self.base, "42", "cave@1", "hi")
        self.assertTrue(inbox_dir(self.base, "42").exists())

    def test_multiple_sends_ordered(self):
        for i in range(5):
            send(self.base, "1", "cave@0", f"msg{i}")
        msgs = receive(self.base, "1")
        self.assertEqual(len(msgs), 5)
        for i, msg in enumerate(msgs):
            self.assertIn(f"msg{i}", msg)


if __name__ == "__main__":
    unittest.main()
