"""과제 08 이벤트 수집·상태 계산.

수집 원칙
  - event_id 는 설비가 붙이는 고유 번호 → 같은 event_id 는 한 번만 저장 (중복 재전송 무시)
  - (device_id, seq) 는 설비별 일련번호 → 빈 번호 = 아직 못 받은 이벤트(통신 단절·유실)
  - 같은 event_id 인데 내용이 다르거나, 같은 seq 에 다른 event_id → 충돌로 거부하고 기록
  - occurred_at(설비 발생시각)과 received_at(서버 수신시각)을 따로 저장

상태 원칙
  - 상태는 '받은 이벤트 집합'만의 함수다. 도착 순서가 아니라 (occurred_at, device_id, seq) 순으로 다시 재생해 만든다
    → 역순 도착·재전송 뒤에도 같은 이벤트 집합이면 같은 상태
  - 수량은 COUNT 이벤트의 증가분 합 → 중복이 저장되지 않으므로 두 번 세지 않는다
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DEVICES = {"LASER-01": "CUT", "WELD-01": "WELD", "WELD-02": "WELD", "INSP-01": "INSP"}
PROCESS_LABEL = {"CUT": "가공(레이저)", "WELD": "용접", "INSP": "검사"}
TYPES = {"START", "COUNT", "STOP", "RESUME", "END", "HEARTBEAT"}
STALE_MIN = 15          # 마지막 수신 후 15분 넘게 아무것도 안 오면 '미수신'
LATE_RECEIPT_MIN = 5    # 발생 후 5분 넘게 늦게 도착한 이벤트 = 지연 수신

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    type        TEXT NOT NULL,
    wo          TEXT,
    process     TEXT,
    good        INTEGER NOT NULL DEFAULT 0,
    ng          INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE (device_id, seq)
);
CREATE TABLE IF NOT EXISTS ingest_log (       -- 수신 처리 결과 (중복·거부·충돌 포함)
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    event_id    TEXT,
    device_id   TEXT,
    seq         INTEGER,
    result      TEXT NOT NULL,               -- ACCEPTED / DUPLICATE / REJECTED / CONFLICT
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS plan (
    wo TEXT NOT NULL, process TEXT NOT NULL, device_id TEXT NOT NULL,
    planned_start TEXT NOT NULL, planned_end TEXT NOT NULL, PRIMARY KEY (wo, process)
);
"""


def connect(path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(SCHEMA)
    return conn


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def payload_hash(e: dict) -> str:
    keys = ("event_id", "device_id", "seq", "type", "wo", "process", "good", "ng", "reason", "occurred_at")
    return hashlib.sha256(json.dumps([e.get(k) for k in keys], ensure_ascii=False).encode()).hexdigest()[:16]


def validate(e) -> str | None:
    if not isinstance(e, dict):
        return "이벤트가 객체가 아님"
    for k in ("event_id", "device_id", "seq", "type", "occurred_at"):
        if e.get(k) in (None, ""):
            return f"필수값 누락: {k}"
    if e["device_id"] not in DEVICES:
        return f"등록되지 않은 설비: {e['device_id']}"
    if e["type"] not in TYPES:
        return f"알 수 없는 type: {e['type']}"
    if not isinstance(e["seq"], int) or e["seq"] < 1:
        return f"seq 는 1 이상 정수: {e['seq']!r}"
    try:
        _ts(e["occurred_at"])
    except (TypeError, ValueError):
        return f"occurred_at 형식 오류: {e['occurred_at']!r}"
    if e["type"] != "HEARTBEAT" and not e.get("wo"):
        return "작업지시(wo) 누락"
    if e["type"] != "HEARTBEAT" and e.get("process") != DEVICES[e["device_id"]]:
        return f"설비 {e['device_id']} 는 {DEVICES[e['device_id']]} 공정 설비인데 process={e.get('process')!r}"
    for k in ("good", "ng"):
        v = e.get(k, 0)
        if not isinstance(v, int) or v < 0:
            return f"{k} 는 0 이상 정수: {v!r}"
    return None


def ingest(conn, batch: list, received_at: str) -> dict:
    """배치 수신. 한 트랜잭션으로 처리하고 이벤트마다 결과를 ingest_log 에 남긴다."""
    out = {"ACCEPTED": 0, "DUPLICATE": 0, "REJECTED": 0, "CONFLICT": 0}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for e in batch:
            err = validate(e)
            eid = e.get("event_id") if isinstance(e, dict) else None
            dev = e.get("device_id") if isinstance(e, dict) else None
            seq = e.get("seq") if isinstance(e, dict) else None
            if err:
                result, detail = "REJECTED", err
            else:
                h = payload_hash(e)
                same_id = conn.execute("SELECT payload_hash FROM events WHERE event_id=?", (eid,)).fetchone()
                same_seq = conn.execute("SELECT event_id FROM events WHERE device_id=? AND seq=?", (dev, seq)).fetchone()
                if same_id:
                    result, detail = ("DUPLICATE", "같은 이벤트 재수신 — 무시") if same_id[0] == h \
                        else ("CONFLICT", "같은 event_id 에 다른 내용")
                elif same_seq:
                    result, detail = "CONFLICT", f"{dev} seq {seq} 는 이미 {same_seq[0]}"
                else:
                    conn.execute(
                        "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (eid, dev, seq, e["type"], e.get("wo"), e.get("process"), e.get("good", 0), e.get("ng", 0),
                         e.get("reason"), e["occurred_at"], received_at, h))
                    result, detail = "ACCEPTED", None
            out[result] += 1
            conn.execute("INSERT INTO ingest_log (received_at, event_id, device_id, seq, result, detail) VALUES (?,?,?,?,?,?)",
                         (received_at, eid if isinstance(eid, str) else None, dev if isinstance(dev, str) else None,
                          seq if isinstance(seq, int) else None, result, detail))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return out


