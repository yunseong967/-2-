"""과제 09 자동 검증.

완료 기준 (슬라이드)
  1. 지연 전후 일정 비교       — 계획/실제 두 일정을 만들고 프로젝트별 출하 변화 계산
  2. 불가능 일정 경고          — 순환 공정(계산 불가), 경합 없이도 못 맞추는 납기, 입력 오류
  3. 미완료 제외 건수와 평균 산식 공개 — 기준 시점까지 미완료 건 목록·건수, 평균 = Σ ÷ n 식
  + 수작업 정답 5건 대조, 일정 불변식(선후·자원 중복·근무시간) 전 프로젝트 검사, HTTP API 확인
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common.jsonapi import http_client
from . import schedule as S, workcal
from .api import start_in_background

HERE = Path(__file__).parent
AS_OF = datetime(2026, 11, 3)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def f(d):
    return d.strftime("%Y-%m-%dT%H:%M") if d else None


def invariants(projects, res: S.Result) -> list[str]:
    """모든 프로젝트에 대해: 선후 관계, 같은 호기 중복 사용, 사내 공정 근무시간 일치, 작업지시 이전 시작 금지."""
    bad = []
    byp = {p["project_id"]: p for p in projects}
    busy = {}
    for pid, rows in res.ops.items():
        end = {r["op"]: r["end"] for r in rows}
        spec = {o["op"]: o for o in byp[pid]["ops"]}
        for r in rows:
            if r["start"] < datetime.fromisoformat(byp[pid]["wo_confirmed_at"]):
                bad.append(f"{pid}/{r['op']} 작업지시 전 시작")
            for a in spec[r["op"]].get("after", []):
                if r["start"] < end[a]:
                    bad.append(f"{pid}/{r['op']} 이 선행 {a} 종료 전에 시작")
            if r["resource"] != "OUTSOURCE":
                wh = workcal.work_hours_between(r["start"], r["end"])
                if abs(wh - r["hours"]) > 1e-9:
                    bad.append(f"{pid}/{r['op']} 근무시간 {wh} ≠ {r['hours']}")
                busy.setdefault((r["resource"], r["unit"]), []).append((r["start"], r["end"], pid, r["op"]))
    for key, iv in busy.items():
        iv.sort()
        for (s1, e1, p1, o1), (s2, e2, p2, o2) in zip(iv, iv[1:]):
            if s2 < e1:
                bad.append(f"{key} 에서 {p1}/{o1} 와 {p2}/{o2} 겹침")
    return bad


def run(out: Path | None = None) -> list[Check]:
    projects = S.load_projects()
    hand = json.loads((HERE / "fixtures" / "hand_answers.json").read_text(encoding="utf-8"))["projects"]
    cmp = S.compare(projects, AS_OF)
    act, plan = cmp["actual"], cmp["plan"]
    lt = {r["project_id"]: r for r in S.lead_times(act, AS_OF)}
    checks: list[Check] = []

    # 0) 수작업 정답 5건
    rows, bad = [], []
    for pid, a in hand.items():
        m, ops = act.milestones[pid], {o["op"]: o for o in act.ops[pid]}
        got = {"pack_done": f(m["pack_done"]), "ship_done": f(m["ship_done"]), "mfg_h": lt[pid]["mfg_h"],
               "o2s_h": lt[pid]["o2s_h"], "plan_ship": f(plan.milestones[pid]["ship_done"]),
               "laser_start": f(ops["LASER"]["start"]), "laser_wait_h": ops["LASER"]["wait_h"],
               "out_arrival": f(ops["OUT"]["end"]),
               "shift_h": next(r["shift_h"] for r in cmp["rows"] if r["project_id"] == pid),
               "warnings": sorted({w.code for w in act.warnings if w.project_id == pid}),
               "excluded": lt[pid]["mfg_h"] is None}
        for k, v in a.items():
            g = got[k]
            ok = (sorted(v) == g) if k == "warnings" else (g == v)
            rows.append((pid, k, v, g, ok))
            if not ok:
                bad.append(f"{pid}.{k}: 정답 {v} / 계산 {g}")
    checks.append(Check(f"수작업 정답 5건 ({len(rows)}개 값) 대조", not bad, "; ".join(bad) or "전부 일치"))

    # 1) 지연 전후 비교
    delayed = [r for r in cmp["rows"] if r["delays"] and r["shift_h"] is not None]
    shifted = [r for r in delayed if r["shift_h"] > 0]
    newly_late = [r["project_id"] for r in cmp["rows"] if r["actual_late"] and not r["plan_late"]]
    ripple = [r["project_id"] for r in cmp["rows"] if not r["delays"] and r["shift_h"] not in (None, 0)]
    checks.append(Check("지연 전후 일정 비교", len(delayed) > 0 and len(shifted) > 0,
                        f"외주 지연 프로젝트 {len(delayed)}건 중 출하가 늦어진 {len(shifted)}건; 지연으로 새로 납기 위반 {newly_late}; "
                        f"지연이 없는데 자원 경합으로 일정이 바뀐 프로젝트 {ripple}"))

    # 2) 불가능 일정 경고
    codes = {(w.project_id, w.code) for w in act.warnings}
    need = {("P18", "PRECEDENCE_CYCLE"), ("P04", "DUE_IMPOSSIBLE"), ("P05", "DUE_UNRECOVERABLE"), ("P19", "WO_BEFORE_ORDER")}
    checks.append(Check("불가능 일정 경고 (순환 공정·처음부터 불가능한 납기·지연으로 회복 불가·입력 오류)", need <= codes and "P18" in act.unschedulable,
                        "; ".join(f"{w.project_id} {w.code}" for w in act.warnings if w.code != "DUE_MISS")
                        + f" / 납기 지연 예상 {sum(1 for w in act.warnings if w.code == 'DUE_MISS')}건"))

    # 3) 미완료 제외·산식
    k = cmp["kpi_actual"]
    ok = True
    for key in ("mfg_h", "o2s_h"):
        vals = [lt[p][key] for p in lt if lt[p][key] is not None]
        ok &= k[key]["n"] == len(vals) and abs(k[key]["mean_h"] - round(sum(vals) / len(vals), 2)) < 1e-9
        ok &= len(k[key]["excluded_incomplete"]) + k[key]["n"] == len(projects) - len(act.unschedulable)
    checks.append(Check("미완료 제외 건수와 평균 산식 공개", ok,
                        f"제조: {k['mfg_h']['formula']} = {k['mfg_h']['mean_h']}h, 미완료 제외 {len(k['mfg_h']['excluded_incomplete'])}건 / "
                        f"수주출하: {k['o2s_h']['formula']} = {k['o2s_h']['mean_h']}h, 미완료 제외 {len(k['o2s_h']['excluded_incomplete'])}건 / "
                        f"계산 불가 {k['n_unschedulable']}건"))

    # 4) 일정 불변식
    inv = invariants(projects, act) + invariants(projects, plan)
    n_ops = sum(len(v) for v in act.ops.values())
    checks.append(Check("일정 규칙 검사 (선후·자원 중복·근무시간·작업지시 이전 시작 금지)", not inv,
                        "; ".join(inv[:5]) or f"계획·실제 일정 공정 {n_ops * 2}개 전부 위반 없음"))

    # 5) API
    server, base = start_in_background(projects)
    try:
        call = http_client(base)
        s, d = call("GET", "/api/compare?as_of=2026-11-03T00:00")
        s2, d2 = call("GET", "/api/compare?as_of=bad")
        ok = s == 200 and d["kpi_actual"]["mfg_h"]["mean_h"] == k["mfg_h"]["mean_h"] and s2 == 400
    finally:
        server.shutdown()
        server.server_close()
    checks.append(Check("일정 비교 API", ok, f"정상 {s}, 잘못된 기준 시점 {s2}"))

    if out:
        _report(out, checks, rows, cmp, lt)
    return checks


def _report(out: Path, checks, rows, cmp, lt):
    out.mkdir(parents=True, exist_ok=True)
    L = ["# 과제 09 자동 검증 결과", "", "- 재실행: `python -m task09_delivery_kpi.cli verify`", "",
         "| 결과 | 항목 | 근거 |", "|---|---|---|",
         *[f"| {'PASS' if c.ok else 'FAIL'} | {c.name} | {c.detail} |" for c in checks], ""]
    (out / "verification.md").write_text("\n".join(L), encoding="utf-8")
    H = ["# 수작업 정답 5건 대조표", "", "손 추적 근거: `fixtures/hand_answers.md`", "",
         "| 프로젝트 | 항목 | 수작업 정답 | 계산 결과 | 판정 |", "|---|---|---|---|---|",
         *[f"| {p} | {k} | {v} | {g} | {'일치' if ok else '불일치'} |" for p, k, v, g, ok in rows], ""]
    (out / "hand_comparison.md").write_text("\n".join(H), encoding="utf-8")
    k = cmp["kpi_actual"]
    K = ["# 과제 09 KPI 보고서", "", f"기준 시점: {AS_OF:%Y-%m-%d %H:%M} (KST)", "",
         "## 정의", "",
         "| KPI | 시작 | 끝 | 단위 |", "|---|---|---|---|",
         "| 제조 리드타임 | 작업지시 확정 | 포장완료 | 경과 시간 h |",
         "| 수주출하 리드타임 | 수주 확정 | 출하완료 | 경과 시간 h |", "",
         "평균 = Σ(기준 시점까지 끝난 프로젝트의 리드타임) ÷ 끝난 프로젝트 수. 끝나지 않은 프로젝트는 제외하고 건수를 공개한다.",
         "일정 계산이 불가능한 프로젝트(순환 공정 등)는 분모·분자 모두에서 빠지며 따로 센다.", "",
         "## 결과", "", "| KPI | 완료 n | 합계 h | 평균 h | 중앙값 h | 미완료 제외 | 사업계획서 (구축 전 → 목표) |", "|---|---:|---:|---:|---:|---|---|",
         *[f"| {v['label']} | {v['n']} | {v['sum_h']:,} | **{v['mean_h']:,}** | {v['median_h']:,} | {len(v['excluded_incomplete'])}건 ({', '.join(v['excluded_incomplete'])}) | "
           f"{v['business_plan']['before']:,} → {v['business_plan']['target']:,} |" for v in (k["mfg_h"], k["o2s_h"])],
         f"| 계산 불가 | {k['n_unschedulable']}건 | | | | | |", "",
         "## 프로젝트별", "", "| 프로젝트 | 수주 확정 | 작업지시 | 포장완료 | 출하완료 | 제조 LT h | 수주출하 LT h |",
         "|---|---|---|---|---|---:|---:|",
         *[f"| {p} | {f(r['order_confirmed_at'])} | {f(r['wo_confirmed_at'])} | {f(r['pack_done'])} | {f(r['ship_done'])} | "
           f"{r['mfg_h'] if r['mfg_h'] is not None else '미완료'} | {r['o2s_h'] if r['o2s_h'] is not None else '미완료'} |"
           for p, r in sorted(lt.items())], "",
         "## 지연 전후 비교", "", "| 프로젝트 | 외주 지연 | 계획 출하 | 실제 출하 | 변화 h | 납기 | 계획→실제 |", "|---|---|---|---|---:|---|---|",
         *[f"| {r['project_id']} | {', '.join(f'{a} +{b}일' for a, b in r['delays'].items()) or '-'} | {f(r['plan_ship']) or '계산 불가'} | "
           f"{f(r['actual_ship']) or '계산 불가'} | {r['shift_h'] if r['shift_h'] is not None else '-'} | {r['due_date']} | "
           f"{'지연' if r['plan_late'] else '준수'}→{'지연' if r['actual_late'] else '준수'} |" for r in cmp["rows"]], "",
         "사업계획서 수치(1,320h 등)는 실제 공장 기준이고, 이 표는 합성 일정이므로 크기를 직접 비교하지 않는다. 같은 산식으로 계산된다는 것만 보인다.", ""]
    (out / "kpi_report.md").write_text("\n".join(K), encoding="utf-8")
