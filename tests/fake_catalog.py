"""A scripted stand-in for the catalog API, served by stdlib ``http.server`` on localhost."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

TOKEN = "eyJhbGciOiJSUzI1NiJ9.c2VjcmV0LXRva2VuLXBheWxvYWQ.c2lnbmF0dXJl"


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Message
    body: bytes


@dataclass
class Reply:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


Responder = Callable[[Recorded], Reply]


def envelope(payload: Any) -> dict[str, Any]:
    """Wrap a payload the way the backend's ``SingleResponse`` does."""
    return {"response": payload}


def paginated(items: list[Any], page: int, pages: int) -> dict[str, Any]:
    """Shape a list the way the backend's ``PaginationResponse`` does."""
    return {"items": items, "metadata": {"page": page, "limit": 100, "pages": pages, "total": len(items)}}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # the default (5) resets concurrent connections on macOS


class FakeCatalog:
    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.responder: Responder = lambda _request: Reply(404, {"title": "no responder configured"})
        self._lock = threading.Lock()
        self._server = _Server(("127.0.0.1", 0), _handler_for(self))
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}/api/v1"

    def reply_in_order(self, *replies: Reply) -> None:
        """Answer successive requests with ``replies``; the last one repeats."""
        queue = list(replies)

        def responder(_request: Recorded) -> Reply:
            with self._lock:
                return queue.pop(0) if len(queue) > 1 else queue[0]

        self.responder = responder

    def handle(self, request: Recorded) -> Reply:
        with self._lock:
            self.requests.append(request)
        return self.responder(request)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _handler_for(fake: FakeCatalog) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            url = urlsplit(self.path)
            reply = fake.handle(Recorded(self.command, url.path, parse_qs(url.query), self.headers, body))
            if isinstance(reply.body, bytes):
                payload = reply.body
            elif reply.body is None:
                payload = b""
            else:
                payload = json.dumps(reply.body).encode()
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.headers.pop("Content-Type", "application/json"))
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _serve
        do_POST = _serve
        do_PUT = _serve

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler
