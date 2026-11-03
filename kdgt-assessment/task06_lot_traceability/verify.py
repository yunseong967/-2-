"""과제 06 자동 검증. 새 DB + 실제 HTTP 서버로 실행한다.

1. 시나리오 (scenario.py) 전 단계가 정답(기대 상태/오류코드)과 일치
2. 동시성 A — 재고 30EA 제품에 20개 스레드가 동시에 3EA씩 출하 요청
     → 정확히 10건 성공, 10건 INSUFFICIENT_QTY, 재고 0, 음수 없음
3. 동시성 B — 같은 request_id 로 10개 스레드가 동시에 출하 요청
     → 출하 1건만 생성, 나머지 9건은 replayed, 재고 1만 차감
4. 동시성 C — 출하 요청과 '부적합 재검사'가 동시에 들어올 때
     → 부적합 처리 이후 커밋된 출하는 0건 (상태 확인과 차감이 원자적)
5. 재고 수불 대조 — LOT 별 수불부 합계 = 현재고, 품목별 보존식, 음수 LOT 0
"""
from __future__ import annotations

import json
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import service as svc
from .api import start_in_background
from .scenario import http_client, run as run_scenario


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _parallel(n, fn):
    """n개 스레드를 Barrier 로 동시에 출발시키고 결과 목록을 반환."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        barrier.wait()
        try:
            results[i] = fn(i)
        except Exception as e:  # 연결 끊김 등은 결과로 기록해 판정에 반영
            results[i] = (599, {"error": f"CLIENT_{type(e).__name__}"})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _prepare_product(call, mat, prod, qty):
    call("POST", "/api/receipts", {"lot_id": mat, "item": "SUS304 6t", "qty": 1000, "unit": "KG"})
    call("POST", "/api/inspections", {"lot_id": mat, "result": "PASS", "inspector": "QC"})
    call("POST", "/api/productions", {"lot_id": prod, "item": "동시성 시험품", "qty": qty, "unit": "EA",
                                      "consumes": [{"lot_id": mat, "qty": 100}]})
    call("POST", "/api/inspections", {"lot_id": prod, "result": "PASS", "inspector": "QC"})


def run(report_dir: str | Path | None = None) -> tuple[list[Check], dict]:
    checks: list[Check] = []
    artifacts: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "task06.db"
        server, base = start_in_background(str(db))
        call = http_client(base)
        try:
            # 1) 시나리오
            steps = run_scenario(call)
            bad = [s.title for s in steps if not s.result["ok"]]
            checks.append(Check(f"시나리오 {len(steps)}단계 정답 일치", not bad,
                                f"불일치 {len(bad)}건 {bad}" if bad else "전 단계 일치"))
            artifacts["steps"] = steps

            # 2) 동시 출하 - 재고 초과 방지
            _prepare_product(call, "M-CC1", "P-CC1", 30)
            res = _parallel(20, lambda i: call("POST", "/api/shipments", {
                "request_id": f"CC1-{i:02d}", "lot_id": "P-CC1", "qty": 3, "customer": "동시성"}))
            outcome = Counter(r[1].get("error", "SHIPPED") if r[0] >= 400 else "SHIPPED" for r in res)
            left = call("GET", "/api/lots/P-CC1")[1]["qty"]
            ok = outcome == Counter({"SHIPPED": 10, "INSUFFICIENT_QTY": 10}) and left == 0
            checks.append(Check("동시성 A: 20스레드×3EA, 재고 30EA → 10건만 성공", ok,
                                f"결과 {dict(outcome)}, 남은 재고 {left}"))
            artifacts["concurrency_a"] = {"outcome": dict(outcome), "left": left}

            # 3) 동시 중복 요청
            _prepare_product(call, "M-CC2", "P-CC2", 10)
            res = _parallel(10, lambda i: call("POST", "/api/shipments", {
                "request_id": "CC2-SAME", "lot_id": "P-CC2", "qty": 1, "customer": "동시성"}))
            created = sum(1 for st, b in res if st == 201)
            replayed = sum(1 for st, b in res if st == 200 and b.get("replayed"))
            ids = {b.get("shipment_id") for st, b in res}
            left = call("GET", "/api/lots/P-CC2")[1]["qty"]
            ok = created == 1 and replayed == 9 and len(ids) == 1 and left == 9
            checks.append(Check("동시성 B: 같은 request_id 10건 동시 → 출하 1건", ok,
                                f"신규 {created}, replayed {replayed}, 출하번호 {ids}, 남은 재고 {left}"))
            artifacts["concurrency_b"] = {"created": created, "replayed": replayed, "left": left}

            # 4) 출하 vs 부적합 재검사 경합
            _prepare_product(call, "M-CC3", "P-CC3", 50)

            def race(i):
                if i == 0:
                    return ("inspect",) + call("POST", "/api/inspections", {
                        "lot_id": "P-CC3", "result": "FAIL", "inspector": "QC", "note": "경합 시험"})
                return ("ship",) + call("POST", "/api/shipments", {
                    "request_id": f"CC3-{i:02d}", "lot_id": "P-CC3", "qty": 1, "customer": "동시성"})
            res = _parallel(11, race)
            conn = svc.connect(db)
            fail_event = conn.execute(
                "SELECT event_id FROM inspections WHERE lot_id='P-CC3' AND result='FAIL'").fetchone()[0]
            late = conn.execute(
                "SELECT COUNT(*) FROM shipments WHERE lot_id='P-CC3' AND event_id > ?", (fail_event,)).fetchone()[0]
            shipped = conn.execute("SELECT COUNT(*) FROM shipments WHERE lot_id='P-CC3'").fetchone()[0]
            blocked = sum(1 for r in res if r[0] == "ship" and r[2].get("error") == "STATUS_BLOCKED")
            ok = late == 0 and shipped + blocked == 10
            checks.append(Check("동시성 C: 출하 10건과 부적합 재검사 동시 → 부적합 이후 출하 0건", ok,
                                f"부적합 전 출하 {shipped}건, 차단 {blocked}건, 부적합 이후 출하 {late}건"))

            # 5) 수불 대조
            rec = svc.reconcile(conn)
            conn.close()
            bad_lots = [r["lot_id"] for r in rec["lots"] if not r["ok"]]
            bad_items = [i["item"] for i in rec["items"] if not i["ok"]]
            checks.append(Check("재고 수불 대조 (LOT별·품목별) 일치, 음수 재고 0", rec["ok"],
                                f"LOT {len(rec['lots'])}개 불일치 {bad_lots}, 품목 {len(rec['items'])}개 "
                                f"불일치 {bad_items}, 음수 LOT {rec['negative_lots']}"))
            artifacts["reconcile"] = rec
        finally:
            server.shutdown()
            server.server_close()

    if report_dir:
        _write_reports(Path(report_dir), checks, artifacts)
    return checks, artifacts


def _write_reports(out: Path, checks, a):
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 과제 06 자동 검증 결과", "",
        "- 방법: 빈 DB + 실제 HTTP 서버(ThreadingHTTPServer)에 요청",
        "- 재실행: `python -m task06_lot_traceability.cli verify`", "",
        "## 판정", "", "| 결과 | 항목 | 근거 |", "|---|---|---|",
        *[f"| {'PASS' if c.ok else 'FAIL'} | {c.name} | {c.detail} |" for c in checks], "",
        "## 시나리오 단계별 결과", "",
        "| # | 단계 | 기대 | 실제 | 판정 | 근거 |", "|---:|---|---|---|---|---|",
    ]
    for n, s in enumerate(a.get("steps", []), 1):
        exp = f"{s.expect_status}" + (f" {s.expect_error}" if s.expect_error else "")
        act = f"{s.result['status']}" + (f" {s.result['body'].get('error')}" if s.result['body'].get('error') else "")
        lines.append(f"| {n} | {s.title} | {exp} | {act} | {'PASS' if s.result['ok'] else 'FAIL'} | "
                     f"{s.result['note'].replace('|', '/')} |")
    (out / "verification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    rec = a.get("reconcile")
    if rec:
        cols = ["RECEIPT", "SPLIT_IN", "SPLIT_OUT", "MERGE_IN", "MERGE_OUT", "PRODUCE", "CONSUME", "SHIP"]
        heads = ["입고", "분할입", "분할출", "병합입", "병합출", "생산", "투입", "출하"]
        md = [
            "# 과제 06 재고 수불 대조표", "",
            "수불부(ledger) 합계와 LOT 현재고(lots.qty)를 대조한다. 차이는 모두 0이어야 한다.", "",
            "## LOT별", "",
            "| LOT | 품목 | 단위 | " + " | ".join(heads) + " | 수불합계 | 현재고 | 차이 | 판정 |",
            "|---|---|---|" + "---:|" * (len(heads) + 3) + "---|",
            *[f"| {r['lot_id']} | {r['item']} | {r['unit']} | " + " | ".join(str(r[c]) for c in cols)
              + f" | {r['ledger_balance']} | {r['on_hand']} | {r['diff']} | {'OK' if r['ok'] else 'NG'} |"
              for r in rec["lots"]],
            "", "## 품목별 보존식 (입고+생산) − (투입+출하) = 현재고 합계, 분할·병합 순증감 = 0", "",
            "| 품목 | 단위 | 입고+생산 | 투입+출하 | 분할·병합 순증감 | 현재고 합계 | 판정 |",
            "|---|---|---:|---:|---:|---:|---|",
            *[f"| {i['item']} | {i['unit']} | {i['inflow']} | {i['outflow']} | {i['split_merge_net']} | "
              f"{i['on_hand']} | {'OK' if i['ok'] else 'NG'} |" for i in rec["items"]],
            "", f"음수 재고 LOT: {rec['negative_lots']}개", "",
        ]
        (out / "inventory_reconciliation.md").write_text("\n".join(md), encoding="utf-8")
    (out / "concurrency.json").write_text(json.dumps(
        {k: a[k] for k in ("concurrency_a", "concurrency_b") if k in a}, ensure_ascii=False, indent=2),
        encoding="utf-8")
