"""과제 04 견적 API (common.jsonapi).

  POST /api/revisions                    BOM 개정 등록 (과제 03 과 같은 형식)
  POST /api/price-versions               단가표 버전 등록 {table, label, effective_from, source, items[]}
  GET  /api/price-versions               단가표 버전 목록 (출처 포함)
  POST /api/quotes                       견적 생성 {quote_no, product_no, revision, qty, quote_date, customer}
  GET  /api/quotes                       견적 목록
  GET  /api/quotes/{no}?category=MATERIAL|PROCESS|OUTSOURCE   견적 + 비용별 근거
  GET  /api/quotes/{no}/excel            견적 Excel 다운로드
"""
from __future__ import annotations

from contextlib import contextmanager
from urllib.parse import quote as urlquote

from common import jsonapi
from common.jsonapi import route
from task03_bom_revision import bom as bomlib
from . import excel, quote as Q


def _excel(conn, req):
    q = Q.get_quote(conn, req.params["no"])
    name = f"견적서_{q['quote_no']}_{q['product_no']}_{q['revision']}.xlsx"
    return 200, excel.build(q), {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": f"attachment; filename*=UTF-8''{urlquote(name)}"}


def _post_quote(conn, req):
    res = Q.create_quote(conn, req.body or {})
    return (201 if res["created"] else 200), res


def _post_prices(conn, req):
    res = Q.register_price_version(conn, req.body or {})
    return (201 if res["created"] else 200), res


def _post_rev(conn, req):
    res = bomlib.register(conn, req.body)
    return (201 if res["created"] else 200), res


ROUTES = [
    route("POST", r"/api/revisions", _post_rev),
    route("POST", r"/api/price-versions", _post_prices),
    route("GET", r"/api/price-versions", lambda c, r: (200, Q.list_price_versions(c))),
    route("POST", r"/api/quotes", _post_quote),
    route("GET", r"/api/quotes", lambda c, r: (200, Q.list_quotes(c))),
    route("GET", r"/api/quotes/(?P<no>[^/]+)", lambda c, r: (200, Q.get_quote(c, r.params["no"], r.query.get("category")))),
    route("GET", r"/api/quotes/(?P<no>[^/]+)/excel", _excel),
]


def opener(db_path):
    @contextmanager
    def open_ctx():
        conn = Q.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()
    return open_ctx


def _init(db_path):
    c = Q.connect(db_path)
    Q.init_db(c)
    c.close()


def make_server(db_path, host="127.0.0.1", port=8004):
    _init(db_path)
    return jsonapi.make_server(ROUTES, opener(db_path), None, host, port)


def start_in_background(db_path):
    _init(db_path)
    return jsonapi.start_in_background(ROUTES, opener(db_path))
