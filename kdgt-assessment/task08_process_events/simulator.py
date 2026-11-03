"""과제 08 공정 이벤트 시뮬레이터 (seed 고정).

1) 정답 이벤트 1,000건 생성: 2026-11-02 08:00~20:00, 설비 4대(레이저 1, 용접 2, 검사 1), 작업지시 12건
   WO 마다 가공 → 용접 → 검사. 이벤트: START, COUNT(양품·불량 증가분), STOP/RESUME(알람), END, HEARTBEAT
2) 전송 계획에 장애 주입 (모두 fault_log 에 기록)
   - 중복: 약 5% 이벤트를 0~5분 뒤 같은 event_id 로 한 번 더 전송 (at-least-once)
   - 역순: 약 8% 이벤트를 1~15분 늦게 도착 → 뒤에 발생한 이벤트보다 늦게 옴
   - 통신 단절 A: INSP-01 10:00~10:20 → 설비 버퍼에 쌓았다가 10:20 재연결 때 한꺼번에 재전송
   - 통신 단절 B: WELD-02 13:00 부터 끊김 → 근무 종료 후 20:10 복구 때 재전송 (버퍼 순서 뒤섞임 + 단절 직전 5건 중복 재전송)
   - 형식 오류 이벤트 3건 (정답에 포함되지 않음, 거부되어야 함)
3) 실행: 시뮬레이션 시각 순서대로 1분 단위 배치를 수집 API 로 보낸다
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .events import DEVICES

DAY = datetime(2026, 11, 2)
SHIFT_START, SHIFT_END = DAY.replace(hour=8), DAY.replace(hour=20)
OUTAGE_A = ("INSP-01", DAY.replace(hour=10), DAY.replace(hour=10, minute=20), DAY.replace(hour=10, minute=20))
OUTAGE_B = ("WELD-02", DAY.replace(hour=13), None, DAY.replace(hour=20, minute=10))   # 복구 = 근무 후 20:10
BEFORE_RECOVERY = DAY.replace(hour=20, minute=5)    # '복구 전' 집계 시점
AFTER_RECOVERY = DAY.replace(hour=20, minute=15)    # '복구 후' 집계 시점
N_EVENTS = 1000


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


@dataclass
class Delivery:
    at: datetime
    event: dict
    kind: str          # NORMAL / DUPLICATE / REORDERED / BUFFERED / RETRANSMIT_DUP / MALFORMED


def generate_truth(seed: int = 8) -> tuple[list[dict], list[dict]]:
    """(정답 이벤트 1,000건, 계획)"""
    rng = random.Random(seed)
    business: list[dict] = []
    plan: list[dict] = []
    # (1) 계획: 계획 작업시간만으로 먼저 고정한다 (실제 진행과 독립 — 처음엔 실제 진행으로 계획을 만들어 지연이 안 보였음)
    proc_dur = {"CUT": (20, 30), "WELD": (55, 85), "INSP": (15, 25)}
    free = {d: SHIFT_START for d in DEVICES}
    for i in range(1, 13):
        wo = f"WO-{i:03d}"
        ready = SHIFT_START + timedelta(minutes=10 * (i - 1))
        for proc in ("CUT", "WELD", "INSP"):
            dev = min((d for d, pr in DEVICES.items() if pr == proc), key=lambda d: (free[d], d))
            ps = max(free[dev], ready)
            pe = ps + timedelta(minutes=rng.randint(*proc_dur[proc]))
            plan.append({"wo": wo, "process": proc, "device_id": dev, "planned_start": iso(ps), "planned_end": iso(pe)})
            free[dev] = pe + timedelta(minutes=5)
            ready = pe + timedelta(minutes=10)
    # (2) 실제: 계획 대비 늦은 시작(0~10분), 작업시간 ±15%, 30% 확률 알람 정지 → 지연이 다음 공정·다음 WO 로 전파
    act_free = {d: SHIFT_START for d in DEVICES}
    prev_end = {}
    for p in plan:
        dev, wo, proc = p["device_id"], p["wo"], p["process"]
        ps, pe = datetime.fromisoformat(p["planned_start"]), datetime.fromisoformat(p["planned_end"])
        t = max(ps + timedelta(minutes=rng.randint(0, 10)), act_free[dev], prev_end.get(wo, SHIFT_START))
        run = (pe - ps).total_seconds() / 60 * rng.uniform(0.9, 1.15)
        business.append({"device_id": dev, "type": "START", "wo": wo, "process": proc, "occurred_at": t})
        end_t = t + timedelta(minutes=run)
        stop_at = t + timedelta(minutes=run * rng.uniform(0.3, 0.6)) if rng.random() < 0.3 else None
        stop_len = timedelta(minutes=rng.randint(8, 25)) if stop_at else timedelta(0)
        end_t += stop_len
        c = t + timedelta(minutes=5)
        while c < end_t - timedelta(minutes=2):
            if stop_at and stop_at <= c < stop_at + stop_len:
                c = stop_at + stop_len + timedelta(minutes=1)
                continue
            business.append({"device_id": dev, "type": "COUNT", "wo": wo, "process": proc, "occurred_at": c,
                             "good": rng.randint(1, 3), "ng": 1 if rng.random() < 0.08 else 0})
            c += timedelta(minutes=rng.randint(4, 7))
        if stop_at:
            business.append({"device_id": dev, "type": "STOP", "wo": wo, "process": proc, "occurred_at": stop_at,
                             "reason": rng.choice(["와이어 공급 이상", "가스 압력 저하", "치수 측정 재확인", "냉각수 온도 경보"])})
            business.append({"device_id": dev, "type": "RESUME", "wo": wo, "process": proc, "occurred_at": stop_at + stop_len})
        business.append({"device_id": dev, "type": "END", "wo": wo, "process": proc, "occurred_at": end_t})
        act_free[dev] = end_t + timedelta(minutes=5)
        prev_end[wo] = end_t + timedelta(minutes=10)
    last_end = max(e["occurred_at"] for e in business)
    assert last_end < SHIFT_END, f"작업이 근무 종료 후({last_end})까지 이어짐 — 복구 전후 집계 시점과 겹침"
    n_hb = N_EVENTS - len(business)
    assert n_hb > 0, "업무 이벤트가 1,000건을 넘음"
    devs = sorted(DEVICES)
    span = (SHIFT_END - SHIFT_START).total_seconds()
    for k in range(n_hb):                       # 하트비트로 정확히 1,000건 맞춤 (설비별 균등)
        d = devs[k % len(devs)]
        slot = k // len(devs)
        per = -(-n_hb // len(devs))
        business.append({"device_id": d, "type": "HEARTBEAT", "occurred_at": SHIFT_START + timedelta(seconds=span * (slot + 0.5) / per)})
    business.sort(key=lambda e: (e["occurred_at"], e["device_id"], e["type"]))
    seq = {d: 0 for d in DEVICES}
    events = []
    for e in business:
        seq[e["device_id"]] += 1
        events.append({"event_id": f"{e['device_id']}-{seq[e['device_id']]:05d}", "device_id": e["device_id"],
                       "seq": seq[e["device_id"]], "type": e["type"], "wo": e.get("wo"), "process": e.get("process"),
                       "good": e.get("good", 0), "ng": e.get("ng", 0), "reason": e.get("reason"),
                       "occurred_at": iso(e["occurred_at"])})
    return events, plan


def delivery_plan(events: list[dict], seed: int = 8) -> tuple[list[Delivery], list[dict]]:
    """(전송 목록, 장애 주입 기록)"""
    rng = random.Random(seed + 1)
    out, log = [], []

    def note(kind, e, at, detail=""):
        log.append({"kind": kind, "event_id": e.get("event_id"), "device_id": e.get("device_id"),
                    "occurred_at": e.get("occurred_at"), "deliver_at": iso(at), "detail": detail})

    dev_b, start_b, _, recover_b = OUTAGE_B
    dev_a, start_a, end_a, recover_a = OUTAGE_A
    buffered_b = []
    for e in events:
        t = datetime.fromisoformat(e["occurred_at"])
        base = t + timedelta(seconds=rng.randint(1, 20))
        if e["device_id"] == dev_b and t >= start_b:
            buffered_b.append(e)
            continue
        if e["device_id"] == dev_a and start_a <= t < end_a:
            at = recover_a + timedelta(seconds=rng.randint(0, 50))
            out.append(Delivery(at, e, "BUFFERED"))
            note("통신단절 버퍼→재전송", e, at, f"{dev_a} 10:00~10:20 단절, 10:20 재연결 때 전송")
            continue
        if rng.random() < 0.08:
            at = base + timedelta(minutes=rng.randint(1, 15))
            out.append(Delivery(at, e, "REORDERED"))
            note("역순 도착(지연)", e, at, f"{int((at - t).total_seconds() // 60)}분 늦게 도착")
        else:
            out.append(Delivery(base, e, "NORMAL"))
        if rng.random() < 0.05:
            at = base + timedelta(seconds=rng.randint(30, 300))
            out.append(Delivery(at, e, "DUPLICATE"))
            note("중복 전송", e, at, "같은 event_id 재전송")
    # WELD-02 복구: 버퍼를 뒤섞어 재전송 + 단절 직전 5건 중복
    rng.shuffle(buffered_b)
    for k, e in enumerate(buffered_b):
        at = recover_b + timedelta(seconds=k // 20)
        out.append(Delivery(at, e, "BUFFERED"))
        note("통신단절 버퍼→재전송", e, at, f"{dev_b} 13:00 단절, 20:10 복구 후 일괄 재전송(순서 뒤섞임)")
    before_b = [e for e in events if e["device_id"] == dev_b and datetime.fromisoformat(e["occurred_at"]) < start_b][-5:]
    for e in before_b:
        at = recover_b + timedelta(seconds=30)
        out.append(Delivery(at, e, "RETRANSMIT_DUP"))
        note("복구 시 중복 재전송", e, at, "설비가 전송 성공 여부를 몰라 단절 직전 5건을 다시 보냄")
    # 형식 오류 3건
    bad = [{"event_id": "BAD-1", "device_id": "PAINT-01", "seq": 1, "type": "COUNT", "wo": "WO-001", "process": "CUT",
            "occurred_at": "2026-11-02T09:00:00"},
           {"event_id": "BAD-2", "device_id": "LASER-01", "seq": 99999, "type": "COUNT", "wo": "WO-001", "process": "CUT",
            "good": -3, "occurred_at": "2026-11-02T09:10:00"},
           {"event_id": "BAD-3", "device_id": "WELD-01", "seq": 99998, "type": "START", "wo": "WO-002", "process": "WELD",
            "occurred_at": "2026-11-02 25:00"}]
    for i, e in enumerate(bad):
        at = DAY.replace(hour=9, minute=5 + 10 * i)
        out.append(Delivery(at, e, "MALFORMED"))
        note("형식 오류 주입", e, at, "거부되어야 함")
    out.sort(key=lambda d: (d.at, d.event.get("event_id") or ""))
    return out, log


def run(deliveries: list[Delivery], send, until: datetime | None = None, after: datetime | None = None) -> int:
    """after < 전송시각 <= until 인 전송을 1분 배치로 send(batch, sim_now) 호출. 보낸 건수 반환."""
    batch, cur, sent = [], None, 0
    for d in deliveries:
        if (after and d.at <= after) or (until and d.at > until):
            continue
        minute = d.at.replace(second=0) + timedelta(minutes=1)
        if cur is not None and minute != cur:
            send(batch, iso(cur))
            batch = []
        cur = minute
        batch.append(d.event)
        sent += 1
    if batch:
        send(batch, iso(cur))
    return sent


def write_fixtures(out: Path, seed: int = 8):
    events, plan = generate_truth(seed)
    deliveries, log = delivery_plan(events, seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "truth_events.json").write_text(json.dumps(events, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return events, plan, deliveries, log
