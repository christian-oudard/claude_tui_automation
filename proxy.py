"""PTY proxy for Claude Code with state-aware message injection.

Wraps a child process in a PTY, forwarding stdin/stdout bidirectionally.
Monitors terminal output for OSC 0 title sequences to detect Claude Code's
state (busy/idle). When idle and quiet, injects pending inbox messages as
user input.
"""

import errno
import fcntl
import os
import select
import signal
import struct
import sys
import termios
import tty
from pathlib import Path

from .inbox import receive
from .state import StateMachine


def _set_nonblock(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _copy_winsize(src_fd: int, dst_fd: int) -> None:
    """Copy terminal window size from src to dst."""
    try:
        packed = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def _inject_message(master_fd: int, message: str) -> None:
    """Inject a message into the PTY as if the user typed it.

    Uses bracketed paste to avoid triggering character-by-character
    handling in the Ink REPL, then sends Enter to submit.
    """
    # Bracketed paste: ESC[200~ ... ESC[201~
    payload = f"\x1b[200~{message}\x1b[201~\r".encode()
    os.write(master_fd, payload)


def run(
    argv: list[str],
    inbox_base: Path | None = None,
    agent_id: str | None = None,
    env: dict[str, str] | None = None,
    quiet_ms: int = 1000,
    poll_interval_ms: int = 500,
) -> int:
    """Run a command under a PTY proxy with message injection.

    argv: command and arguments to run (e.g. ["claude", "--dangerously-skip-permissions"])
    inbox_base: base directory for inboxes (contains inbox/<agent_id>/)
    agent_id: this agent's ID (for receiving messages)
    env: environment for the child process
    quiet_ms: ms of silence before considering safe to inject
    poll_interval_ms: how often to check inbox when idle

    Returns the child's exit code.
    """
    # Open PTY pair
    master_fd, slave_fd = os.openpty()

    # Copy current terminal size to the PTY
    if os.isatty(sys.stdin.fileno()):
        _copy_winsize(sys.stdin.fileno(), master_fd)

    pid = os.fork()
    if pid == 0:
        # Child: set up slave as controlling terminal
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.execvpe(argv[0], argv, env or os.environ)
        # unreachable

    # Parent: proxy I/O
    os.close(slave_fd)
    _set_nonblock(master_fd)

    state = StateMachine(quiet_ms=quiet_ms)
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()

    # Save and set raw mode on real terminal
    is_tty = os.isatty(stdin_fd)
    old_attrs = None
    if is_tty:
        old_attrs = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
        _set_nonblock(stdin_fd)

    # Forward SIGWINCH to the PTY
    def _on_winch(signum, frame):
        if is_tty:
            _copy_winsize(stdin_fd, master_fd)
            os.kill(pid, signal.SIGWINCH)

    prev_winch = signal.signal(signal.SIGWINCH, _on_winch)

    exit_code = 0
    try:
        _proxy_loop(
            master_fd, stdin_fd, stdout_fd, pid, state,
            inbox_base, agent_id, poll_interval_ms,
        )
    except _ChildExited as e:
        exit_code = e.code
    finally:
        os.close(master_fd)
        if old_attrs is not None:
            termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old_attrs)
        signal.signal(signal.SIGWINCH, prev_winch)

    return exit_code


class _ChildExited(Exception):
    def __init__(self, code: int):
        self.code = code


def _proxy_loop(
    master_fd: int,
    stdin_fd: int,
    stdout_fd: int,
    child_pid: int,
    state: StateMachine,
    inbox_base: Path | None,
    agent_id: str | None,
    poll_interval_ms: int,
) -> None:
    """Select loop: forward stdin<->PTY, detect state, inject messages."""
    poll_sec = poll_interval_ms / 1000.0
    can_inject = inbox_base is not None and agent_id is not None

    while True:
        # Check if child is still alive
        result = os.waitpid(child_pid, os.WNOHANG)
        if result != (0, 0):
            _, wstatus = result
            if os.WIFEXITED(wstatus):
                raise _ChildExited(os.WEXITSTATUS(wstatus))
            raise _ChildExited(128 + os.WTERMSIG(wstatus))

        # Wait for activity on stdin or PTY master
        try:
            rfds, _, _ = select.select([stdin_fd, master_fd], [], [], poll_sec)
        except (select.error, OSError) as e:
            if getattr(e, "errno", None) == errno.EINTR or (
                isinstance(e, OSError) and e.errno == errno.EINTR
            ):
                continue
            raise

        # Forward user input to PTY
        if stdin_fd in rfds:
            try:
                data = os.read(stdin_fd, 4096)
            except OSError:
                data = b""
            if data:
                state.record_user_input()
                os.write(master_fd, data)

        # Forward PTY output to real terminal
        if master_fd in rfds:
            try:
                data = os.read(master_fd, 65536)
            except OSError as e:
                if e.errno == errno.EIO:
                    # PTY closed (child exited)
                    result = os.waitpid(child_pid, 0)
                    _, wstatus = result
                    if os.WIFEXITED(wstatus):
                        raise _ChildExited(os.WEXITSTATUS(wstatus))
                    raise _ChildExited(128 + os.WTERMSIG(wstatus))
                raise
            if data:
                state.feed(data)
                os.write(stdout_fd, data)

        # Check inbox and inject if safe
        if can_inject and state.safe_to_inject():
            messages = receive(inbox_base, agent_id)
            for msg in messages:
                _inject_message(master_fd, msg)
                # After injection, state will go busy (model processes it)
                # so further injection is naturally blocked until idle again
                break  # One message at a time
