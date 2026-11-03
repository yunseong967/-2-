"""과제 08 수집 API + 대시보드.

  GET  /                     대시보드
  POST /api/events           {"events": [...], "sim_now": "..."}  (sim_now 는 시뮬레이션 모드에서만 사용)
  POST /api/plan             [{wo, process, device_id, planned_start, planned_end}, ...]
  GET  /api/state?now=...    now 시점까지 수신한 이벤트로 계산한 상태 (기본: 현재 시각 / 시뮬레이션 마지막 시각)
  GET  /api/ingest-log?result=DUPLICATE|REJECTED|CONFLICT
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from common import jsonapi
from common.jsonapi import route
from task03_bom_revision.bom import BomError
from . import events as E

HERE = Path(__file__).parent
STATIC = {"/": HERE / "static" / "dashboard.html"}


class BadRequest(BomError):
    http_status = 400


class Clock:
    def __init__(self, sim: bool):
        self.sim, self.sim_now, self.lock = sim, None, threading.Lock()

    def received_at(self, body_now):
        if self.sim:
            if not body_now:
                raise BadRequest("SIM_TIME_REQUIRED", "시뮬레이션 모드에서는 sim_now 필요")
            with self.lock:
                self.sim_now = max(self.sim_now or body_now, body_now)
            return body_now
        return datetime.now().isoformat(timespec="seconds")

    def now(self):
        return (self.sim_now if self.sim else None) or datetime.now().isoformat(timespec="seconds")


def make_routes(clock: Clock):
    def post_events(conn, req):
        b = req.body or {}
        if not isinstance(b, dict) or not isinstance(b.get("events"), list):
            raise BadRequest("BODY_INVALID", '{"events": [...]} 형식이어야 함')
        if len(b["events"]) > 5000:
            raise BadRequest("BATCH_TOO_LARGE", "한 번에 5,000건까지")
        return 200, E.ingest(conn, b["events"], clock.received_at(b.get("sim_now")))

    def post_plan(conn, req):
        if not isinstance(req.body, list):
            raise BadRequest("BODY_INVALID", "계획 목록이어야 함")
        E.set_plan(conn, req.body)
        return 200, {"rows": len(req.body)}

    def state(conn, req):
        now = req.query.get("now") or clock.now()
        try:
            datetime.fromisoformat(now)
        except ValueError:
            raise BadRequest("DATE_INVALID", f"now 형식 오류: {now!r}") from None
        return 200, E.build_state(conn, now) | {"sim": clock.sim}

    def ingest_log(conn, req):
        res = req.query.get("result")
        sql = "SELECT * FROM ingest_log" + (" WHERE result=?" if res else "") + " ORDER BY id DESC LIMIT 200"
        return 200, [dict(r) for r in conn.execute(sql, (res,) if res else ())]

    return [route("POST", r"/api/events", post_events), route("POST", r"/api/plan", post_plan),
            route("GET", r"/api/state", state), route("GET", r"/api/ingest-log", ingest_log)]


def opener(db_path):
    @contextmanager
    def ctx():
        conn = E.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()
    return ctx


def make_server(db_path, sim=False, host="127.0.0.1", port=8008):
    E.connect(db_path).close()
    clock = Clock(sim)
    return jsonapi.make_server(make_routes(clock), opener(db_path), STATIC, host, port), clock


def start_in_background(db_path, sim=True):
    E.connect(db_path).close()
    clock = Clock(sim)
    server, base = jsonapi.start_in_background(make_routes(clock), opener(db_path), STATIC)
    return server, base, clock
