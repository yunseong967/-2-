"""과제 08 자동 검증: 시뮬레이터 → 실제 HTTP 수집 API → 상태 비교.

완료 기준 (슬라이드)
  1. 재전송 후 수량 일치        — 복구 후 집계(이벤트 수, 공정별 양품·불량, 작업 상태) = 정답
  2. 중복 집계 방지             — 중복 60건 전송, 저장은 1,000건, 수량 부풀림 0
  3. 역순 이벤트 후 상태 일관성 — 같은 이벤트 집합을 무작위 순서 5가지로 넣어도 상태 동일
                                  + 도착 순서대로 계산하는 순진한 방식과 비교
  + 발생/수신 시각 분리와 미수신 표시, 형식 오류 거부, 장애 주입 로그, 복구 전/후 집계표
"""
from __future__ import annotations

import csv
import json
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from common.jsonapi import http_client
from . import events as E, simulator as S
from .api import start_in_background

HERE = Path(__file__).parent


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def naive_arrival_state(deliveries: list, until) -> dict:
    """비교용 '순진한' 방식: 도착 순서대로, 중복 제거 없이 누적. (이렇게 하면 무엇이 틀어지는지 보이기 위함)"""
    status, good = {}, {}
    for d in deliveries:
        if d.at > until or d.kind == "MALFORMED" or d.event["type"] == "HEARTBEAT":
            continue
        e = d.event
        k = (e["wo"], e["process"])
        if e["type"] in ("START", "RESUME"):
            status[k] = "RUNNING"
        elif e["type"] == "STOP":
            status[k] = "STOPPED"
        elif e["type"] == "END":
            status[k] = "DONE"
        elif e["type"] == "COUNT":
            good[e["process"]] = good.get(e["process"], 0) + e["good"]
    return {"status": status, "good": good}


