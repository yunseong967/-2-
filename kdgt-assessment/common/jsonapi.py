"""작은 JSON HTTP 서버 (표준 라이브러리). 과제 03 부터 공통으로 쓴다.

- 라우트: (메서드, 정규식, 함수). 함수는 (ctx, Request) → (상태코드, 본문)
- 예외에 http_status 와 to_dict() 가 있으면 그대로 응답 (업무 오류)
- 그 밖의 예외는 내부 정보 없이 500
- 과제 06 에서 겪은 문제 반영: 접속 대기열(listen backlog) 기본값 5 → 128
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass
class Request:
    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)   # 경로 변수
    query: dict[str, str] = field(default_factory=dict)    # ?a=1 (마지막 값)
    body: Any = None


Route = tuple[str, "re.Pattern[str]", Callable[[Any, Request], tuple[int, Any]]]


def route(method: str, pattern: str, fn) -> Route:
    return (method, re.compile(f"^{pattern}$"), fn)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


def make_handler(routes: list[Route], open_ctx: Callable[[], AbstractContextManager],
                 static: dict[str, Path] | None = None):
    static = static or {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "KDGT/1.0"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status: int, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str):
            parts = urlsplit(self.path)
            path = unquote(parts.path)
            if method == "GET" and path in static:
                return self._send(200, static[path].read_bytes(), "text/html; charset=utf-8")
            for m, pattern, fn in routes:
                match = pattern.match(path)
                if m == method and match:
                    break
            else:
                return self._send(404, {"error": "NO_ROUTE", "message": f"{method} {path}"})

            body = None
            if method in ("POST", "PUT"):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"null")
                except ValueError:
                    return self._send(400, {"error": "INVALID_JSON", "message": "JSON 형식 오류"})
            req = Request(method, path, match.groupdict(),
                          {k: v[-1] for k, v in parse_qs(parts.query).items()}, body)
            try:
                with open_ctx() as ctx:
                    status, out = fn(ctx, req)
                self._send(status, out)
            except Exception as e:
                if hasattr(e, "http_status") and hasattr(e, "to_dict"):
                    self._send(e.http_status, e.to_dict())
                else:
                    self._send(500, {"error": "INTERNAL", "message": type(e).__name__})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def make_server(routes, open_ctx, static=None, host="127.0.0.1", port=0) -> ThreadingHTTPServer:
    return _Server((host, port), make_handler(routes, open_ctx, static))


def start_in_background(routes, open_ctx, static=None) -> tuple[ThreadingHTTPServer, str]:
    server = make_server(routes, open_ctx, static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def http_client(base_url: str):
    """테스트·검증용 클라이언트: call(method, path, body) → (status, json)"""
    import urllib.error
    import urllib.request

    def call(method: str, path: str, body=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    return call
