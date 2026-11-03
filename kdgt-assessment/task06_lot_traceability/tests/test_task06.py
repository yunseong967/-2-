"""과제 06 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
import json
import sqlite3
import threading
import urllib.request

import pytest

from task06_lot_traceability import service as svc
from task06_lot_traceability import verify
from task06_lot_traceability.api import start_in_background
from task06_lot_traceability.scenario import http_client


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t6.db"
    c = svc.connect(path)
    svc.init_db(c)
    c.close()
    return path


@pytest.fixture
def conn(db):
    c = svc.connect(db)
    yield c
    c.close()


def material(conn, lot, qty=100, item="SUS304 6t", passed=True):
    svc.receive(conn, lot, item, qty, "KG")
    if passed:
        svc.inspect(conn, lot, "PASS", "QC")


def product(conn, lot, qty, consumes, passed=True):
    svc.produce(conn, lot, "제품", qty, "EA", [{"lot_id": l, "qty": q} for l, q in consumes])
    if passed:
        svc.inspect(conn, lot, "PASS", "QC")


def err(fn, *a, **k):
    with pytest.raises(svc.DomainError) as e:
        fn(*a, **k)
    return e.value.code


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("lots", "lot_links", "ledger", "shipments", "inspections", "events")}


# ---------------------------------------------------------------- 전체 검증
def test_full_verification_passes():
    checks, _ = verify.run(None)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


# ---------------------------------------------------------------- 수량
@pytest.mark.parametrize("v", [0, -1, "abc", "1.2345", "NaN", "Infinity", None])
def test_invalid_quantities(v):
    with pytest.raises(svc.BadRequest):
        svc.to_m(v)


def test_decimal_quantities_have_no_float_drift(conn):
    material(conn, "M1", "0.3")
    svc.split(conn, "M1", ["0.1", "0.1", "0.1"])
    assert svc.lot_view(conn, "M1")["qty"] == 0
    assert svc.reconcile(conn)["ok"]


def test_negative_stock_rejected_by_db_constraint(conn):
    material(conn, "M1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE lots SET qty_m = -1 WHERE lot_id='M1'")


# ---------------------------------------------------------------- 분할 / 병합
def test_split_whole_quantity_and_inherit_status(conn):
    material(conn, "M1", 100, passed=False)
    r = svc.split(conn, "M1", [60, 40])
    assert r["parent"]["qty"] == 0
    assert [c["status"] for c in r["children"]] == ["PENDING", "PENDING"]


def test_split_failure_is_atomic(conn):
    material(conn, "M1", 100)
    material(conn, "TAKEN", 1)
    before = counts(conn)
    assert err(svc.split, conn, "M1", [10, 10], ["NEW-1", "TAKEN"]) == "DUPLICATE_LOT"
    assert counts(conn) == before
    assert svc.lot_view(conn, "M1")["qty"] == 100


def test_merge_pass_and_pending_becomes_pending(conn):
    material(conn, "A")
    material(conn, "B", passed=False)
    assert svc.merge(conn, ["A", "B"], "AB")["status"] == "PENDING"


def test_merge_with_fail_is_blocked(conn):
    material(conn, "A")
    svc.receive(conn, "B", "SUS304 6t", 10, "KG")
    svc.inspect(conn, "B", "FAIL", "QC")
    assert err(svc.merge, conn, ["A", "B"], "AB") == "STATUS_BLOCKED"


def test_merge_different_unit_blocked(conn):
    material(conn, "A")
    svc.receive(conn, "B", "SUS304 6t", 10, "EA")
    svc.inspect(conn, "B", "PASS", "QC")
    assert err(svc.merge, conn, ["A", "B"], "AB") == "ITEM_MISMATCH"


# ---------------------------------------------------------------- 생산
def test_produce_insufficient_rolls_back_product(conn):
    material(conn, "A", 10)
    material(conn, "B", 10)
    assert err(svc.produce, conn, "P1", "x", 1, "EA",
               [{"lot_id": "A", "qty": 5}, {"lot_id": "B", "qty": 50}]) == "INSUFFICIENT_QTY"
    assert conn.execute("SELECT 1 FROM lots WHERE lot_id='P1'").fetchone() is None
    assert svc.lot_view(conn, "A")["qty"] == 10  # 앞 투입분도 롤백


def test_produce_same_lot_twice_rejected(conn):
    material(conn, "A")
    with pytest.raises(svc.BadRequest):
        svc.produce(conn, "P1", "x", 1, "EA", [{"lot_id": "A", "qty": 1}, {"lot_id": "A", "qty": 1}])


# ---------------------------------------------------------------- 출하
def test_partial_shipments_until_zero(conn):
    material(conn, "A")
    product(conn, "P1", 3, [("A", 30)])
    for i in range(3):
        svc.ship(conn, f"R{i}", "P1", 1, "고객")
    assert err(svc.ship, conn, "R9", "P1", 1, "고객") == "INSUFFICIENT_QTY"


def test_replay_and_conflict(conn):
    material(conn, "A")
    product(conn, "P1", 3, [("A", 30)])
    first = svc.ship(conn, "R1", "P1", 1, "고객")
    again = svc.ship(conn, "R1", "P1", 1, "고객")
    assert again["replayed"] and again["shipment_id"] == first["shipment_id"]
    assert err(svc.ship, conn, "R1", "P1", 1, "다른고객") == "REQUEST_ID_CONFLICT"
    assert svc.lot_view(conn, "P1")["qty"] == 2


def test_shipment_evidence_is_frozen_at_ship_time(conn):
    material(conn, "A")
    product(conn, "P1", 2, [("A", 30)])
    s = svc.ship(conn, "R1", "P1", 1, "고객")
    svc.inspect(conn, "A", "FAIL", "QC", "사후 발견")
    stored = svc.shipment_view(conn, s["shipment_id"])["evidence"]
    assert all(e["result"] == "PASS" for e in stored)          # 출하 당시 근거 보존
    assert any(e["result"] == "FAIL" for e in svc.inspection_evidence(conn, "P1"))  # 현재 근거는 변함


# ---------------------------------------------------------------- 재검사 / 보류
def test_reinspection_restores_previous_status(conn):
    material(conn, "A")
    svc.produce(conn, "P1", "x", 1, "EA", [{"lot_id": "A", "qty": 1}])   # 미검사 제품
    r = svc.inspect(conn, "A", "FAIL", "QC")
    assert r["held_lots"] == ["P1"]
    r = svc.inspect(conn, "A", "PASS", "QC")
    assert r["released_lots"] == ["P1"]
    assert svc.lot_view(conn, "P1")["status"] == "PENDING"   # 합격이 아니라 원래 상태로


def test_release_kept_when_other_parent_still_fails(conn):
    material(conn, "A")
    material(conn, "B")
    product(conn, "P1", 1, [("A", 1), ("B", 1)])
    svc.inspect(conn, "A", "FAIL", "QC")
    svc.inspect(conn, "B", "FAIL", "QC")
    r = svc.inspect(conn, "A", "PASS", "QC")
    assert r["released_lots"] == []
    v = svc.lot_view(conn, "P1")
    assert v["status"] == "HOLD" and "B" in v["status_reason"]
    svc.inspect(conn, "B", "PASS", "QC")
    assert svc.lot_view(conn, "P1")["status"] == "PASS"


def test_product_reinspection_cannot_override_failed_material(conn):
    material(conn, "A")
    product(conn, "P1", 1, [("A", 1)])
    svc.inspect(conn, "A", "FAIL", "QC")
    svc.inspect(conn, "P1", "PASS", "QC")
    assert err(svc.ship, conn, "R1", "P1", 1, "고객") == "UPSTREAM_FAIL"


def test_affected_shipments_include_split_and_merge_paths(conn):
    material(conn, "A")
    material(conn, "B")
    svc.split(conn, "A", [10])
    svc.merge(conn, ["A-S1", "B"], "AB")
    product(conn, "P1", 2, [("AB", 5)])
    svc.ship(conn, "R1", "P1", 1, "고객")
    r = svc.inspect(conn, "A", "FAIL", "QC")
    assert [s["request_id"] for s in r["affected_shipments"]] == ["R1"]
    assert set(r["held_lots"]) == {"A-S1", "AB", "P1"}


def test_backward_trace_reaches_all_raw_materials(conn):
    material(conn, "A")
    material(conn, "B")
    svc.merge(conn, ["A", "B"], "AB")
    product(conn, "SUB", 1, [("AB", 5)])
    product(conn, "TOP", 1, [("SUB", 1)])
    assert sorted(svc.trace_backward(conn, "TOP")["raw_materials"]) == ["A", "B"]


# ---------------------------------------------------------------- 수불
def test_reconcile_detects_tampering(conn):
    material(conn, "A")
    assert svc.reconcile(conn)["ok"]
    conn.execute("UPDATE lots SET qty_m = qty_m - 1000 WHERE lot_id='A'")  # 수불부 없이 직접 수정
    rec = svc.reconcile(conn)
    assert not rec["ok"] and [r["lot_id"] for r in rec["lots"] if not r["ok"]] == ["A"]


# ---------------------------------------------------------------- 서비스 레벨 동시성 (분할 vs 출하)
def test_concurrent_split_and_produce_never_oversell(db):
    c = svc.connect(db)
    material(c, "A", 100)
    c.close()
    barrier = threading.Barrier(10)
    results = []

    def worker(i):
        cc = svc.connect(db)
        barrier.wait()
        try:
            if i % 2:
                svc.split(cc, "A", [15], [f"A-X{i}"])
            else:
                svc.produce(cc, f"P{i}", "x", 1, "EA", [{"lot_id": "A", "qty": 15}])
            results.append("ok")
        except svc.DomainError as e:
            results.append(e.code)
        finally:
            cc.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    c = svc.connect(db)
    assert results.count("ok") == 6 and results.count("INSUFFICIENT_QTY") == 4   # 100 // 15 = 6
    assert svc.lot_view(c, "A")["qty"] == 10
    assert svc.reconcile(c)["ok"]
    c.close()


# ---------------------------------------------------------------- API
@pytest.fixture
def api(db):
    server, base = start_in_background(str(db))
    yield base, http_client(base)
    server.shutdown()
    server.server_close()


def test_api_errors(api):
    base, call = api
    assert call("GET", "/api/lots/NOPE") == (404, call("GET", "/api/lots/NOPE")[1])
    assert call("GET", "/api/lots/NOPE")[1]["error"] == "LOT_NOT_FOUND"
    assert call("POST", "/api/receipts", {"lot_id": "A"})[1]["error"] == "MISSING_FIELD"
    assert call("POST", "/api/nowhere", {})[0] == 404
    req = urllib.request.Request(base + "/api/receipts", data=b"[1,2]", method="POST")
    try:
        urllib.request.urlopen(req)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 400 and json.loads(e.read())["error"] == "INVALID_JSON"


def test_api_serves_trace_screen(api):
    base, _ = api
    with urllib.request.urlopen(base + "/") as r:
        assert r.status == 200 and "LOT 양방향 추적" in r.read().decode()
