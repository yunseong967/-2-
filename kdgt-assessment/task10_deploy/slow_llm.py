"""LLM 시간초과 시나리오용 가짜 API 서버: 요청을 받고 delay 초 동안 응답하지 않는다.
실제 anthropic SDK 가 이 주소(KDGT_ANTHROPIC_BASE_URL)로 요청하면 SDK 의 timeout 에 걸려 APITimeoutError 가 난다."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make(delay: float, port: int = 0):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            time.sleep(delay)
            body = json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "slow stub"}}).encode()
            try:
                self.send_response(529)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass
    s = ThreadingHTTPServer(("0.0.0.0", port), H)
    s.daemon_threads = True
    return s