def run(out: Path | None = None) -> tuple[list[Check], dict]:
    events, plan = S.generate_truth()
    deliveries, fault_log = S.delivery_plan(events)
    before_t, after_t = S.iso(S.BEFORE_RECOVERY), S.iso(S.AFTER_RECOVERY)
    truth = E.truth_state(events, plan, after_t)
    checks, art = [], {"fault_log": fault_log}
    ingest_results = []

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t8.db"
        server, base, clock = start_in_background(str(db), sim=True)
        call = http_client(base)
        try:
            call("POST", "/api/plan", plan)

            def send(batch, now):
                s, r = call("POST", "/api/events", {"events": batch, "sim_now": now})
                assert s == 200, r
                ingest_results.append((now, len(batch), r))

            mid = S.DAY.replace(hour=10, minute=18)
            S.run(deliveries, send, until=mid)
            art["mid"] = call("GET", f"/api/state?now={S.iso(mid)}")[1]          # INSP-01 단절 중
            S.run(deliveries, send, after=mid, until=S.BEFORE_RECOVERY)
            before = call("GET", f"/api/state?now={before_t}")[1]                # WELD-02 단절 중 (복구 전)
            S.run(deliveries, send, after=S.BEFORE_RECOVERY)
            after = call("GET", f"/api/state?now={after_t}")[1]                  # 복구 후
            before_again = call("GET", f"/api/state?now={before_t}")[1]          # 나중에 다시 본 복구 전
        finally:
            server.shutdown()
            server.server_close()

        # 순서 독립성: 같은 1,000건을 무작위 순서 5가지로 넣기
        prints = []
        for seed in range(5):
            conn = E.connect(":memory:")
            E.set_plan(conn, plan)
            shuffled = events[:]
            random.Random(seed).shuffle(shuffled)
            for i in range(0, len(shuffled), 37):
                E.ingest(conn, shuffled[i:i + 37], after_t)
            prints.append(E.state_fingerprint(E.build_state(conn, after_t)))
            conn.close()

    art.update(before=before, after=after, truth=truth)
    sent_dup = sum(1 for d in deliveries if d.kind in ("DUPLICATE", "RETRANSMIT_DUP"))
    ing = after["ingest"]

    # 1) 재전송 후 수량 일치
    same = (after["totals"] == truth["totals"] and E.state_fingerprint(after) == E.state_fingerprint(truth))
    jobs_diff = [j["wo"] + "/" + j["process"] for j, t in zip(after["jobs"], truth["jobs"])
                 if (j["status"], j["good"], j["ng"]) != (t["status"], t["good"], t["ng"])]
    checks.append(Check("재전송 후 수량 일치 (복구 후 = 정답)", same and not jobs_diff,
                        f"이벤트 {after['totals']['events']}/{truth['totals']['events']}, 양품 {after['totals']['good']} = 정답 {truth['totals']['good']}, "
                        f"불량 {after['totals']['ng']}, 상태 다른 작업 {jobs_diff or '없음'}"))
    miss_before = truth["totals"]["events"] - before["totals"]["events"]
    checks.append(Check("복구 전에는 누락이 보임 (단절 구간 미수신)", miss_before > 0 and before["devices"]["WELD-02"]["link"] == "STALE",
                        f"복구 전 이벤트 {before['totals']['events']}건 (정답보다 {miss_before}건 적음), WELD-02 {before['devices']['WELD-02']['silent_min']}분 미수신"))

    # 2) 중복 집계 방지
    naive = naive_arrival_state(deliveries, S.AFTER_RECOVERY)
    inflated = {p: naive["good"].get(p, 0) - truth["totals"]["good"].get(p, 0) for p in truth["totals"]["good"]}
    ok = ing.get("DUPLICATE", 0) == sent_dup and after["totals"]["events"] == S.N_EVENTS
    checks.append(Check("중복 집계 방지", ok,
                        f"중복 전송 {sent_dup}건 → DUPLICATE 처리 {ing.get('DUPLICATE', 0)}건, 저장 {after['totals']['events']}건. "
                        f"(중복 제거 없이 세면 양품이 {inflated} 만큼 부풀려짐)"))

    # 3) 역순 이벤트 후 상태 일관성
    naive_wrong = [f"{k[0]}/{k[1]}" for k, v in naive["status"].items()
                   if v != next(j["status"] for j in truth["jobs"] if (j["wo"], j["process"]) == k)]
    ok = len(set(prints)) == 1 and prints[0] == E.state_fingerprint(truth)
    checks.append(Check("역순 이벤트 후 상태 일관성", ok,
                        f"무작위 도착 순서 5가지 → 상태 해시 {len(set(prints))}종 (정답과 동일={prints[0] == E.state_fingerprint(truth)}). "
                        f"도착 순서대로 상태를 바꾸는 순진한 방식이면 작업 {len(naive_wrong)}건 상태가 틀림 {naive_wrong[:4]}"))

    # 4) 발생/수신 분리, 미수신 표시
    m = art["mid"]["devices"]["INSP-01"]
    a2 = after["devices"]["WELD-02"]
    uncertain = [j["wo"] for j in before["jobs"] if any("상태 불확실" in f for f in j["flags"])]
    unknown = [j["wo"] for j in before["jobs"] if j["status"] == "UNKNOWN"]
    ok = (m["link"] == "STALE" and a2["link"] == "OK" and a2["late_receipts"] > 0 and bool(uncertain) and bool(unknown)
          and not any(j["status"] == "UNKNOWN" for j in after["jobs"]))
    checks.append(Check("발생시각·수신시각 분리, 마지막 갱신시각·미수신 표시", ok,
                        f"10:18 INSP-01 마지막 수신 {m['last_received_at']} ({m['silent_min']}분 미수신) / "
                        f"복구 전 WELD-02 마지막 수신 {before['devices']['WELD-02']['last_received_at']}, 상태 불확실 {uncertain}, "
                        f"후공정은 진행됐는데 기록 없는 '미확인' {unknown} (복구 후 0건) / "
                        f"복구 후 WELD-02 지연 수신 {a2['late_receipts']}건"))

    # 5) 형식 오류 거부·충돌 없음
    ok = ing.get("REJECTED", 0) == 3 and ing.get("CONFLICT", 0) == 0
    checks.append(Check("형식 오류 거부", ok, f"거부 {ing.get('REJECTED', 0)}건 (주입 3건), 충돌 {ing.get('CONFLICT', 0)}건"))

    # 6) 시점 재현성
    ok = E.state_fingerprint(before) == E.state_fingerprint(before_again) and before["totals"] == before_again["totals"]
    checks.append(Check("과거 시점 상태 재현 (복구 전 화면을 나중에 다시 계산해도 동일)", ok, "수신시각 기준 필터"))

    art["ingest_results"] = ingest_results
    art["naive_inflated"] = inflated
    if out:
        _report(Path(out), checks, art, deliveries)
    return checks, art


