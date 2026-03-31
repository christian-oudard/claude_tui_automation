# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python library for programmatically controlling the Claude Code CLI through a PTY (pseudo-terminal). It captures screen output via pyte (virtual terminal emulation) and automates interactions: sending prompts, navigating menus, waiting for state changes, approving tools, and injecting messages.

## Build & Test

```bash
# Install in dev mode (uses hatchling, requires Python >= 3.12)
uv pip install -e .

# Run all tests
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_state.py

# Run a single test
python -m pytest tests/test_automation.py::TestScreenParsing::test_input_line
```

Tests that require the real `claude` CLI (`test_real_tui.py`) are auto-skipped when it's not installed. All other tests run offline with no network access.

## Architecture

Four modules, one concern each:

- **`automation.py`** (Session): High-level API. Launches Claude Code in a PTY, feeds output to pyte, exposes methods for input (`send_line`, `send_key`), screen reading (`display_text`, `conversation_lines`, `input_line`, `status_bar`), state detection (`is_idle`, `wait_for_idle`), overlay handling (`dismiss_overlay`, `select_menu_item`, `approve_tool`), and prompt automation (`prompt_and_wait`). A background daemon thread reads the PTY continuously and feeds both the pyte screen and the StateMachine.

- **`state.py`** (StateMachine): Parses OSC 0/2 title sequences from raw PTY bytes to determine IDLE/BUSY/UNKNOWN state. Claude Code sets the terminal title with a prefix character: `✳` = idle, `⠂`/`⠐` = busy. Also tracks output timing for `safe_to_inject()`.

- **`inbox.py`**: File-based atomic message passing between agents. Messages land in `{base}/inbox/{agent_id}/` via tempfile+rename.

- **`proxy.py`** (`run()`): Low-level PTY proxy. Multiplexes stdin and PTY master via select, forwards I/O, detects idle state, and injects inbox messages using bracketed paste.

## Screen Layout

The Claude Code Ink TUI renders as:
```
Rows 0-2:   Header (logo, model, cwd)
Row 3:      Blank
Rows 4+:    Conversation (❯ user, ● assistant)
Row N:      Separator (─────)
Row N+1:    Input (❯ ...)
Row N+2:    Separator (─────)
Row N+3:    Status bar
```

Screen parsing in Session finds separators (rows >80% `─`), then locates input/status/conversation relative to them.

## Testing

- **Unit tests** (`test_state.py`, `test_automation.py`, `test_inbox.py`): Synthetic pyte screens and raw byte sequences. Fast, no subprocesses.
- **Fake CLI tests** (`test_session.py`): Full Session lifecycle against `tests/fake_claude.py`, a standalone script that mimics the Claude Code TUI layout and OSC titles.
- **Real CLI tests** (`test_real_tui.py`): Session against actual `claude` binary, using `tests/mock_api.py` (HTTP server returning streaming SSE) to avoid real API calls.

## Public API

Exported from `__init__.py`: `Session`, `State`, `StateMachine`, `send`, `receive`, `run`.
