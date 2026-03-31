"""File-based message inbox for inter-agent notifications.

Each agent has an inbox directory. Messages are individual files, atomically
created via rename. The receiver reads and removes them.
"""

import os
import tempfile
import time
from pathlib import Path


def inbox_dir(base: Path, agent_id: str) -> Path:
    """Return the inbox directory for an agent."""
    d = base / "inbox" / agent_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def send(base: Path, target_agent_id: str, sender: str, message: str) -> None:
    """Send a message to another agent's inbox.

    Atomic: writes to a temp file then renames into the inbox directory.
    """
    target = inbox_dir(base, target_agent_id)
    fd, tmp = tempfile.mkstemp(dir=target, prefix=".msg-")
    try:
        os.write(fd, f"[from {sender}] {message}".encode())
        os.close(fd)
        # Rename with timestamp prefix for ordering
        ts = f"{time.monotonic_ns():020d}"
        final = target / f"{ts}-{os.path.basename(tmp)}"
        os.rename(tmp, final)
    except Exception:
        os.close(fd) if not os.get_inheritable(fd) else None
        raise


def receive(base: Path, agent_id: str) -> list[str]:
    """Read and remove all pending messages from an agent's inbox.

    Returns list of message strings, oldest first.
    """
    d = inbox_dir(base, agent_id)
    messages = []
    for entry in sorted(d.iterdir()):
        if entry.name.startswith("."):
            continue
        try:
            messages.append(entry.read_text())
            entry.unlink()
        except FileNotFoundError:
            pass  # Race: another reader got it
    return messages
