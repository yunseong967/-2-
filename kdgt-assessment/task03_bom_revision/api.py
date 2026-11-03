"""과제 03 BOM API (common.jsonapi 사용).

  GET  /                                        개정 비교 화면
  POST /api/revisions                           도면 추출 JSON 으로 개정 등록 (201 신규 / 200 동일 재등록)
  GET  /api/products                            제품 목록
  GET  /api/products/{p}/revisions              개정 목록
  GET  /api/products/{p}/bom?rev=B|as_of=날짜&unit=mm|cm|m   BOM 트리 + 전개 (기본: 최신)
  GET  /api/products/{p}/diff?from=A&to=B&unit=m           개정 비교

오류: 422 BOM_INVALID(errors[] 에 전체 목록) / 409 REVISION_EXISTS, REVISION_ORDER / 404 없음
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from common import jsonapi
from common.jsonapi import route
from . import bom

STATIC = {"/": Path(__file__).with_name("static") / "compare.html"}


def _post_revision(conn, req):
    res = bom.register(conn, req.body)
    return (201 if res["created"] else 200), res


ROUTES = [
    route("POST", r"/api/revisions", _post_revision),
    route("GET", r"/api/products", lambda c, r: (200, bom.list_products(c))),
    route("GET", r"/api/products/(?P<p>[^/]+)/revisions", lambda c, r: (200, bom.list_revisions(c, r.params["p"]))),
    route("GET", r"/api/products/(?P<p>[^/]+)/bom", lambda c, r: (200, bom.get_bom(
        c, r.params["p"], r.query.get("rev"), r.query.get("as_of"), r.query.get("unit", "mm")))),
    route("GET", r"/api/products/(?P<p>[^/]+)/diff", lambda c, r: (200, bom.diff(
        c, r.params["p"], r.query.get("from"), r.query.get("to"), r.query.get("unit", "mm")))),
]


def opener(db_path: str):
    @contextmanager
    def open_ctx():
        conn = bom.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()
    return open_ctx


def _init(db_path):
    c = bom.connect(db_path)
    bom.init_db(c)
    c.close()


def make_server(db_path: str, host="127.0.0.1", port=8003):
    _init(db_path)
    return jsonapi.make_server(ROUTES, opener(db_path), STATIC, host, port)


def start_in_background(db_path: str):
    _init(db_path)
    return jsonapi.start_in_background(ROUTES, opener(db_path), STATIC)
