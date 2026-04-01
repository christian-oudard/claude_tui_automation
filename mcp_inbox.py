#!/usr/bin/env python3
"""MCP server providing inter-agent messaging tools.

Exposes send_message and list_agents tools to Claude Code sessions.
Messages are received synchronously via proxy injection, not polling.

Configured via environment variables:
    AGENT_ID         - this agent's identifier
    AGENT_INBOX_BASE - shared directory for all agent inboxes
    AGENT_PEERS      - JSON object mapping agent IDs to descriptions,
                       e.g. {"agent_b": "code reviewer", "agent_c": "researcher"}
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Agent Inbox")

AGENT_ID = os.environ.get("AGENT_ID", "")
INBOX_BASE = Path(os.environ.get("AGENT_INBOX_BASE", ""))
PEERS: dict[str, str] = json.loads(os.environ.get("AGENT_PEERS", "{}"))


def _inbox_dir(agent_id: str) -> Path:
    d = INBOX_BASE / "inbox" / agent_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@mcp.tool()
def list_agents() -> str:
    """List the other agents you can communicate with."""
    if not PEERS:
        return "No known agents"
    lines = [f"You are {AGENT_ID}.", "", "Other agents:"]
    for agent_id, description in PEERS.items():
        lines.append(f"  {agent_id} - {description}")
    return "\n".join(lines)


@mcp.tool()
def send_message(target: str, message: str) -> str:
    """Send a message to another agent.

    Args:
        target: The target agent's ID (use list_agents to see available agents).
        message: The message text to send.
    """
    if not AGENT_ID or not INBOX_BASE:
        return "Error: AGENT_ID and AGENT_INBOX_BASE must be set"
    if PEERS and target not in PEERS and target != AGENT_ID:
        available = ", ".join(PEERS.keys())
        return f"Error: unknown agent '{target}'. Available: {available}"

    d = _inbox_dir(target)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".msg-")
    os.write(fd, f"[from {AGENT_ID}] {message}".encode())
    os.close(fd)
    ts = f"{time.monotonic_ns():020d}"
    final = d / f"{ts}-{os.path.basename(tmp)}"
    os.rename(tmp, final)
    return f"Message sent to {target}"


if __name__ == "__main__":
    mcp.run()
