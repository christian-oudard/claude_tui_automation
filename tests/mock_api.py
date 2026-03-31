"""Mock Anthropic API server for testing.

Runs a local HTTP server that mimics the Anthropic Messages API with
streaming SSE responses. Designed for testing the real Claude Code binary
without network access.

The mock echoes back text after "ECHO:" in the user message, or returns
"MOCK_RESPONSE" by default. Supports configurable response callbacks.

Usage:
    server = MockAPIServer()
    server.start()
    # ... launch Claude Code with ANTHROPIC_BASE_URL=server.url ...
    server.stop()
"""

import http.server
import json
import os
import threading


def _default_responder(messages: list[dict]) -> str:
    """Default response logic: echo ECHO: markers, else generic response."""
    last = messages[-1] if messages else {}
    content = last.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            c.get("text", "") for c in content if c.get("type") == "text"
        )
    if "ECHO:" in content:
        return content.split("ECHO:")[-1].strip()
    return "MOCK_RESPONSE"


def _make_sse_response(text: str, msg_id: str = "msg_mock") -> list[dict]:
    """Build the SSE event list for a streaming response."""
    return [
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-haiku-4-5-20251001",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": len(text.split())},
        },
        {"type": "message_stop"},
    ]


class MockAPIServer:
    """Local mock of the Anthropic Messages API."""

    def __init__(self, port: int = 0, responder=None):
        self.responder = responder or _default_responder
        self.requests: list[dict] = []
        self._port = port
        self._server = None
        self._thread = None

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self._port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self, base: dict | None = None) -> dict:
        """Return env dict with API keys and base URL set."""
        e = dict(base or os.environ)
        e["ANTHROPIC_BASE_URL"] = self.url
        e["ANTHROPIC_API_KEY"] = "sk-ant-mock-key-for-testing"
        return e

    def start(self):
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                parent.requests.append({"path": self.path, "body": body})

                messages = body.get("messages", [])
                text = parent.responder(messages)
                events = _make_sse_response(text, f"msg_{len(parent.requests)}")

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for e in events:
                    self.wfile.write(
                        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()
                    )
                self.wfile.flush()

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def log_message(self, *a):
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
