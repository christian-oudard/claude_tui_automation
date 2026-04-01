"""End-to-end test: agents communicate via MCP inbox using claude --print.

Uses the real Claude Code CLI in non-interactive (--print) mode with real API
calls to prove that the MCP inbox server works end-to-end. Each agent gets
its own .mcp.json config. Receiving is handled by the proxy (synchronous
injection), so these tests only verify send_message and list_agents.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from claude_tui_automation.multi import agent_env, MCP_SERVER


def has_claude():
    return shutil.which("claude") is not None


def claude_print(mcp_config: str, prompt: str, env: dict, timeout: int = 60) -> str:
    """Run claude --print with MCP config and return output."""
    result = subprocess.run(
        [
            "claude", "--print",
            "--mcp-config", mcp_config,
            "--model", "haiku",
            "--dangerously-skip-permissions",
            prompt,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude --print failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout


@unittest.skipUnless(has_claude(), "claude CLI not installed")
class TestMCPEndToEnd(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.inbox_base = self.tmpdir / "inboxes"
        self.inbox_base.mkdir()

    def _setup_agent(self, agent_id, peers):
        """Create workspace and env for an agent, return (mcp_config_path, env)."""
        cwd = self.tmpdir / agent_id
        cwd.mkdir(exist_ok=True)
        env = agent_env(agent_id, self.inbox_base, cwd, peers=peers)
        mcp_config = str(cwd / ".mcp.json")
        return mcp_config, env

    def test_list_agents(self):
        """Agent can discover peers via list_agents tool."""
        mcp_cfg, env = self._setup_agent("agent_a", {"agent_b": "code reviewer"})

        output = claude_print(
            mcp_cfg,
            "Use the list_agents tool and tell me the names of the other agents.",
            env,
        )
        self.assertIn("agent_b", output)

    def test_send_creates_inbox_file(self):
        """Agent A sends a message, verify it lands in agent B's inbox on disk."""
        mcp_a, env_a = self._setup_agent(
            "agent_a", {"agent_b": "partner"},
        )

        # Agent A sends a message to agent B
        claude_print(
            mcp_a,
            "Use the send_message tool to send the message 'PING_FROM_A' "
            "to agent_b. Just call the tool, say nothing else.",
            env_a,
        )

        # Verify message file exists in agent_b's inbox
        inbox_dir = self.inbox_base / "inbox" / "agent_b"
        files = [f for f in inbox_dir.iterdir() if not f.name.startswith(".")]
        self.assertTrue(len(files) > 0, f"No message files in {inbox_dir}")
        content = files[0].read_text()
        self.assertIn("PING_FROM_A", content)
        self.assertIn("[from agent_a]", content)


if __name__ == "__main__":
    unittest.main()
