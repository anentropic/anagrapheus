"""
A local HTTP server for the UI: three static files, one event stream, one button.

Standard library only. A demo that needed a web framework to show itself would be
asking a reader to install one to look at a negotiation, and everything here is a
single local user: ``ThreadingHTTPServer`` gives each open stream its own thread, which
is all the concurrency there is.

- ``GET /`` and ``/static/*`` -- the page.
- ``GET /config`` -- the ``--model`` specs the server was started with, to prefill.
- ``GET /events`` -- server-sent events, one :class:`~.session.Hub` message each, with
  the message's index as its id so a reconnecting ``EventSource`` resumes rather than
  replays.
- ``POST /run`` -- ``{"models": [...]}``: start a negotiation, or say why not.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from examples.negotiation.ui.session import Hub, Runner

__all__ = ["serve"]

_STATIC = Path(__file__).parent / "static"
_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}

#: How long a stream waits for news before sending a keep-alive comment.
_HEARTBEAT = 15.0


class _Handler(BaseHTTPRequestHandler):
    hub: ClassVar[Hub]
    runner: ClassVar[Runner]
    specs: ClassVar[list[str]]

    def do_GET(self) -> None:  # noqa: N802 -- http.server's naming
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._file(_STATIC / "index.html")
        elif path.startswith("/static/"):
            self._file(_STATIC / path.removeprefix("/static/"))
        elif path == "/config":
            self._json(HTTPStatus.OK, {"models": self.specs})
        elif path == "/events":
            self._stream()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 -- http.server's naming
        if self.path != "/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            models = [str(m) for m in body.get("models", []) if str(m).strip()]
        except ValueError, AttributeError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": 'expected {"models": [...]}'})
            return
        error = self.runner.start(models)
        if error is None:
            self._json(HTTPStatus.ACCEPTED, {"ok": True})
        else:
            self._json(HTTPStatus.BAD_REQUEST, {"error": error})

    def _stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        last = self.headers.get("Last-Event-ID")
        n = int(last) + 1 if last and last.isdigit() else 0
        try:
            while True:
                messages = self.hub.since(n, timeout=_HEARTBEAT)
                if not messages:
                    self.wfile.write(b": keep-alive\n\n")
                for message in messages:
                    data = json.dumps(message, separators=(",", ":"))
                    self.wfile.write(f"id: {message['n']}\ndata: {data}\n\n".encode())
                n += len(messages)
                self.wfile.flush()
        except BrokenPipeError, ConnectionResetError:
            return

    def _file(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(_STATIC.resolve()) or not resolved.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _TYPES.get(resolved.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ANN401
        """Quiet: the stream would log a line per reconnect, and the run logs plenty."""


def serve(
    hub: Hub, runner: Runner, specs: list[str], *, host: str, port: int
) -> ThreadingHTTPServer:
    """Bind the server; the caller runs ``serve_forever`` on it."""
    handler = type("Handler", (_Handler,), {"hub": hub, "runner": runner, "specs": specs})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
