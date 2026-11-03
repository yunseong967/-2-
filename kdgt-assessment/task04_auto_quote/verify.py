"""과제 04 자동 검증 (실제 HTTP 서버, 새 DB).

완료 기준 (슬라이드)
  1. 정답 10건과 계산 일치     — 독립 계산기(reference_calc.py) 정답과 합계·비용별·줄별 금액·상태 비교
  2. 단가 변경 후 기존 견적 유지 — 3월 단가로 발행 → 6월 단가 등록 → 기존 견적 재조회 결과가 완전히 동일
  3. 비용별 근거 조회           — 모든 확정 줄에 계산식·단가·단가표 버전·출처, 줄 합계 = 비용 합계
추가: 미등록 단가 → 검토 필요(발행 불가), Excel 견적 파일 생성·내용 확인
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from common.jsonapi import http_client
from .api import start_in_background

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
BOM_FIX = HERE.parent / "task03_bom_revision" / "fixtures"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _j(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _download(base, path):
    import urllib.request
    with urllib.request.urlopen(base + path) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def _line_key(l):
    return f"{l['category']}:{l['item']}" + (f":{l['cost_key']}" if l["category"] != "MATERIAL" else "")


def compare(q, e) -> list[str]:
    bad = []
    for k in ("status", "material_total", "process_total", "outsource_total", "subtotal", "supply", "vat", "total"):
        if q[k] != e[k]:
            bad.append(f"{k} {q[k]}≠{e[k]}")
    if q["price_versions"] != e["versions"]:
        bad.append(f"단가버전 {q['price_versions']}≠{e['versions']}")
    got_lines = {_line_key(l): l["amount"] for l in q["lines"] if l["status"] == "OK"}
    if got_lines != e["line_amounts"]:
        diff = {k: (got_lines.get(k), e["line_amounts"].get(k)) for k in set(got_lines) | set(e["line_amounts"])
                if got_lines.get(k) != e["line_amounts"].get(k)}
        bad.append(f"줄 금액 {diff}")
    review = sorted(f"{l['category']}:{l['item']}" for l in q["lines"] if l["status"] == "REVIEW")
    if review != e["review_items"]:
        bad.append(f"검토항목 {review}≠{e['review_items']}")
    return bad


def run(report_dir=None):
    exp = {q["quote_no"]: q for q in _j(FIX / "expected.json")["quotes"]}
    reqs = {r["quote_no"]: r for r in _j(FIX / "quote_requests.json")}
    prices = sorted((FIX / "prices").glob("*.json"))
    march = [p for p in prices if "2026-03" in p.name]
    june = [p for p in prices if "2026-06" in p.name]
    checks: list[Check] = []
    art = {"rows": [], "excel": []}
    with tempfile.TemporaryDirectory() as tmp:
        server, base = start_in_background(str(Path(tmp) / "t4.db"))
        call = http_client(base)
        try:
            for r in "ABCD":
                call("POST", "/api/revisions", _j(BOM_FIX / f"rev_{r}.json"))
            for p in march:
                call("POST", "/api/price-versions", _j(p))

            phase1 = [n for n, r in reqs.items() if r["quote_date"] < "2026-06-01"]
            phase2 = [n for n in reqs if n not in phase1]
            got = {}
            for n in phase1:
                got[n] = call("POST", "/api/quotes", reqs[n])[1]
            before = {n: call("GET", f"/api/quotes/{n}")[1] for n in phase1}
            before_xlsx = {n: load_workbook(BytesIO(_download(base, f"/api/quotes/{n}/excel")[2]))["견적서"]
                           for n in phase1}

            # --- 단가 변경 (6월 버전 등록) ---
            for p in june:
                call("POST", "/api/price-versions", _j(p))
            for n in phase2:
                got[n] = call("POST", "/api/quotes", reqs[n])[1]

            # 1) 정답 10건
            bad = {}
            for n in reqs:
                b = compare(call("GET", f"/api/quotes/{n}")[1], exp[n])
                art["rows"].append((n, reqs[n], got[n], exp[n], not b))
                if b:
                    bad[n] = b
            n_lines = sum(len(e["line_amounts"]) for e in exp.values())
            checks.append(Check(f"견적 10건 × (합계·비용별·줄 금액 {n_lines}개·상태) 정답 일치", not bad,
                                json.dumps(bad, ensure_ascii=False) if bad else "10건 전부 일치"))

            # 2) 단가 변경 후 기존 견적 유지
            after = {n: call("GET", f"/api/quotes/{n}")[1] for n in phase1}
            same = [n for n in phase1 if after[n] == before[n]]
            re_s, re_b = call("POST", "/api/quotes", reqs["Q01"])
            q10 = call("GET", "/api/quotes/Q10")[1]
            changed = q10["total"] != after["Q01"]["total"] and q10["price_versions"]["MATERIAL"] == "2026-06"
            ok = len(same) == len(phase1) and re_s == 200 and re_b["total"] == before["Q01"]["total"] and changed
            checks.append(Check("단가 변경(6월 버전 등록) 후 기존 견적 유지", ok,
                                f"기존 {len(phase1)}건 중 {len(same)}건 완전 동일, Q01 재요청 {re_s}(기존 반환), "
                                f"같은 BOM 신규 견적 Q10 은 새 단가 적용 {after['Q01']['total']:,}→{q10['total']:,}원"))

            # 3) 비용별 근거 조회
            bad = []
            for n in reqs:
                for cat in ("MATERIAL", "PROCESS", "OUTSOURCE"):
                    s, d = call("GET", f"/api/quotes/{n}?category={cat}")
                    ok_lines = [l for l in d["lines"] if l["status"] == "OK"]
                    if any(not (l["formula"] and l["unit_price"] and l["price_version"] and l["price_table"])
                           for l in ok_lines):
                        bad.append(f"{n}/{cat} 근거 누락")
                    if sum(l["amount"] for l in ok_lines) != d[f"{cat.lower()}_total"]:
                        bad.append(f"{n}/{cat} 줄 합계 불일치")
                    if {l["category"] for l in d["lines"]} - {cat}:
                        bad.append(f"{n}/{cat} 필터 오류")
                srcs = {(s["table_type"], s["label"]) for s in d["price_sources"]}
                if any((t, v) not in srcs for t, v in d["price_versions"].items() if v):
                    bad.append(f"{n} 출처 누락")
            checks.append(Check("비용별 근거 조회 (30개 조회: 계산식·단가·버전·출처, 줄 합계=비용 합계)", not bad,
                                "; ".join(bad) or "전부 확인"))

            # 4) 미등록 단가 → 검토 필요
            q4 = call("GET", "/api/quotes/Q04")[1]
            rv = [l for l in q4["lines"] if l["status"] == "REVIEW"]
            ok = q4["status"] == "NEEDS_REVIEW" and len(rv) == 1 and rv[0]["item"] == "SG-01" and rv[0]["amount"] is None
            checks.append(Check("미등록 단가(사이트글라스, 3월 단가표) → 금액 추측 없이 검토 필요", ok,
                                f"Q04 상태 {q4['status']}, 검토 줄: " + "; ".join(f"{l['item']} — {l['review_reason']}" for l in rv)))

            # 5) Excel
            out_dir = Path(report_dir) / "quotes" if report_dir else Path(tmp) / "x"
            out_dir.mkdir(parents=True, exist_ok=True)
            bad = []
            for n in reqs:
                s, ctype, data = _download(base, f"/api/quotes/{n}/excel")
                wb = load_workbook(BytesIO(data))
                sheet = wb["견적서"]
                vals = {sheet.cell(r, 1).value: sheet.cell(r, 2).value for r in range(1, sheet.max_row + 1)}
                q = call("GET", f"/api/quotes/{n}")[1]
                ok = (wb.sheetnames == ["견적서", "산출근거", "단가출처", "계산규칙"] and vals.get("합계") == q["total"]
                      and sum(1 for r in wb["산출근거"].iter_rows(min_row=2, max_col=1) if isinstance(r[0].value, int)) == len(q["lines"]) and ctype.startswith("application/vnd.openxml"))
                if not ok:
                    bad.append(n)
                (out_dir / f"견적서_{n}.xlsx").write_bytes(data)
                art["excel"].append((n, vals.get("합계"), vals.get("상태")))
            unchanged_xlsx = all(
                [c.value for row in before_xlsx[n].iter_rows() for c in row] ==
                [c.value for row in load_workbook(BytesIO(_download(base, f"/api/quotes/{n}/excel")[2]))["견적서"].iter_rows() for c in row]
                for n in phase1)
            checks.append(Check("Excel 견적 10개 (시트 4개, 합계 일치, 근거 줄 수 일치, 단가 변경 전후 동일)",
                                not bad and unchanged_xlsx, f"불일치 {bad}, 단가 변경 전후 Excel 동일={unchanged_xlsx}"))
        finally:
            server.shutdown()
            server.server_close()
    if report_dir:
        _report(Path(report_dir), checks, art)
    return checks, art


def _report(out: Path, checks, a):
    L = ["# 과제 04 자동 검증 결과", "",
         "- 방법: 빈 DB + 실제 HTTP 서버. 3월 단가로 견적 발행 → 6월 단가 등록 → 나머지 발행 → 전체 재조회",
         "- 정답: `fixtures/expected.json` (엔진과 독립 구현인 `reference_calc.py` 가 계산, 계산서 `answer_sheets.md`)",
         "- 재실행: `python -m task04_auto_quote.cli verify`", "",
         "## 판정", "", "| 결과 | 항목 | 근거 |", "|---|---|---|",
         *[f"| {'PASS' if c.ok else 'FAIL'} | {c.name} | {c.detail} |" for c in checks], ""]
    (out / "verification.md").write_text("\n".join(L), encoding="utf-8")
    T = ["# 과제 04 정답 대조표", "",
         "| 견적 | 개정 | 수량 | 견적일 | 단가 버전(재료/구매/공정) | 재료비 | 가공비 | 외주비 | 공급가액 | 부가세 | 합계 | 정답 합계 | 상태 | 판정 |",
         "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for n, r, q, e, ok in a["rows"]:
        v = q["price_versions"]
        T.append(f"| {n} | {r['revision']} | {r['qty']} | {r['quote_date']} | {v['MATERIAL']}/{v['PURCHASE']}/{v['PROCESS']} | "
                 f"{q['material_total']:,} | {q['process_total']:,} | {q['outsource_total']:,} | {q['supply']:,} | "
                 f"{q['vat']:,} | {q['total']:,} | {e['total']:,} | {'검토 필요' if q['status'] != 'ISSUED' else '발행'} | "
                 f"{'일치' if ok else '불일치'} |")
    T += ["", "줄 단위 금액(재료·가공·외주 각 줄)까지 정답과 비교했다. 견적 파일: `reports/quotes/견적서_Qxx.xlsx`", ""]
    (out / "answer_comparison.md").write_text("\n".join(T), encoding="utf-8")
