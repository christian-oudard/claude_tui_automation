"""High-level automation API for Claude Code via PTY proxy.

Launches Claude Code in a PTY, captures all output via a pyte virtual
terminal, tracks state from the rendered screen, and provides methods
for programmatic interaction: sending prompts, navigating menus,
reading screen regions, and waiting for state changes.
"""

import errno
import fcntl
import os
import re
import select
import signal
import struct
import sys
import termios
import time
import threading

import pyte

from .state import StateMachine, State

COLS = 200
ROWS = 50


def _set_nonblock(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


class Session:
    """Programmatic control of a Claude Code interactive session.

    Uses pyte to maintain a virtual terminal that mirrors what the user
    would see. Screen contents can be read at any time via display(),
    and UI regions (input field, conversation, status bar) are parsed
    from the rendered screen.

    Usage:
        with Session(model="haiku") as s:
            s.wait_for_idle(timeout=30)
            s.send_line("Say hello")
            output = s.wait_for_idle(timeout=30)
            print(s.display_text())
    """

    def __init__(
        self,
        model: str | None = None,
        extra_args: list[str] | None = None,
        quiet_ms: int = 500,
        env: dict[str, str] | None = None,
        rows: int = ROWS,
        cols: int = COLS,
        command: list[str] | None = None,
    ):
        self.model = model
        self.extra_args = extra_args or []
        self.quiet_ms = quiet_ms
        self.env = env
        self.rows = rows
        self.cols = cols
        self._command = command

        self.state_machine = StateMachine(quiet_ms=quiet_ms)
        self._master_fd: int | None = None
        self._child_pid: int | None = None

        # pyte virtual terminal with scrollback history
        self._screen = pyte.HistoryScreen(cols, rows, history=10000)
        self._stream = pyte.Stream(self._screen)
        self._screen_lock = threading.Lock()

        self._reader_thread: threading.Thread | None = None
        self._running = False
        self._exit_code: int | None = None

    def start(self) -> None:
        """Launch Claude Code (or a custom command) in a PTY."""
        if self._command:
            argv = list(self._command)
        else:
            argv = ["claude", "--dangerously-skip-permissions"]
            if self.model:
                argv.extend(["--model", self.model])
            argv.extend(self.extra_args)

        master_fd, slave_fd = os.openpty()

        # Set window size to match our virtual terminal
        winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        pid = os.fork()
        if pid == 0:
            # Child
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            env = self.env or os.environ.copy()
            env["TERM"] = "xterm-256color"
            os.execvpe(argv[0], argv, env)

        # Parent
        os.close(slave_fd)
        _set_nonblock(master_fd)
        self._master_fd = master_fd
        self._child_pid = pid
        self._running = True

        # Capture scrollback via history listener
        self._screen.set_mode(pyte.modes.LNM)

        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True
        )
        self._reader_thread.start()

    def stop(self) -> int:
        """Send Ctrl-C then Ctrl-D to exit, wait for child, return exit code."""
        if not self._running:
            return self._exit_code or 0

        try:
            self.send_raw(b"\x1b")  # Escape
            time.sleep(0.2)
            self.send_raw(b"\x03")  # Ctrl-C
            time.sleep(0.2)
            self.send_raw(b"\x04")  # Ctrl-D (EOF)
        except OSError:
            pass

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self._exit_code is None:
            time.sleep(0.1)

        if self._exit_code is None:
            try:
                os.kill(self._child_pid, signal.SIGKILL)
                _, wstatus = os.waitpid(self._child_pid, 0)
                if os.WIFEXITED(wstatus):
                    self._exit_code = os.WEXITSTATUS(wstatus)
                else:
                    self._exit_code = 128 + os.WTERMSIG(wstatus)
            except OSError:
                self._exit_code = -1

        self._running = False
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        return self._exit_code

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def _read_loop(self) -> None:
        """Background thread: read PTY output, feed state machine and pyte."""
        master_fd = self._master_fd
        while self._running:
            try:
                rfds, _, _ = select.select([master_fd], [], [], 0.1)
            except (OSError, ValueError):
                break

            if master_fd in rfds:
                try:
                    data = os.read(master_fd, 65536)
                except OSError as e:
                    if e.errno == errno.EIO:
                        break
                    if e.errno == errno.EAGAIN:
                        continue
                    break
                if not data:
                    break
                # Feed the OSC state machine (for title-based state detection)
                self.state_machine.feed(data)
                # Feed pyte virtual terminal
                text = data.decode("utf-8", errors="replace")
                with self._screen_lock:
                    self._stream.feed(text)

        self._running = False
        if self._child_pid is not None and self._exit_code is None:
            try:
                _, wstatus = os.waitpid(self._child_pid, 0)
                if os.WIFEXITED(wstatus):
                    self._exit_code = os.WEXITSTATUS(wstatus)
                else:
                    self._exit_code = 128 + os.WTERMSIG(wstatus)
            except ChildProcessError:
                self._exit_code = -1

    # --- Input methods ---

    def send(self, text: str) -> None:
        """Send text as bracketed paste + Enter (like typing a prompt)."""
        payload = f"\x1b[200~{text}\x1b[201~\r".encode()
        os.write(self._master_fd, payload)

    def send_line(self, text: str) -> None:
        """Send text as raw keystrokes + Enter.

        Needed for slash commands which use character-by-character input.
        """
        os.write(self._master_fd, text.encode() + b"\r")

    def send_raw(self, data: bytes) -> None:
        """Send raw bytes to the PTY."""
        os.write(self._master_fd, data)

    def send_key(self, key: str) -> None:
        """Send a named key."""
        keys = {
            "escape": b"\x1b",
            "enter": b"\r",
            "up": b"\x1b[A",
            "down": b"\x1b[B",
            "left": b"\x1b[D",
            "right": b"\x1b[C",
            "tab": b"\t",
            "shift-tab": b"\x1b[Z",
            "ctrl-c": b"\x03",
            "ctrl-d": b"\x04",
        }
        if key not in keys:
            raise ValueError(f"Unknown key: {key!r}. Known: {list(keys)}")
        os.write(self._master_fd, keys[key])

    # --- Screen reading ---

    def display(self) -> list[str]:
        """Return the current terminal screen as a list of strings (one per row).

        This is what pyte has rendered. It's the same as what the user would see.
        """
        with self._screen_lock:
            return list(self._screen.display)

    def display_text(self) -> str:
        """Return the screen as a single string, trailing whitespace stripped."""
        lines = self.display()
        return "\n".join(line.rstrip() for line in lines)

    def history(self) -> list[str]:
        """Return lines that have scrolled off the top of the screen.

        Combined with display(), gives the full terminal output.
        """
        with self._screen_lock:
            result = []
            for row in self._screen.history.top:
                if row:
                    cols = sorted(row.keys())
                    line = "".join(row[c].data for c in range(cols[-1] + 1))
                else:
                    line = ""
                result.append(line)
            return result

    def full_text(self) -> str:
        """Return history + current screen as a single string.

        Use this to search through all output, including content that
        has scrolled off the visible screen.
        """
        lines = self.history() + self.display()
        return "\n".join(line.rstrip() for line in lines)

    def cursor_pos(self) -> tuple[int, int]:
        """Return (row, col) of the cursor position."""
        with self._screen_lock:
            return (self._screen.cursor.y, self._screen.cursor.x)

    def find_line(self, pattern: str, flags: int = 0) -> int | None:
        """Find the first screen row matching a regex. Returns row index or None."""
        compiled = re.compile(pattern, flags)
        for i, line in enumerate(self.display()):
            if compiled.search(line):
                return i
        return None

    def find_all_lines(self, pattern: str, flags: int = 0) -> list[int]:
        """Find all screen rows matching a regex. Returns list of row indices."""
        compiled = re.compile(pattern, flags)
        return [i for i, line in enumerate(self.display()) if compiled.search(line)]

    # --- Screen region parsing ---

    def input_line(self) -> str | None:
        """Read the current input field contents.

        The input field is between two horizontal separator bars near the
        bottom of the screen. Uses cursor position to find the right area.
        """
        lines = self.display()
        # Find separator bars
        bar_rows = self._find_bar_rows()
        if len(bar_rows) >= 2:
            # Input is between the second-to-last and last bars
            input_area_start = bar_rows[-2] + 1
            input_area_end = bar_rows[-1]
            for i in range(input_area_start, input_area_end):
                line = lines[i]
                idx = line.find("❯")
                if idx >= 0:
                    return line[idx + 1:].strip()
        # Fallback: use cursor row
        row, _ = self.cursor_pos()
        # Check lines near cursor
        for offset in range(0, 5):
            for r in (row - offset, row + offset):
                if 0 <= r < len(lines):
                    idx = lines[r].find("❯")
                    if idx >= 0:
                        return lines[r][idx + 1:].strip()
        return None

    def _find_bar_rows(self) -> list[int]:
        """Find rows that are horizontal separator bars (─)."""
        result = []
        for i, line in enumerate(self.display()):
            stripped = line.strip()
            if len(stripped) > 10 and stripped.count("─") > len(stripped) * 0.8:
                result.append(i)
        return result

    def status_bar(self) -> str | None:
        """Read the bottom status bar line.

        The status bar contains permissions state, session info, etc.
        It's typically the line with "bypass permissions" or similar.
        """
        lines = self.display()
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and ("bypass" in stripped.lower()
                           or "permission" in stripped.lower()
                           or "shift+tab" in stripped.lower()):
                return stripped
        return None

    def conversation_lines(self) -> list[str]:
        """Return the visible conversation area.

        Screen layout (from actual observation):
          rows 0-2: header (logo, model, cwd)
          row 3: blank
          rows 4+: conversation turns (❯ for user, ● or ⎿ for assistant)
          ...
          bar row: ────────
          input row: ❯ [input]
          bar row: ────────
          status rows: project name, permissions

        Conversation is everything between the header and the first
        separator bar near the bottom.
        """
        lines = self.display()
        bar_rows = self._find_bar_rows()

        # Skip header (first 3 rows)
        start = 3
        # End at the first separator bar (which bounds the input area)
        end = bar_rows[0] if bar_rows else len(lines)

        return [lines[i].rstrip() for i in range(start, end) if lines[i].strip()]

    # --- State detection ---

    @property
    def state(self) -> State:
        """Current state from OSC 0 title tracking."""
        return self.state_machine.state

    @property
    def title(self) -> str | None:
        return self.state_machine.last_title

    def screen_state(self) -> str:
        """Detect UI state from the rendered screen content.

        Returns one of: 'idle', 'busy', 'waiting', 'menu', 'btw', 'unknown'.

        Screen-based detection can identify overlays and busy states but
        cannot reliably distinguish idle from unknown. The input chevron
        is always visible regardless of state, so it cannot be used as
        an idle signal. Use the OSC title (self.state) for idle detection.
        """
        # Check for /btw overlay
        btw = self._detect_btw()
        if btw:
            return "btw"

        # Check for menu/overlay indicators
        if self._detect_menu():
            return "menu"

        # Check for approval prompt
        if self._detect_approval_prompt():
            return "waiting"

        # Screen alone cannot reliably distinguish busy from idle.
        # Spinner chars appear in both active spinners and completed
        # task summaries ("✻ Worked for 13m"), and the verb is
        # configurable. Use OSC title for busy/idle detection.
        return "unknown"

    def is_idle(self) -> bool:
        """Check if the session is idle.

        Uses OSC title as the primary idle signal. Screen-based detection
        can identify busy/overlay states but cannot confirm idle (the
        input chevron is always visible regardless of state).
        """
        ss = self.screen_state()
        if ss in ("busy", "menu", "waiting", "btw"):
            return False
        return self.state == State.IDLE

    # --- Menu and overlay detection ---

    def _detect_menu(self) -> bool:
        """Detect if a menu/overlay is currently showing."""
        text = self.display_text()
        return any(marker in text for marker in (
            "Esc to cancel", "Esc to dismiss", "Esc to exit",
            "Enter to confirm",
        ))

    def _detect_approval_prompt(self) -> bool:
        """Detect if a tool approval prompt is showing."""
        text = self.display_text()
        return ("Allow" in text and ("deny" in text.lower() or "Yes" in text)) or \
               "Do you want to" in text

    def _detect_btw(self) -> str | None:
        """Detect if a /btw overlay is showing.

        Returns 'loading' if spinner/answering is visible, 'ready' if the
        response is loaded (dismiss footer visible), or None if no /btw overlay.
        """
        text = self.display_text()
        if "Press Space, Enter, or Escape to dismiss" in text:
            return "ready"
        if "Answering..." in text and "/btw" in text:
            return "loading"
        return None

    def _detect_plan_mode(self) -> bool:
        """Detect if Claude is in plan mode."""
        text = self.display_text()
        return "Plan mode" in text or "plan mode" in text

    def menu_items(self) -> list[str]:
        """Read visible menu items when a menu overlay is showing.

        Returns list of menu item strings (e.g. model names, slash commands).
        """
        lines = self.display()
        items = []
        in_menu = False
        for line in lines:
            stripped = line.strip()
            # Menu items often have selection markers or consistent indentation
            if "Esc to cancel" in stripped or "Esc to dismiss" in stripped:
                in_menu = False
                continue
            # Look for lines with bullet/selection markers or consistent formatting
            if stripped and not stripped.startswith("─"):
                # Simple heuristic: non-empty, non-separator lines in the menu area
                if in_menu or self._detect_menu():
                    items.append(stripped)
                    in_menu = True
        return items

    def dismiss_overlay(self) -> None:
        """Dismiss any visible overlay/menu."""
        if self._detect_btw():
            self.send_key("enter")
            time.sleep(0.5)
        elif self._detect_menu() or self._detect_approval_prompt():
            self.send_key("escape")
            time.sleep(0.5)

    def select_menu_item(self, target: str, timeout: float = 5) -> bool:
        """Navigate a menu to find and select an item containing `target`.

        Uses arrow keys to navigate, Enter to select.
        Returns True if item was found and selected.
        """
        deadline = time.monotonic() + timeout
        # First, check if menu is visible
        if not self._detect_menu():
            return False

        # Try each item (up to 20 positions)
        for _ in range(20):
            if time.monotonic() > deadline:
                return False
            text = self.display_text()
            # Check if current selection matches target
            # Menus typically highlight the selected item
            if target.lower() in text.lower():
                self.send_key("enter")
                time.sleep(0.5)
                return True
            self.send_key("down")
            time.sleep(0.2)
        return False

    # --- Tool approval ---

    def approve_tool(self) -> None:
        """Approve a pending tool execution (send 'y')."""
        if self._detect_approval_prompt():
            self.send_raw(b"y")

    def deny_tool(self) -> None:
        """Deny a pending tool execution (send Escape)."""
        if self._detect_approval_prompt():
            self.send_key("escape")

    # --- Permissions ---

    def toggle_permissions(self) -> None:
        """Toggle permissions mode with Shift+Tab."""
        self.send_key("shift-tab")
        time.sleep(0.5)

    def permissions_bypassed(self) -> bool:
        """Check if permissions are currently bypassed."""
        bar = self.status_bar()
        if bar:
            return "bypass" in bar.lower() and "on" in bar.lower()
        return False

    # --- Plan mode ---

    def in_plan_mode(self) -> bool:
        """Check if Claude is currently in plan mode."""
        return self._detect_plan_mode()

    # --- Waiting methods ---

    def wait_for_idle(self, timeout: float = 30) -> str:
        """Wait until idle. Returns screen text."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_idle():
                time.sleep(0.3)
                return self.display_text()
            if not self._running:
                raise RuntimeError(
                    f"Session exited (code={self._exit_code}) "
                    f"while waiting for idle"
                )
            time.sleep(0.1)
        raise TimeoutError(
            f"Timed out after {timeout}s waiting for idle. "
            f"State={self.state}, screen_state={self.screen_state()}"
        )

    def wait_for_busy(self, timeout: float = 10) -> None:
        """Wait until Claude Code enters busy state (via OSC title)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state == State.BUSY:
                return
            if not self._running:
                raise RuntimeError("Session exited while waiting for busy")
            time.sleep(0.05)
        raise TimeoutError(
            f"Timed out after {timeout}s waiting for busy. "
            f"State={self.state}, screen_state={self.screen_state()}"
        )

    def wait_for_screen(
        self, pattern: str, timeout: float = 30, flags: int = 0
    ) -> re.Match:
        """Wait until the screen contains a regex match.

        Checks the full rendered screen (not a raw byte buffer).
        """
        compiled = re.compile(pattern, flags)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.display_text()
            m = compiled.search(text)
            if m:
                return m
            if not self._running:
                raise RuntimeError(
                    f"Session exited while waiting for pattern {pattern!r}"
                )
            time.sleep(0.1)
        raise TimeoutError(
            f"Timed out after {timeout}s waiting for screen pattern {pattern!r}"
        )

    def prompt_and_wait(
        self, text: str, timeout: float = 60
    ) -> str:
        """Send a prompt, wait for response. Returns screen text.

        Waits for BUSY->IDLE transition via OSC title. For very fast
        responses where BUSY is never observed, falls back to detecting
        output settling while OSC reports IDLE.
        """
        self.send_line(text)
        deadline = time.monotonic() + timeout
        saw_busy = False

        while time.monotonic() < deadline:
            if self.state == State.BUSY:
                saw_busy = True

            if saw_busy and self.is_idle():
                time.sleep(0.3)
                return self.display_text()

            # Fallback for very fast responses: if we never saw BUSY but
            # OSC reports IDLE and output has settled, return.
            if not saw_busy and self.state == State.IDLE and \
               (time.monotonic() - self.state_machine._last_output_time) > 2.0:
                return self.display_text()

            if not self._running:
                raise RuntimeError("Session exited while waiting for response")
            time.sleep(0.1)

        raise TimeoutError(
            f"Timed out after {timeout}s waiting for response. "
            f"State={self.state}, screen_state={self.screen_state()}"
        )

    # --- Slash commands ---

    def run_command(self, command: str, timeout: float = 15) -> str:
        """Run a slash command and wait for it to complete.

        Sends the command, waits for any result, dismisses overlays,
        and returns screen text.
        """
        self.send_line(command)
        time.sleep(2)
        # Dismiss any overlay the command opened
        self.dismiss_overlay()
        time.sleep(0.5)
        return self.display_text()

    def compact(self, timeout: float = 30) -> str:
        """Run /compact and wait for completion."""
        self.send_line("/compact")
        # /compact triggers a model call to summarize, so wait for busy->idle
        try:
            self.wait_for_busy(timeout=10)
        except TimeoutError:
            pass
        return self.wait_for_idle(timeout=timeout)

    # --- Resume dialog ---

    def resume_session(self, name_or_index: str, timeout: float = 10) -> bool:
        """Navigate the /resume dialog to select a session.

        name_or_index: session name to search for, or "1", "2" etc for position.
        """
        self.send_line("/resume")
        time.sleep(1)
        if not self._detect_menu():
            return False
        # Try to find the target in the menu
        return self.select_menu_item(name_or_index, timeout=timeout)

    # --- /btw dialog ---

    def btw_response(self) -> str | None:
        """Read the response content from a visible /btw overlay.

        Returns the response text, or None if no /btw overlay is showing.
        The response is between the header ("/btw <question>") and the
        dismiss footer ("Press Space, Enter, or Escape to dismiss").
        """
        state = self._detect_btw()
        if state is None:
            return None
        if state == "loading":
            return ""

        lines = self.display()
        # Find the /btw header line and dismiss footer line
        header_row = None
        footer_row = None
        for i, line in enumerate(lines):
            if "/btw" in line and header_row is None:
                header_row = i
            if "Press Space, Enter, or Escape to dismiss" in line:
                footer_row = i

        if header_row is None or footer_row is None:
            return None

        # Response content is between header+2 (skip blank line after header,
        # content starts at marginTop:1 + marginLeft:2 indent) and footer
        content_lines = []
        for i in range(header_row + 1, footer_row):
            stripped = lines[i].rstrip()
            if stripped.strip():
                content_lines.append(stripped)

        return "\n".join(content_lines).strip() or None

    def btw(self, question: str, timeout: float = 60) -> str | None:
        """Send a /btw side question and return the response.

        Sends "/btw <question>", waits for the response to load, reads
        the content, dismisses the overlay, and returns the response text.
        Returns None if the response couldn't be read.
        """
        self.send_line(f"/btw {question}")

        # Wait for the /btw overlay to appear and finish loading
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._detect_btw()
            if state == "ready":
                break
            if not self._running:
                raise RuntimeError("Session exited while waiting for /btw")
            time.sleep(0.2)
        else:
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for /btw response"
            )

        response = self.btw_response()

        # Dismiss the overlay (Enter is cleaner than Escape in PTY)
        self.send_key("enter")
        time.sleep(0.5)

        return response
