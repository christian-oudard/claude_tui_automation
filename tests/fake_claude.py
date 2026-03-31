#!/usr/bin/env python3
"""Fake Claude Code TUI for testing.

Simulates the Claude Code terminal UI protocol with full screen redraws
(like Ink). Maintains conversation history and redraws the full layout
on each state change.

Reads stdin char-by-char (PTY delivers \\r not \\n), writes stdout.
"""

import os
import sys
import time

# OSC 0 sequences
IDLE_TITLE = "\x1b]0;✳ Claude Code\x07"
BUSY_TITLE = "\x1b]0;⠂ Claude Code\x07"

BAR = "─" * 120

# Conversation history (user prompts + assistant responses)
history = []
# Current overlay state
overlay = None


def emit(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def clear():
    emit("\x1b[2J\x1b[H")


def read_line():
    buf = []
    while True:
        ch = os.read(0, 1)
        if not ch:
            return None
        c = ch[0]
        if c in (0x0D, 0x0A):
            return "".join(buf)
        if c == 0x1B:  # Escape
            return "\x1b"
        if c == 0x20 and not buf:  # Space (for /btw dismiss)
            return " "
        buf.append(chr(c))


def draw_idle():
    clear()
    emit(IDLE_TITLE)
    emit("  Claude Code v0.0.0-fake\r\n")
    emit("  Haiku · Claude API\r\n")
    emit("  /tmp/test\r\n\r\n")
    for role, text in history:
        if role == "user":
            emit(f"❯ {text}\r\n")
        else:
            emit(f"● {text}\r\n")
    emit(f"\r\n{BAR}\r\n")
    emit("❯ \r\n")
    emit(f"{BAR}\r\n")
    emit("  Test Project\r\n")
    emit("  ⏵⏵ bypass permissions on (shift+tab to cycle)\r\n")


def draw_busy():
    clear()
    emit(BUSY_TITLE)
    emit("  Claude Code v0.0.0-fake\r\n")
    emit("  Haiku · Claude API\r\n")
    emit("  /tmp/test\r\n\r\n")
    for role, text in history:
        if role == "user":
            emit(f"❯ {text}\r\n")
        else:
            emit(f"● {text}\r\n")
    emit("\r\n  Working...\r\n")


def draw_btw_loading(question):
    clear()
    emit(f"  /btw {question}\r\n")
    emit("\r\n    Answering...\r\n")


def draw_btw_ready(question, response):
    clear()
    emit(f"  /btw {question}\r\n")
    emit(f"\r\n    {response}\r\n")
    emit("\r\n  Press Space, Enter, or Escape to dismiss\r\n")


def draw_model_menu():
    clear()
    emit("  1. Opus\r\n")
    emit("  2. Sonnet\r\n")
    emit("  3. Haiku\r\n")
    emit("  Esc to exit\r\n")


def draw_status():
    clear()
    emit("  Version: 0.0.0-fake\r\n")
    emit("  Model: haiku\r\n")
    emit("  Esc to dismiss\r\n")


def draw_approval():
    clear()
    emit("  Allow tool Bash?\r\n")
    emit("  Yes / deny\r\n")


def respond(user_input):
    history.append(("user", user_input))
    if "ECHO:" in user_input:
        marker = user_input.split("ECHO:")[-1].strip()
        response = marker
    else:
        response = f"Response to: {user_input}"
    draw_busy()
    time.sleep(0.05)
    history.append(("assistant", response))
    draw_idle()


def main():
    draw_idle()

    while True:
        try:
            line = read_line()
        except OSError:
            break
        if line is None:
            break

        # Escape, Space, or Enter dismisses overlays
        if line in ("\x1b", " ", ""):
            draw_idle()
            continue

        line = line.replace("\x1b[200~", "").replace("\x1b[201~", "")
        line = line.strip()
        if not line:
            continue

        if line == "/btw":
            emit("  Usage: /btw\r\n")
            draw_idle()
        elif line.startswith("/btw "):
            question = line[5:]
            draw_btw_loading(question)
            time.sleep(0.1)
            draw_btw_ready(question, f"This is a test response to: {question}")
        elif line == "/model":
            draw_model_menu()
        elif line == "/status":
            draw_status()
        elif line == "/clear":
            history.clear()
            draw_idle()
        elif line == "/compact":
            draw_busy()
            time.sleep(0.05)
            draw_idle()
        elif line == "__approval__":
            draw_approval()
        else:
            respond(line)


if __name__ == "__main__":
    main()
