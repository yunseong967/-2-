"""과제 09 일정 계산 + KPI.

일정 규칙 (손으로 추적할 수 있게 단순하고 결정적으로 만든다)
  1. 우선순위: 납기가 빠른 프로젝트 먼저(EDD), 같으면 프로젝트 번호 순
  2. 우선순위 순서대로 프로젝트 하나씩, 그 안에서는 선후 관계 순서(입력 순서 유지)대로 공정을 배정
  3. 공정 시작 가능 시각 = max(작업지시 확정 시각, 선행 공정들의 종료 시각)
  4. 사내 공정: 해당 자원(설비·팀) 중 가장 빨리 '빈 구간'에 넣을 수 있는 호기에 배정 (빈틈 끼워넣기 허용, 중간 중단 없음)
     작업 시간은 근무시간(평일 08~17시, 점심 제외 8h/일, 공휴일 제외)으로 센다
  5. 외주 공정: 자원 제약 없음. 시작일 + (외주 기간 + 지연) 달력일 후 08:00 입고
  6. 포장(PACK) 종료 = 포장완료, 출하(SHIP) 종료 = 출하완료

KPI (사업계획서 정의)
  제조 리드타임   = 포장완료 − 작업지시 확정   (경과 시간, h)
  수주출하 리드타임 = 출하완료 − 수주 확정       (경과 시간, h)
  평균 = Σ(완료 프로젝트의 리드타임) ÷ 완료 프로젝트 수
  기준 시점(as_of) 까지 해당 마일스톤이 끝나지 않은 프로젝트는 '미완료'로 제외하고 건수를 공개한다
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import workcal

HERE = Path(__file__).parent
CAPACITY = {"LASER": 1, "WELD": 2, "BUFF": 1, "INSP": 1, "PACK": 1, "SHIP": 1}
RESOURCE_LABEL = {"LASER": "레이저", "WELD": "제관·용접", "BUFF": "버핑", "INSP": "검사", "PACK": "포장",
                  "SHIP": "출하", "OUTSOURCE": "외주"}


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@dataclass
class Warning_:
    project_id: str
    code: str
    message: str
    severity: str = "WARN"     # WARN(경고) / ERROR(일정 계산 불가)


@dataclass
class Result:
    ops: dict[str, list[dict]] = field(default_factory=dict)        # project_id → 공정 일정
    milestones: dict[str, dict] = field(default_factory=dict)
    warnings: list[Warning_] = field(default_factory=list)
    unschedulable: set[str] = field(default_factory=set)


def _topo(ops: list[dict]) -> list[dict] | None:
    """입력 순서를 최대한 유지하는 위상 정렬. 순환이면 None."""
    names = {o["op"] for o in ops}
    done, order, pending = set(), [], list(ops)
    while pending:
        for o in pending:
            if all(p in done for p in o.get("after", [])):
                order.append(o)
                done.add(o["op"])
                pending.remove(o)
                break
        else:
            return None
    return order if done == names else None


def _cycle_path(ops: list[dict]) -> list[str]:
    after = {o["op"]: o.get("after", []) for o in ops}
    seen, stack = set(), []

    def dfs(n):
        if n in stack:
            return stack[stack.index(n):] + [n]
        if n in seen:
            return None
        seen.add(n)
        stack.append(n)
        for p in after.get(n, []):
            r = dfs(p)
            if r:
                return r
        stack.pop()
        return None
    for n in after:
        r = dfs(n)
        if r:
            return list(reversed(r))
    return []


def validate_project(p: dict) -> list[Warning_]:
    """일정 계산 전 검사. ERROR 가 있으면 그 프로젝트는 일정을 만들지 않는다."""
    w, pid = [], p["project_id"]
    names = [o["op"] for o in p["ops"]]
    if len(set(names)) != len(names):
        w.append(Warning_(pid, "DUPLICATE_OP", "공정 이름 중복", "ERROR"))
    for o in p["ops"]:
        for a in o.get("after", []):
            if a not in names:
                w.append(Warning_(pid, "UNKNOWN_PREDECESSOR", f"{o['op']} 의 선행 공정 {a} 가 없음", "ERROR"))
        res = o["resource"]
        if res != "OUTSOURCE" and CAPACITY.get(res, 0) <= 0:
            w.append(Warning_(pid, "NO_RESOURCE", f"{o['op']}: 자원 {res} 가 없음(능력 0)", "ERROR"))
        if res == "OUTSOURCE" and o.get("days", 0) <= 0:
            w.append(Warning_(pid, "DURATION_INVALID", f"{o['op']}: 외주 기간이 0 이하", "ERROR"))
        if res != "OUTSOURCE" and o.get("hours", 0) <= 0:
            w.append(Warning_(pid, "DURATION_INVALID", f"{o['op']}: 작업 시간이 0 이하", "ERROR"))
    if not any(w_.severity == "ERROR" for w_ in w) and _topo(p["ops"]) is None:
        w.append(Warning_(pid, "PRECEDENCE_CYCLE", "선후 공정 순환: " + " → ".join(_cycle_path(p["ops"])), "ERROR"))
    for m in ("PACK", "SHIP"):
        if m not in names:
            w.append(Warning_(pid, "MISSING_MILESTONE_OP", f"{m} 공정이 없어 KPI 계산 불가", "ERROR"))
    if dt(p["wo_confirmed_at"]) < dt(p["order_confirmed_at"]):
        w.append(Warning_(pid, "WO_BEFORE_ORDER",
                          f"작업지시 확정({p['wo_confirmed_at']})이 수주 확정({p['order_confirmed_at']})보다 빠름 — 입력 확인 필요"))
    return w


def _place(busy: list[tuple[datetime, datetime]], est: datetime, hours: float) -> tuple[datetime, datetime]:
    """한 호기의 바쁜 구간 사이에서 est 이후 가장 이른 시작 시각을 찾는다 (빈틈 끼워넣기)."""
    s = workcal.next_work_moment(est)
    while True:
        e = workcal.add_work_hours(s, hours)
        clash = [b for b in busy if b[0] < e and s < b[1]]
        if not clash:
            return s, e
        s = workcal.next_work_moment(max(b[1] for b in clash))


def critical_path_finish(p: dict, with_delay: bool) -> datetime:
    """자원 경합이 없다고 가정한 최소 완료 시각 (물리적으로 가능한 가장 빠른 출하)."""
    end = {}
    for o in _topo(p["ops"]):
        est = max([dt(p["wo_confirmed_at"])] + [end[a] for a in o.get("after", [])])
        if o["resource"] == "OUTSOURCE":
            end[o["op"]] = workcal.add_calendar_days_delivery(est, o["days"] + (o.get("delay_days", 0) if with_delay else 0))
        else:
            end[o["op"]] = workcal.add_work_hours(workcal.next_work_moment(est), o["hours"])
    return end["SHIP"]


def run(projects: list[dict], with_delay: bool = True) -> Result:
    res = Result()
    units = {r: [[] for _ in range(c)] for r, c in CAPACITY.items()}
    valid = []
    for p in projects:
        ws = validate_project(p)
        res.warnings += ws
        if any(w.severity == "ERROR" for w in ws):
            res.unschedulable.add(p["project_id"])
        else:
            valid.append(p)

    for p in sorted(valid, key=lambda p: (p["due_date"], p["project_id"])):
        pid, end, rows = p["project_id"], {}, []
        release = dt(p["wo_confirmed_at"])
        for o in _topo(p["ops"]):
            est = max([release] + [end[a] for a in o.get("after", [])])
            if o["resource"] == "OUTSOURCE":
                days = o["days"] + (o.get("delay_days", 0) if with_delay else 0)
                s, e, unit = est, workcal.add_calendar_days_delivery(est, days), None
            else:
                best = None
                for i, busy in enumerate(units[o["resource"]]):
                    s, e = _place(busy, est, o["hours"])
                    if best is None or (s, i) < (best[0], best[2]):
                        best = (s, e, i)
                s, e, unit = best
                units[o["resource"]][unit].append((s, e))
            end[o["op"]] = e
            rows.append({"op": o["op"], "resource": o["resource"], "unit": unit, "start": s, "end": e,
                         "ready": est, "wait_h": round((s - est).total_seconds() / 3600, 2),
                         "hours": o.get("hours"), "days": o.get("days"),
                         "delay_days": (o.get("delay_days", 0) if with_delay else 0)})
        res.ops[pid] = rows
        ms = {"order_confirmed_at": dt(p["order_confirmed_at"]), "wo_confirmed_at": release,
              "pack_done": end["PACK"], "ship_done": end["SHIP"]}
        res.milestones[pid] = ms
        due_end = datetime.combine(datetime.fromisoformat(p["due_date"]).date(), datetime.max.time())
        if ms["ship_done"] > due_end:
            late = (ms["ship_done"].date() - datetime.fromisoformat(p["due_date"]).date()).days
            res.warnings.append(Warning_(pid, "DUE_MISS", f"예상 출하 {ms['ship_done']:%Y-%m-%d %H:%M} — 납기 {p['due_date']} 보다 {late}일 늦음"))
            # 원인 구분 (처음엔 둘을 하나로 묶었다가 P05 를 잘못 분류함 — AI_LOG 오류 #1)
            cp_plan = critical_path_finish(p, with_delay=False)
            cp_now = critical_path_finish(p, with_delay)
            if cp_plan > due_end:
                res.warnings.append(Warning_(pid, "DUE_IMPOSSIBLE",
                                             f"지연이 없고 자원을 독점해도 최소 {cp_plan:%Y-%m-%d %H:%M} 출하 — "
                                             f"납기 {p['due_date']} 는 수주 때부터 불가능한 약속"))
            elif cp_now > due_end:
                res.warnings.append(Warning_(pid, "DUE_UNRECOVERABLE",
                                             f"계획상 가능했으나 외주 지연으로 자원을 독점해도 최소 {cp_now:%Y-%m-%d %H:%M} 출하 — "
                                             f"우선순위 조정으로 회복 불가, 고객 협의 필요"))
    return res


# ---------------------------------------------------------------- KPI
def lead_times(res: Result, as_of: datetime) -> list[dict]:
    rows = []
    for pid, m in sorted(res.milestones.items()):
        mfg_done = m["pack_done"] <= as_of
        o2s_done = m["ship_done"] <= as_of
        rows.append({"project_id": pid,
                     "mfg_h": round((m["pack_done"] - m["wo_confirmed_at"]).total_seconds() / 3600, 2) if mfg_done else None,
                     "o2s_h": round((m["ship_done"] - m["order_confirmed_at"]).total_seconds() / 3600, 2) if o2s_done else None,
                     "mfg_done": mfg_done, "o2s_done": o2s_done, **m})
    return rows


def kpi(res: Result, as_of: datetime, n_total: int) -> dict:
    lt = lead_times(res, as_of)
    out = {"as_of": as_of, "n_total": n_total, "n_unschedulable": len(res.unschedulable)}
    for key, label, target in (("mfg_h", "제조 리드타임", (1320, 1080)), ("o2s_h", "수주출하 리드타임", (1440, 1200))):
        vals = [r[key] for r in lt if r[key] is not None]
        excluded = [r["project_id"] for r in lt if r[key] is None]
        out[key] = {"label": label, "n": len(vals), "excluded_incomplete": excluded,
                    "sum_h": round(sum(vals), 2), "mean_h": round(sum(vals) / len(vals), 2) if vals else None,
                    "median_h": (sorted(vals)[len(vals) // 2] if len(vals) % 2 else
                                 (sorted(vals)[len(vals) // 2 - 1] + sorted(vals)[len(vals) // 2]) / 2) if vals else None,
                    "business_plan": {"before": target[0], "target": target[1]},
                    "formula": f"평균 = Σ{label} ÷ 완료 건수 = {round(sum(vals), 2)} ÷ {len(vals)}" if vals else "완료 건 없음"}
    return out


def compare(projects: list[dict], as_of: datetime) -> dict:
    """지연 반영 전(계획) / 후(실제) 일정 비교."""
    plan, act = run(projects, with_delay=False), run(projects, with_delay=True)
    rows = []
    for p in sorted(projects, key=lambda p: (p["due_date"], p["project_id"])):
        pid = p["project_id"]
        a, b = plan.milestones.get(pid), act.milestones.get(pid)
        delays = {o["op"]: o["delay_days"] for o in p["ops"] if o.get("delay_days")}
        rows.append({"project_id": pid, "name": p.get("name", ""), "due_date": p["due_date"], "delays": delays,
                     "plan_ship": a["ship_done"] if a else None, "actual_ship": b["ship_done"] if b else None,
                     "shift_h": round((b["ship_done"] - a["ship_done"]).total_seconds() / 3600, 2) if a and b else None,
                     "plan_late": bool(a and a["ship_done"].date().isoformat() > p["due_date"]),
                     "actual_late": bool(b and b["ship_done"].date().isoformat() > p["due_date"]),
                     "warnings": [w.__dict__ for w in act.warnings if w.project_id == pid]})
    return {"rows": rows, "plan": plan, "actual": act, "kpi_plan": kpi(plan, as_of, len(projects)),
            "kpi_actual": kpi(act, as_of, len(projects))}


def load_projects(path: Path | None = None) -> list[dict]:
    return json.loads((path or HERE / "fixtures" / "projects.json").read_text(encoding="utf-8"))["projects"]
