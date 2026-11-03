"""과제 07 채팅 화면 + API.

  GET  /                 채팅 화면
  GET  /api/info         백엔드·모델 정보, 예시 질문
  POST /api/ask          {question} → 답변·상태·인용·도구 로그·응답시간·비용
  GET  /api/ref?ref=...  인용 원문 (DB 행 또는 문서 절)
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from common import jsonapi
from common.jsonapi import route
from task03_bom_revision.bom import BomError
from . import agent as A, evaluate as E
from .tools import build_db

HERE = Path(__file__).parent
STATIC = {"/": HERE / "static" / "chat.html"}


class BadRequest(BomError):
    http_status = 400


def make_routes(ag: A.Agent):
    questions = E.load_questions()
    by_text = {q["question"]: q["id"] for q in questions}
    lock = threading.Lock()

    def ask(_, req):
        qtext = str((req.body or {}).get("question", "")).strip()
        if not qtext or len(qtext) > 500:
            raise BadRequest("QUESTION_INVALID", "질문은 1~500자")
        with lock:          # 모의 백엔드 상태 보호 + API 비용 폭주 방지(동시 1건)
            r = ag.ask(qtext, by_text.get(qtext))
        return 200, r.to_dict()

    def ref(_, req):
        k = req.query.get("ref", "")
        row = ag.tools.db.get_ref(k) or ag.tools.docs.get_ref(k)
        if row is None:
            raise BadRequest("REF_NOT_FOUND", f"인용 {k!r} 없음")
        return 200, row

    def info(_, req):
        return 200, {"backend": ag.backend.name, "model": getattr(ag.backend, "model", "scripted"),
                     "examples": [{"id": q["id"], "category": q["category"], "question": q["question"]} for q in questions]}

    return [route("POST", r"/api/ask", ask), route("GET", r"/api/ref", ref), route("GET", r"/api/info", info)]


@contextmanager
def _noctx():
    yield None


def build_agent(backend_name: str, db_path: Path, effort: str = "medium") -> A.Agent:
    build_db(db_path)
    backend = A.ClaudeBackend(effort=effort) if backend_name == "claude" else A.ScriptedBackend(backend_name)
    return A.Agent(db_path, backend)


def make_server(ag: A.Agent, host="127.0.0.1", port=8007):
    return jsonapi.make_server(make_routes(ag), _noctx, STATIC, host, port)


def start_in_background(ag: A.Agent):
    return jsonapi.start_in_background(make_routes(ag), _noctx, STATIC)