def _report(out: Path, checks, a, deliveries):
    out.mkdir(parents=True, exist_ok=True)
    L = ["# 과제 08 자동 검증 결과", "", "- 시뮬레이터 → 실제 HTTP 수집 API(시뮬레이션 시계) → 상태 조회",
         "- 재실행: `python -m task08_process_events.cli verify`", "",
         "| 결과 | 항목 | 근거 |", "|---|---|---|",
         *[f"| {'PASS' if c.ok else 'FAIL'} | {c.name} | {c.detail} |" for c in checks], ""]
    (out / "verification.md").write_text("\n".join(L), encoding="utf-8")

    with open(out / "fault_injection_log.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "event_id", "device_id", "occurred_at", "deliver_at", "detail"])
        w.writeheader()
        w.writerows(a["fault_log"])
    kinds = {}
    for r in a["fault_log"]:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1

    b, af, t = a["before"], a["after"], a["truth"]
    procs = ["CUT", "WELD", "INSP"]

    def row(label, f):
        return f"| {label} | {f(b)} | {f(af)} | {f(t)} | {'일치' if f(af) == f(t) else '불일치'} |"
    R = ["# 복구 전/후 집계 비교", "",
         f"- 복구 전: {b['now']} (WELD-02 통신 단절 중) / 복구 후: {af['now']} (재전송 완료) / 정답: 시뮬레이터 원본 1,000건", "",
         "## 장애 주입 요약 (`fault_injection_log.csv`)", "",
         "| 장애 | 건수 |", "|---|---:|", *[f"| {k} | {v} |" for k, v in kinds.items()], "",
         f"수신 처리: {af['ingest']}", "",
         "## 집계", "", "| 항목 | 복구 전 | 복구 후 | 정답 | 복구 후 판정 |", "|---|---:|---:|---:|---|",
         row("저장 이벤트 수", lambda s: s["totals"]["events"]),
         *[row(f"{E.PROCESS_LABEL[p]} 양품", lambda s, p=p: s["totals"]["good"].get(p, 0)) for p in procs],
         *[row(f"{E.PROCESS_LABEL[p]} 불량", lambda s, p=p: s["totals"]["ng"].get(p, 0)) for p in procs],
         *[row(f"{d} 이벤트", lambda s, d=d: s["devices"][d]["events"]) for d in E.DEVICES],
         row("완료(DONE) 작업 수", lambda s: sum(j["status"] == "DONE" for j in s["jobs"])),
         row("진행 중으로 보이는 작업", lambda s: sum(j["status"] in ("RUNNING", "STOPPED") for j in s["jobs"])), "",
         "## 설비 상태", "", "| 설비 | 복구 전 연결 | 복구 전 마지막 수신 | 복구 후 연결 | 복구 후 지연 수신 |", "|---|---|---|---|---:|",
         *[f"| {d} | {b['devices'][d]['link']} ({b['devices'][d]['silent_min']}분) | {b['devices'][d]['last_received_at']} | "
           f"{af['devices'][d]['link']} | {af['devices'][d]['late_receipts']} |" for d in E.DEVICES], "",
         f"참고: 중복 제거 없이 도착 순서대로 세면 양품이 {a['naive_inflated']} 만큼 부풀려진다.", ""]
    (out / "recovery_compare.md").write_text("\n".join(R), encoding="utf-8")
