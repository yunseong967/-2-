"""과제 09 API + 일정 비교 화면 (common.jsonapi).

  GET /                                  일정 비교 화면
  GET /api/compare?as_of=2026-11-03T00:00   계획(지연 전) vs 실제(지연 후) 일정, KPI, 경고
  GET /api/projects                      입력 프로젝트 20건
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from common import jsonapi
from common.jsonapi import route
from task03_bom_revision.bom import BomError
from . import schedule as S

HERE = Path(__file__).parent
STATIC = {"/": HERE / "static" / "compare.html"}


class BadRequest(BomError):
    http_status = 400


def _ser(o):
    if isinstance(o, datetime):
        return o.isoformat(timespec="minutes")
    if isinstance(o, set):
        return sorted(o)
    if isinstance(o, dict):
        return {k: _ser(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_ser(v) for v in o]
    return o


def compare_payload(projects, as_of: datetime) -> dict:
    c = S.compare(projects, as_of)
    forecast = S.kpi(c["actual"], datetime(9999, 1, 1), len(projects))
    return _ser({"as_of": as_of, "rows": c["rows"], "kpi_actual": c["kpi_actual"], "kpi_plan": c["kpi_plan"],
                 "kpi_forecast": forecast, "plan_ops": c["plan"].ops, "actual_ops": c["actual"].ops,
                 "warnings": [w.__dict__ for w in c["actual"].warnings],
                 "unschedulable": c["actual"].unschedulable})


def _compare(projects):
    def handler(ctx, req):
        raw = req.query.get("as_of", "2026-11-03T00:00")
        try:
            as_of = datetime.fromisoformat(raw)
        except ValueError:
            raise BadRequest("DATE_INVALID", f"as_of 형식 오류: {raw!r}") from None
        return 200, compare_payload(projects, as_of)
    return handler


def make_routes(projects):
    return [route("GET", r"/api/compare", _compare(projects)),
            route("GET", r"/api/projects", lambda c, r: (200, projects))]


@contextmanager
def _noctx():
    yield None


def make_server(projects=None, host="127.0.0.1", port=8009):
    return jsonapi.make_server(make_routes(projects or S.load_projects()), _noctx, STATIC, host, port)


def start_in_background(projects=None):
    return jsonapi.start_in_background(make_routes(projects or S.load_projects()), _noctx, STATIC)
