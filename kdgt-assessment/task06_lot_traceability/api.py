"""과제 06 HTTP API (표준 라이브러리 ThreadingHTTPServer).

요청마다 스레드 1개 + DB 연결 1개. 동시 요청의 정합성은 service.tx() 가 보장한다.

  GET  /                                추적 화면
  GET  /api/lots                        LOT 목록
  GET  /api/lots/{lot}                  LOT 1건
  GET  /api/trace/forward/{lot}         정방향 추적 (+ 영향 출하)
  GET  /api/trace/backward/{lot}        역방향 추적 (+ 검사 근거)
  GET  /api/shipments                   출하 목록
  GET  /api/shipments/{id}/trace        출하 → 원자재 역추적
  GET  /api/reconcile                   재고 수불 대조
  POST /api/receipts      {lot_id, item, qty, unit, project_id?}
  POST /api/inspections   {lot_id, result, inspector, note?}
  POST /api/splits        {lot_id, quantities[], new_lot_ids[]?}
  POST /api/merges        {lot_ids[], new_lot_id}
  POST /api/productions   {lot_id, item, qty, unit, consumes:[{lot_id, qty}], project_id?}
  POST /api/shipments     {request_id, lot_id, qty, customer}

오류 응답: {"error": 코드, "message": 설명, ...}  400 입력오류 / 404 없음 / 409 업무규칙 위반
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from . import service as svc

STATIC = Path(__file__).with_name("static")


def _need(body: dict, *keys):
    missing = [k for k in keys if body.get(k) in (None, "", [])]
    if missing:
        raise svc.BadRequest("MISSING_FIELD", f"필수 필드 누락: {', '.join(missing)}")


def _post_receipts(c, b):
    _need(b, "lot_id", "item", "qty", "unit")
    return 201, svc.receive(c, b["lot_id"], b["item"], b["qty"], b["unit"], b.get("project_id"), b.get("actor"))


def _post_inspections(c, b):
    _need(b, "lot_id", "result", "inspector")
    return 201, svc.inspect(c, b["lot_id"], b["result"], b["inspector"], b.get("note"))


def _post_splits(c, b):
    _need(b, "lot_id", "quantities")
    return 201, svc.split(c, b["lot_id"], b["quantities"], b.get("new_lot_ids"), b.get("actor"))


def _post_merges(c, b):
    _need(b, "lot_ids", "new_lot_id")
    return 201, svc.merge(c, b["lot_ids"], b["new_lot_id"], b.get("actor"))


def _post_productions(c, b):
    _need(b, "lot_id", "item", "qty", "unit", "consumes")
    return 201, svc.produce(c, b["lot_id"], b["item"], b["qty"], b["unit"], b["consumes"],
                            b.get("project_id"), b.get("actor"))


def _post_shipments(c, b):
    _need(b, "request_id", "lot_id", "qty", "customer")
    res = svc.ship(c, b["request_id"], b["lot_id"], b["qty"], b["customer"], b.get("actor"))
    return (200 if res["replayed"] else 201), res


def _list_shipments(c):
    return 200, [svc.shipment_view(c, r[0]) for r in c.execute("SELECT shipment_id FROM shipments ORDER BY 1")]


POST_ROUTES = {
    "/api/receipts": _post_receipts, "/api/inspections": _post_inspections,
    "/api/splits": _post_splits, "/api/merges": _post_merges,
    "/api/productions": _post_productions, "/api/shipments": _post_shipments,
}
GET_ROUTES = [
    (re.compile(r"^/api/lots$"), lambda c: (200, svc.list_lots(c))),
    (re.compile(r"^/api/lots/(?P<lot>[^/]+)$"), lambda c, lot: (200, svc.lot_view(c, lot))),
    (re.compile(r"^/api/trace/forward/(?P<lot>[^/]+)$"), lambda c, lot: (200, svc.trace_forward(c, lot))),
    (re.compile(r"^/api/trace/backward/(?P<lot>[^/]+)$"), lambda c, lot: (200, svc.trace_backward(c, lot))),
    (re.compile(r"^/api/shipments$"), _list_shipments),
    (re.compile(r"^/api/shipments/(?P<sid>\d+)/trace$"), lambda c, sid: (200, svc.trace_shipment(c, int(sid)))),
    (re.compile(r"^/api/reconcile$"), lambda c: (200, svc.reconcile(c))),
]


def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KDGT-Task06/1.0"

        def log_message(self, fmt, *args):  # 기본 stderr 로그 끔 (시연 화면 깔끔하게)
            pass

        def _send(self, status: int, body, content_type="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _run(self, fn, *args):
            conn = svc.connect(db_path)
            try:
                status, body = fn(conn, *args)
                self._send(status, body)
            except svc.DomainError as e:
                self._send(e.http_status, e.to_dict())
            except Exception as e:  # 예상 못 한 오류: 내부 정보 노출 없이 500
                self._send(500, {"error": "INTERNAL", "message": type(e).__name__})
            finally:
                conn.close()

        def do_GET(self):
            path = unquote(self.path.split("?", 1)[0])
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "trace.html").read_bytes(), "text/html; charset=utf-8")
            for pattern, fn in GET_ROUTES:
                m = pattern.match(path)
                if m:
                    return self._run(fn, *m.groupdict().values())
            self._send(404, {"error": "NO_ROUTE", "message": path})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            fn = POST_ROUTES.get(path)
            if fn is None:
                return self._send(404, {"error": "NO_ROUTE", "message": path})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "INVALID_JSON", "message": "JSON 객체가 아님"})
            self._run(fn, body)

    return Handler


class _Server(ThreadingHTTPServer):
    # 기본 listen 대기열(5)로는 동시 접속 20건 중 일부가 'Connection reset' 으로 끊긴다.
    # (검증 중 실제로 발견 → AI_LOG 오류 기록)
    request_queue_size = 128


def make_server(db_path: str, host="127.0.0.1", port=8006) -> ThreadingHTTPServer:
    conn = svc.connect(db_path)
    svc.init_db(conn)
    conn.close()
    server = _Server((host, port), make_handler(db_path))
    server.daemon_threads = True
    return server


def start_in_background(db_path: str, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """테스트/검증용: 빈 포트에 서버를 띄우고 (server, base_url) 반환."""
    server = make_server(db_path, port=port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, p = server.server_address[:2]
    return server, f"http://{host}:{p}"
