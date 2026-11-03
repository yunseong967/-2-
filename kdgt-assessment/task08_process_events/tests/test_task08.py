"""과제 08 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
import random

import pytest

from common.jsonapi import http_client
from task08_process_events import events as E, simulator as S, verify
from task08_process_events.api import start_in_background

T0 = "2026-11-02T09:00:00"


def ev(eid, dev="WELD-01", seq=1, typ="COUNT", wo="WO-001", proc="WELD", t=T0, **kw):
    return {"event_id": eid, "device_id": dev, "seq": seq, "type": typ, "wo": wo, "process": proc, "occurred_at": t, **kw}


@pytest.fixture
def conn():
    c = E.connect(":memory:")
    yield c
    c.close()


# ---------------------------------------------------------------- 수신 검증
@pytest.mark.parametrize("bad, why", [
    ({"device_id": "PAINT-01"}, "등록되지 않은 설비"), ({"type": "EXPLODE"}, "type"), ({"seq": 0}, "seq"),
    ({"seq": "3"}, "seq"), ({"occurred_at": "2026-11-02 25:00"}, "occurred_at"), ({"good": -1}, "good"),
    ({"wo": None}, "wo"), ({"process": "CUT"}, "공정 설비"), ({"event_id": ""}, "event_id")])
def test_validation(conn, bad, why):
    e = {**ev("e1", good=1), **bad}
    assert why in E.validate(e)
    assert E.ingest(conn, [e], T0)["REJECTED"] == 1


def test_duplicate_and_conflicts(conn):
    a = ev("e1", good=2)
    assert E.ingest(conn, [a], T0)["ACCEPTED"] == 1
    assert E.ingest(conn, [a], T0)["DUPLICATE"] == 1                        # 같은 내용 재전송
    assert E.ingest(conn, [{**a, "good": 3}], T0)["CONFLICT"] == 1          # 같은 id, 다른 내용
    assert E.ingest(conn, [ev("e2", seq=1, good=1)], T0)["CONFLICT"] == 1   # 같은 seq, 다른 id
    s = E.build_state(conn, "2026-11-02T10:00:00")
    assert s["totals"]["good"]["WELD"] == 2 and s["totals"]["events"] == 1


# ---------------------------------------------------------------- 순서 독립
def test_end_arriving_before_start_gives_done(conn):
    evs = [ev("s", seq=1, typ="START", t="2026-11-02T09:00:00"), ev("c", seq=2, good=2, t="2026-11-02T09:10:00"),
           ev("x", seq=3, typ="STOP", reason="r", t="2026-11-02T09:20:00"), ev("r", seq=4, typ="RESUME", t="2026-11-02T09:30:00"),
           ev("e", seq=5, typ="END", t="2026-11-02T09:40:00")]
    E.ingest(conn, list(reversed(evs)), "2026-11-02T09:50:00")
    j = E.build_state(conn, "2026-11-02T10:00:00")["jobs"][0]
    assert j["status"] == "DONE" and j["good"] == 2 and j["stops"] == 1 and j["stop_min"] == 10


@pytest.mark.parametrize("seed", range(3))
def test_random_arrival_order_same_state(seed):
    events, plan = S.generate_truth()
    ref = E.state_fingerprint(E.truth_state(events, plan, "2026-11-02T21:00:00"))
    c = E.connect(":memory:")
    E.set_plan(c, plan)
    sh = events[:] + random.Random(seed).sample(events, 50)      # 섞고 중복 50건 추가
    random.Random(seed + 10).shuffle(sh)
    for i in range(0, len(sh), 23):
        E.ingest(c, sh[i:i + 23], "2026-11-02T21:00:00")
    assert E.state_fingerprint(E.build_state(c, "2026-11-02T21:00:00")) == ref


# ---------------------------------------------------------------- 수신시각·미수신·누락
def test_as_of_uses_received_time_not_occurred(conn):
    E.ingest(conn, [ev("a", seq=1, good=1, t="2026-11-02T09:00:00")], "2026-11-02T09:00:10")
    E.ingest(conn, [ev("b", seq=2, good=5, t="2026-11-02T09:05:00")], "2026-11-02T12:00:00")   # 늦게 도착
    assert E.build_state(conn, "2026-11-02T10:00:00")["totals"]["good"]["WELD"] == 1
    s = E.build_state(conn, "2026-11-02T12:00:00")
    assert s["totals"]["good"]["WELD"] == 6 and s["devices"]["WELD-01"]["late_receipts"] == 1


def test_stale_threshold_and_missing_seq(conn):
    E.ingest(conn, [ev("a", seq=1), ev("c", seq=4), ev("d", seq=5)], "2026-11-02T09:00:00")
    d = E.build_state(conn, "2026-11-02T09:15:00")["devices"]["WELD-01"]
    assert d["link"] == "OK" and d["missing_seq"] == 2 and d["missing_ranges"] == ["2-3"]
    assert E.build_state(conn, "2026-11-02T09:15:01")["devices"]["WELD-01"]["link"] == "STALE"
    assert E.build_state(conn, T0)["devices"]["LASER-01"]["link"] == "NO_DATA"


def test_upstream_without_records_is_unknown_not_delayed(conn):
    E.set_plan(conn, [{"wo": "WO-001", "process": p, "device_id": d, "planned_start": "2026-11-02T08:00:00",
                       "planned_end": "2026-11-02T08:30:00"} for p, d in (("CUT", "LASER-01"), ("WELD", "WELD-01"), ("INSP", "INSP-01"))])
    E.ingest(conn, [ev("i1", dev="INSP-01", proc="INSP", typ="START", t="2026-11-02T10:00:00")], "2026-11-02T10:00:00")
    jobs = {j["process"]: j for j in E.build_state(conn, "2026-11-02T11:00:00")["jobs"]}
    assert jobs["WELD"]["status"] == "UNKNOWN" and jobs["WELD"]["delay_min"] == 0
    assert jobs["CUT"]["status"] == "UNKNOWN"


def test_ranges():
    assert E._ranges([1, 2, 3, 5, 7, 8]) == ["1-3", "5", "7-8"] and E._ranges([]) == []


# ---------------------------------------------------------------- 시뮬레이터
def test_simulator_contract():
    events, plan = S.generate_truth()
    assert len(events) == 1000 and len({e["event_id"] for e in events}) == 1000
    for d in E.DEVICES:
        seqs = sorted(e["seq"] for e in events if e["device_id"] == d)
        assert seqs == list(range(1, len(seqs) + 1))
    assert all(E.validate(e) is None for e in events)
    deliveries, log = S.delivery_plan(events)
    kinds = {d.kind for d in deliveries}
    assert {"DUPLICATE", "REORDERED", "BUFFERED", "RETRANSMIT_DUP", "MALFORMED"} <= kinds
    assert {d.event["event_id"] for d in deliveries if d.kind != "MALFORMED"} == {e["event_id"] for e in events}


def test_full_verification_passes():
    checks, _ = verify.run(None)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


# ---------------------------------------------------------------- API
def test_api_guards(tmp_path):
    server, base, _ = start_in_background(str(tmp_path / "a.db"), sim=True)
    try:
        call = http_client(base)
        assert call("POST", "/api/events", {"events": [ev("a")]})[0] == 400               # sim_now 필요
        assert call("POST", "/api/events", {"events": "x", "sim_now": T0})[0] == 400
        assert call("POST", "/api/events", {"events": [ev("a", good=1)] * 5001, "sim_now": T0})[0] == 400
        assert call("POST", "/api/events", {"events": [ev("a", good=1)], "sim_now": T0})[1]["ACCEPTED"] == 1
        assert call("GET", "/api/state?now=bad")[0] == 400
        assert call("GET", "/api/ingest-log?result=ACCEPTED")[1][0]["event_id"] == "a"
    finally:
        server.shutdown()
        server.server_close()