def set_plan(conn, rows: list[dict]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM plan")
    conn.executemany("INSERT INTO plan VALUES (?,?,?,?,?)",
                     [(r["wo"], r["process"], r["device_id"], r["planned_start"], r["planned_end"]) for r in rows])
    conn.execute("COMMIT")


# ---------------------------------------------------------------- 상태 계산
def build_state(conn, now: str) -> dict:
    """now 시점까지 '받은' 이벤트만으로 상태를 다시 계산한다 (도착 순서와 무관).
    수신시각 ≤ now 로 거르므로, 나중에 다시 계산해도 그 시점에 화면에 보였던 상태가 그대로 재현된다."""
    nowt = _ts(now)
    evs = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE received_at <= ? ORDER BY occurred_at, device_id, seq", (now,))]
    plan = {(r["wo"], r["process"]): dict(r) for r in conn.execute("SELECT * FROM plan")}

    # 설비
    devices = {}
    for d, proc in DEVICES.items():
        mine = [e for e in evs if e["device_id"] == d]
        seqs = {e["seq"] for e in mine}
        maxseq = max(seqs) if seqs else 0
        missing = sorted(set(range(1, maxseq + 1)) - seqs)
        last_recv = max((e["received_at"] for e in mine), default=None)
        last_occ = max((e["occurred_at"] for e in mine), default=None)
        silent_min = (nowt - _ts(last_recv)).total_seconds() / 60 if last_recv else None
        late = [e for e in mine if (_ts(e["received_at"]) - _ts(e["occurred_at"])).total_seconds() / 60 > LATE_RECEIPT_MIN]
        work = None
        for e in mine:                        # 발생시각 순서로 재생
            if e["type"] in ("START", "RESUME"):
                work = {"wo": e["wo"], "state": "RUNNING"}
            elif e["type"] == "STOP":
                work = {"wo": e["wo"], "state": "STOPPED", "reason": e["reason"]}
            elif e["type"] == "END":
                work = None
        devices[d] = {
            "device_id": d, "process": proc, "events": len(mine), "max_seq": maxseq,
            "missing_seq": len(missing), "missing_ranges": _ranges(missing),
            "last_occurred_at": last_occ, "last_received_at": last_recv,
            "silent_min": round(silent_min, 1) if silent_min is not None else None,
            "link": "NO_DATA" if last_recv is None else ("STALE" if silent_min > STALE_MIN else "OK"),
            "late_receipts": len(late), "work": work}

    # 작업지시 × 공정
    jobs = {}
    for e in evs:
        if e["type"] == "HEARTBEAT":
            continue
        key = (e["wo"], e["process"])
        j = jobs.setdefault(key, {"wo": e["wo"], "process": e["process"], "device_id": e["device_id"], "status": "WAITING",
                                  "started_at": None, "ended_at": None, "good": 0, "ng": 0, "stops": 0,
                                  "stop_min": 0.0, "_stop_at": None, "last_occurred_at": None, "flags": []})
        t = _ts(e["occurred_at"])
        if e["type"] == "START":
            j["status"], j["started_at"] = "RUNNING", e["occurred_at"]
        elif e["type"] == "STOP":
            j["status"], j["_stop_at"], j["stops"] = "STOPPED", t, j["stops"] + 1
        elif e["type"] == "RESUME":
            if j["_stop_at"]:
                j["stop_min"] += (t - j["_stop_at"]).total_seconds() / 60
            j["status"], j["_stop_at"] = "RUNNING", None
        elif e["type"] == "END":
            j["status"], j["ended_at"] = "DONE", e["occurred_at"]
        elif e["type"] == "COUNT":
            j["good"] += e["good"]
            j["ng"] += e["ng"]
        j["last_occurred_at"] = e["occurred_at"]
    for key, p in plan.items():
        jobs.setdefault(key, {"wo": key[0], "process": key[1], "device_id": p["device_id"], "status": "WAITING",
                              "started_at": None, "ended_at": None, "good": 0, "ng": 0, "stops": 0, "stop_min": 0.0,
                              "_stop_at": None, "last_occurred_at": None, "flags": []})
    for key, j in jobs.items():
        j.pop("_stop_at")
        j["stop_min"] = round(j["stop_min"], 1)
        if j["status"] == "DONE" and not j["started_at"]:
            j["flags"].append("시작 기록 누락")
        p = plan.get(key)
        j["planned_start"], j["planned_end"] = (p["planned_start"], p["planned_end"]) if p else (None, None)
        j["delay_min"] = 0.0
        if p:
            pe, ps = _ts(p["planned_end"]), _ts(p["planned_start"])
            if j["status"] == "DONE":
                j["delay_min"] = max(0.0, (_ts(j["ended_at"]) - pe).total_seconds() / 60)
            elif j["status"] in ("RUNNING", "STOPPED"):
                j["delay_min"] = max(0.0, (nowt - pe).total_seconds() / 60)
            elif nowt > ps:
                j["delay_min"] = (nowt - ps).total_seconds() / 60
                j["flags"].append("시작 지연")
        j["delay_min"] = round(j["delay_min"], 1)
        if devices[j["device_id"]]["link"] == "STALE" and j["status"] in ("RUNNING", "STOPPED"):
            j["flags"].append("설비 미수신 — 상태 불확실")
    # 후공정이 이미 시작됐는데 선공정 기록이 없으면 '대기'가 아니라 '미확인'(기록 미수신 의심).
    # 처음엔 이 경우를 '대기 + 시작 지연 459분'으로 보여 줬다 — AI_LOG 오류 #2
    order_idx = {"CUT": 0, "WELD": 1, "INSP": 2}
    for (wo, proc), j in jobs.items():
        if j["status"] != "WAITING":
            continue
        later = [x for (w, pr), x in jobs.items() if w == wo and order_idx[pr] > order_idx[proc] and x["started_at"]]
        if later:
            j["status"], j["delay_min"] = "UNKNOWN", 0.0
            j["flags"] = [f for f in j["flags"] if f != "시작 지연"] + \
                [f"후공정({', '.join(PROCESS_LABEL[x['process']] for x in later)})이 이미 시작됨 — 이 공정 기록 미수신 의심"]

    totals = {"events": len(evs), "by_type": {}, "good": {}, "ng": {}}
    for e in evs:
        totals["by_type"][e["type"]] = totals["by_type"].get(e["type"], 0) + 1
        if e["type"] == "COUNT":
            totals["good"][e["process"]] = totals["good"].get(e["process"], 0) + e["good"]
            totals["ng"][e["process"]] = totals["ng"].get(e["process"], 0) + e["ng"]
    log = {r[0]: r[1] for r in conn.execute(
        "SELECT result, COUNT(*) FROM ingest_log WHERE received_at <= ? GROUP BY result", (now,))}
    order = {"CUT": 0, "WELD": 1, "INSP": 2}
    return {"now": now, "devices": devices, "jobs": sorted(jobs.values(), key=lambda j: (j["wo"], order[j["process"]])),
            "totals": totals, "ingest": log}


def _ranges(nums: list[int]) -> list[str]:
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return out


def state_fingerprint(state: dict) -> str:
    """도착 순서 독립성 검사용: 수신시각에 의존하는 값(마지막 수신·지연 수신·미수신)을 뺀 상태의 해시."""
    jobs = [{**j, "flags": [f for f in j["flags"] if "미수신" not in f]} for j in state["jobs"]]
    core = {"jobs": jobs, "totals": state["totals"],
            "devices": {d: {k: v for k, v in x.items() if k not in ("last_received_at", "silent_min", "link", "late_receipts")}
                        for d, x in state["devices"].items()}}
    return hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def truth_state(events: list[dict], plan: list[dict], now: str) -> dict:
    """정답 상태: 모든 이벤트가 정상 수신됐다고 가정하고 같은 계산을 적용."""
    conn = connect(":memory:")
    set_plan(conn, plan)
    ingest(conn, events, received_at=now)
    return build_state(conn, now)
