"""Terminal UI automation for Claude Code."""

from .automation import Session
from .state import State, StateMachine
from .inbox import send, receive
from .proxy import run

__all__ = ["Session", "State", "StateMachine", "send", "receive", "run"]
