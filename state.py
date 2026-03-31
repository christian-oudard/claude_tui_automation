"""Claude Code terminal state detection via OSC 0 title sequences.

Claude Code sets the terminal title (OSC 0) with a prefix that encodes state:
  - '⠂' or '⠐': model is busy (animated spinner, 960ms cycle)
  - '✳': model is idle (static prefix)

The proxy parses these from the raw output stream to decide when it's safe
to inject a notification message via stdin.
"""

import enum
import time

# Prefix characters Claude Code uses in OSC 0 titles
_BUSY_PREFIXES = frozenset(("⠂", "⠐"))
_IDLE_PREFIX = "✳"


class State(enum.Enum):
    UNKNOWN = "unknown"
    BUSY = "busy"
    IDLE = "idle"


class StateMachine:
    """Track Claude Code's UI state from terminal output bytes.

    Feed raw PTY output into feed(). Query state and safe_to_inject().
    """

    def __init__(self, quiet_ms: int = 500):
        self.quiet_ms = quiet_ms
        self.state = State.UNKNOWN
        self.last_title: str | None = None
        self._last_output_time: float = 0.0
        self._last_user_input_time: float = 0.0
        # Buffer for incomplete OSC sequences
        self._osc_buf: bytes = b""
        self._in_osc = False

    def feed(self, data: bytes) -> bytes:
        """Process output bytes. Extracts OSC titles, updates state.

        Returns the data unchanged (passthrough for the terminal).
        """
        self._last_output_time = time.monotonic()
        self._parse_osc(data)
        return data

    def record_user_input(self) -> None:
        """Record that the user typed something (forwarded through proxy)."""
        self._last_user_input_time = time.monotonic()

    def safe_to_inject(self, user_quiet_ms: int = 500) -> bool:
        """Is it safe to inject a message right now?

        Safe when: idle state + no output for quiet_ms + no user input for user_quiet_ms.
        """
        if self.state != State.IDLE:
            return False
        now = time.monotonic()
        if self.quiet_ms > 0:
            if (now - self._last_output_time) * 1000 < self.quiet_ms:
                return False
        if user_quiet_ms > 0:
            if (now - self._last_user_input_time) * 1000 < user_quiet_ms:
                return False
        return True

    def _parse_osc(self, data: bytes) -> None:
        """Scan bytes for OSC 0/2 title sequences."""
        i = 0
        while i < len(data):
            if self._in_osc:
                # Look for terminator: BEL (\x07) or ST (ESC \)
                bel = data.find(b"\x07", i)
                st = data.find(b"\x1b\\", i)
                if bel < 0 and st < 0:
                    # No terminator yet, buffer the rest
                    self._osc_buf += data[i:]
                    return
                # Pick whichever terminator comes first
                if bel >= 0 and (st < 0 or bel < st):
                    end = bel
                    skip = 1  # skip the BEL byte
                elif st >= 0:
                    end = st
                    skip = 2  # skip ESC and backslash
                else:
                    self._osc_buf = b""
                    self._in_osc = False
                    i += 1
                    continue
                self._osc_buf += data[i:end]
                self._finish_osc()
                i = end + skip
            else:
                # Look for OSC start: ESC ]
                esc = data.find(b"\x1b]", i)
                if esc < 0:
                    return
                self._in_osc = True
                self._osc_buf = b""
                i = esc + 2  # skip ESC ]
        return

    def _finish_osc(self) -> None:
        """Process a complete OSC sequence."""
        self._in_osc = False
        try:
            content = self._osc_buf.decode("utf-8", errors="replace")
        except Exception:
            self._osc_buf = b""
            return
        self._osc_buf = b""

        # Parse "N;data" where N is the OSC command number
        semi = content.find(";")
        if semi < 0:
            return
        try:
            cmd = int(content[:semi])
        except ValueError:
            return
        payload = content[semi + 1:]

        # OSC 0 (title+icon) and OSC 2 (window title) carry the title
        if cmd not in (0, 2):
            return

        self.last_title = payload
        self._update_state_from_title(payload)

    def _update_state_from_title(self, title: str) -> None:
        """Derive state from the title prefix character."""
        if not title:
            return
        # The title format is "<prefix> <rest>" where prefix is a single
        # Unicode character. Check first char (or first grapheme).
        first = title[0]
        if first in _BUSY_PREFIXES:
            self.state = State.BUSY
        elif first == _IDLE_PREFIX:
            self.state = State.IDLE
        else:
            self.state = State.UNKNOWN
