"""Tests for the MCP inbox server tools."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Set env before importing so module-level reads pick them up
_tmpdir = tempfile.mkdtemp()
os.environ["AGENT_ID"] = "alice"
os.environ["AGENT_INBOX_BASE"] = _tmpdir
os.environ["AGENT_PEERS"] = '{"bob": "test partner"}'

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from mcp_inbox import send_message, list_agents, _inbox_dir, INBOX_BASE


class TestMCPInboxTools(unittest.TestCase):

    def setUp(self):
        # Clear any leftover messages
        for agent in ("alice", "bob"):
            d = Path(_tmpdir) / "inbox" / agent
            if d.exists():
                for f in d.iterdir():
                    f.unlink()

    def test_send_creates_message(self):
        result = send_message("bob", "hello")
        self.assertEqual(result, "Message sent to bob")

        d = Path(_tmpdir) / "inbox" / "bob"
        files = [f for f in d.iterdir() if not f.name.startswith(".")]
        self.assertEqual(len(files), 1)
        self.assertIn("[from alice]", files[0].read_text())

    def test_message_includes_sender(self):
        send_message("bob", "test payload")
        d = Path(_tmpdir) / "inbox" / "bob"
        files = [f for f in d.iterdir() if not f.name.startswith(".")]
        content = files[0].read_text()
        self.assertTrue(content.startswith("[from alice]"))
        self.assertIn("test payload", content)

    def test_send_rejects_unknown_target(self):
        result = send_message("nobody", "hello")
        self.assertIn("unknown agent", result)
        self.assertIn("bob", result)

    def test_list_agents(self):
        result = list_agents()
        self.assertIn("alice", result)
        self.assertIn("bob", result)
        self.assertIn("test partner", result)


class TestMCPJsonGeneration(unittest.TestCase):
    """Test that .mcp.json can be generated for Session launch."""

    def test_generate_mcp_config(self):
        import json
        inbox_base = "/tmp/test-inbox"
        python = sys.executable
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "mcp_inbox.py"
        )

        config = {
            "mcpServers": {
                "agent-inbox": {
                    "command": python,
                    "args": [server_path],
                    "env": {
                        "AGENT_ID": "agent_a",
                        "AGENT_INBOX_BASE": inbox_base,
                    },
                },
            },
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f, indent=2)
            f.flush()
            # Verify it's valid JSON
            with open(f.name) as r:
                loaded = json.load(r)
            self.assertEqual(
                loaded["mcpServers"]["agent-inbox"]["env"]["AGENT_ID"],
                "agent_a",
            )
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
