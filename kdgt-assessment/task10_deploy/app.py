"""과제 10 배포용 서비스: 과제 06 LOT 추적 API + 과제 07 질의응답, 권한·감사·장애 대응을 덧씌움.

권한
  GET  /api/...                 viewer, admin   (조회)
  POST /api/receipts 등 쓰기     admin           (조회자 쓰기 → 403)
  POST /api/assist               viewer, admin   (질의응답은 읽기 전용)
  /admin/backup, /admin/audit    admin
  /healthz, /readyz              인증 없음 (데이터 노출 없음)

장애 대응
  DB 잠김·열기 실패·손상 → 500 이 아니라 503 + Retry-After, 서버는 계속 살아 있고 DB 가 돌아오면 재시작 없이 회복
  유지보수(복원) 중      → 모든 /api 503 MAINTENANCE
  LLM 시간초과          → 504 LLM_TIMEOUT (다른 API 는 영향 없음), 연속 3회 실패 시 60초간 즉시 503 (차단기)
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from common import jsonapi
from common.jsonapi import route
from task03_bom_revision.bom import BomError
from task06_lot_traceability import api as t6api, service as svc
from .security import AuditLog, KeyStore, presented_key


class HttpError(BomError):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None, **details):
        super().__init__(code, message, **details)
        self.http_status, self.headers = status, headers or {}


@dataclass
class Config:
    data_dir: Path
    llm_timeout: float = 10.0
    llm_backend: str = "claude"            # claude | mock (키 없는 시연)
    db_busy_ms: int = 2000                 # DB 잠김 시 최대 대기 → 그 뒤 503
    breaker_threshold: int = 3
    breaker_cooldown: float = 60.0

    @property
    def db(self):
        return self.data_dir / "lot.db"

    @property
    def qa_db(self):
        return self.data_dir / "qa.db"

    @property
    def keys(self):
        return self.data_dir / "keys.json"

    @property
    def audit(self):
        return self.data_dir / "audit.log"

    @property
    def backups(self):
        return self.data_dir / "backups"

    @property
    def maintenance(self):
        return self.data_dir / "MAINTENANCE"

    @classmethod
    def from_env(cls):
        return cls(data_dir=Path(os.environ.get("KDGT_DATA_DIR", "/data")),
                   llm_timeout=float(os.environ.get("KDGT_LLM_TIMEOUT", "10")),
                   llm_backend=os.environ.get("KDGT_LLM_BACKEND", "claude"),
                   db_busy_ms=int(os.environ.get("KDGT_DB_BUSY_MS", "2000")))


class Breaker:
    """연속 실패 n 회 → cooldown 초 동안 열림(호출 생략)."""

    def __init__(self, threshold: int, cooldown: float):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures, self.open_until, self.lock = 0, 0.0, threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            return time.monotonic() >= self.open_until

    def record(self, ok: bool):
        with self.lock:
            if ok:
                self.failures = 0
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.open_until = time.monotonic() + self.cooldown
                    self.failures = 0

    def state(self) -> dict:
        with self.lock:
            left = max(0.0, self.open_until - time.monotonic())
            return {"open": left > 0, "retry_after_s": round(left, 1), "consecutive_failures": self.failures}


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.keys = KeyStore(cfg.keys)
        self.audit = AuditLog(cfg.audit)
        self.breaker = Breaker(cfg.breaker_threshold, cfg.breaker_cooldown)
        self._agent = None
        self._agent_lock = threading.Lock()
        # LLM Agent 를 기동 시 미리 준비 — 첫 요청이 SDK 로딩·DB 생성 시간만큼 '제한 시간'을 넘기지 않게 (AI_LOG 오류 #2)
        threading.Thread(target=self._warm_agent, daemon=True).start()
        if not cfg.maintenance.exists() and not cfg.db.exists():   # 최초 기동: DB 생성 (WAL)
            c = svc.connect(cfg.db)
            svc.init_db(c)
            c.close()

    # ---------------------------------------------------------- DB
    def connect(self):
        if self.cfg.maintenance.exists():
            raise HttpError(503, "MAINTENANCE", "유지보수(복원) 중입니다. 잠시 후 다시 시도하세요.", {"Retry-After": "30"})
        # mode=rw: 파일이 없으면 새로 만들지 않고 오류 → 503. (빈 DB 를 조용히 만들면 장애가 '데이터 0건'으로 숨겨진다)
        conn = sqlite3.connect(f"file:{self.cfg.db}?mode=rw", uri=True, isolation_level=None,
                               timeout=self.cfg.db_busy_ms / 1000, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.cfg.db_busy_ms}")
        return conn

    @contextmanager
    def ctx(self):
        yield self

    # ---------------------------------------------------------- 요청 감싸기 (인증·권한·감사·DB 오류 변환)
    def guarded(self, roles: tuple[str, ...], handler, action: str):
        def fn(app, req):
            rid = uuid.uuid4().hex[:12]
            t0 = time.perf_counter()
            who = self.keys.verify(presented_key(req.headers))
            entry = {"request_id": rid, "client": req.client, "method": req.method, "path": req.path,
                     "action": action, "key_id": who[0] if who else None, "role": who[1] if who else None}
            status, detail = 500, None
            try:
                if who is None:
                    raise HttpError(401, "UNAUTHENTICATED", "유효한 API 키가 필요합니다 (Authorization: Bearer <key>)")
                if who[1] not in roles:
                    raise HttpError(403, "FORBIDDEN", f"역할 {who[1]} 은(는) 이 작업({action})을 할 수 없습니다")
                try:
                    result = handler(req)
                except sqlite3.OperationalError as e:
                    msg = str(e)
                    code = "DB_LOCKED" if "locked" in msg or "busy" in msg else "DB_UNAVAILABLE"
                    raise HttpError(503, code, "데이터베이스를 일시적으로 사용할 수 없습니다", {"Retry-After": "5"}) from e
                except sqlite3.DatabaseError as e:
                    raise HttpError(503, "DB_UNAVAILABLE", "데이터베이스 파일 오류 — 운영자에게 알리세요(복원 필요)",
                                    {"Retry-After": "60"}) from e
                status = result[0]
                if isinstance(result[1], dict):
                    detail = {k: result[1][k] for k in ("lot_id", "shipment_id", "request_id", "status", "error") if k in result[1]}
                return result
            except Exception as e:
                # 업무 오류는 클래스가 과제마다 달라도 http_status 를 가진다 (과제 06 DomainError 등).
                # 처음엔 BomError 만 잡아서, 실제 응답은 409 인데 감사 로그엔 500 으로 남았다 — AI_LOG 오류 #1
                status = getattr(e, "http_status", 500)
                detail = {"error": getattr(e, "code", type(e).__name__)}
                raise
            finally:
                self.audit.write(**entry, status=status, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                                 detail=detail)
        return fn

    def with_db(self, f):
        def h(req):
            conn = self.connect()
            try:
                return f(conn, req)
            finally:
                conn.close()
        return h

    # ---------------------------------------------------------- LLM 질의응답
    def agent(self):
        from task07_qa_agent import agent as A, tools as T
        with self._agent_lock:
            if self._agent is None:
                if not self.cfg.qa_db.exists():
                    T.build_db(self.cfg.qa_db)
                backend = (A.ClaudeBackend(timeout=self.cfg.llm_timeout, max_retries=0)
                           if self.cfg.llm_backend == "claude" else A.ScriptedBackend(self.cfg.llm_backend))
                self._agent = A.Agent(self.cfg.qa_db, backend)
            return self._agent

    def _warm_agent(self):
        try:
            self.agent()
        except Exception:
            pass                      # 자격 증명 없음 등 → 첫 요청 때 503 으로 안내

    def assist(self, req):
        q = str((req.body or {}).get("question", "")).strip()
        if not q or len(q) > 500:
            raise HttpError(400, "QUESTION_INVALID", "질문은 1~500자")
        if not self.breaker.allow():
            st = self.breaker.state()
            raise HttpError(503, "LLM_UNAVAILABLE", "AI 응답이 일시 중단되었습니다. LOT 조회 API 를 직접 사용하세요.",
                            {"Retry-After": str(int(st["retry_after_s"]) + 1)}, breaker=st)
        try:
            ag = self.agent()
        except RuntimeError as e:                         # 자격 증명 없음 등
            raise HttpError(503, "LLM_NOT_CONFIGURED", str(e)) from e
        t0 = time.perf_counter()
        r = ag.ask(q)
        if r.error in ("APITimeoutError", "APIConnectionError", "InternalServerError", "RateLimitError", "OverloadedError"):
            self.breaker.record(False)
            code, status = ("LLM_TIMEOUT", 504) if r.error == "APITimeoutError" else ("LLM_UPSTREAM_ERROR", 502)
            raise HttpError(status, code, f"AI 응답 실패({r.error}, {round(time.perf_counter() - t0, 1)}s). "
                                          "LOT 조회 API 를 직접 사용하세요.", breaker=self.breaker.state())
        self.breaker.record(True)
        return 200, {k: v for k, v in r.to_dict().items() if k in ("status", "answer", "citations", "latency_s", "cost_usd", "guards")}

    # ---------------------------------------------------------- 운영
    def healthz(self, _, req):
        return 200, {"ok": True}

    def readyz(self, _, req):
        checks = {"maintenance": self.cfg.maintenance.exists()}
        try:
            conn = self.connect()
            try:
                conn.execute("SELECT COUNT(*) FROM lots").fetchone()
                checks["db"] = "ok"
            finally:
                conn.close()
        except Exception as e:
            checks["db"] = f"error: {type(e).__name__}"
        checks["llm_breaker"] = self.breaker.state()
        ok = checks["db"] == "ok" and not checks["maintenance"]
        if not ok:
            raise HttpError(503, "NOT_READY", "준비 안 됨", {"Retry-After": "5"}, checks=checks)
        return 200, {"ready": True, "checks": checks}

    def routes(self):
        R = []
        # 과제 06 GET (조회) — viewer·admin
        for pattern, fn in t6api.GET_ROUTES:
            pat = pattern.pattern.strip("^$")
            R.append(route("GET", pat, self.guarded(("viewer", "admin"),
                                                    self.with_db(lambda c, r, fn=fn: fn(c, *r.params.values())), "read")))
        # 과제 06 POST (쓰기) — admin 만
        for path, fn in t6api.POST_ROUTES.items():
            R.append(route("POST", path, self.guarded(("admin",), self.with_db(lambda c, r, fn=fn: fn(c, r.body or {})),
                                                      "write:" + path.rsplit("/", 1)[-1])))
        R.append(route("POST", r"/api/assist", self.guarded(("viewer", "admin"), self.assist, "assist")))
        R.append(route("POST", r"/admin/backup", self.guarded(("admin",), self.admin_backup, "admin:backup")))
        R.append(route("GET", r"/admin/audit", self.guarded(("admin",), lambda r: (200, self.audit.tail(int(r.query.get("n", 50)))),
                                                           "admin:audit")))
        R.append(route("GET", r"/healthz", self.healthz))
        R.append(route("GET", r"/readyz", self.readyz))
        return R

    def admin_backup(self, req):
        from . import backup as B
        try:
            return 201, B.backup(self.cfg.db, self.cfg.backups)
        except sqlite3.DatabaseError as e:
            raise HttpError(503, "BACKUP_FAILED", f"백업 실패: {type(e).__name__}") from e


def make_server(cfg: Config, host="0.0.0.0", port=8010):
    app = App(cfg)
    return jsonapi.make_server(app.routes(), app.ctx, None, host, port), app


def start_in_background(cfg: Config):
    app = App(cfg)
    server, base = jsonapi.start_in_background(app.routes(), app.ctx, None)
    return server, base, app
