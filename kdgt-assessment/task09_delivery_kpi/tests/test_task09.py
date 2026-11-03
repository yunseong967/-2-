"""과제 09 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
from datetime import datetime as D

import pytest

from task09_delivery_kpi import schedule as S, verify, workcal as W
from task09_delivery_kpi.generate import project, std_ops


# ---------------------------------------------------------------- 작업 달력
@pytest.mark.parametrize("start, hours, end", [
    (D(2026, 8, 10, 8), 4, D(2026, 8, 10, 12)),        # 점심 전 끝
    (D(2026, 8, 10, 10), 4, D(2026, 8, 10, 15)),       # 점심 1시간 건너뜀
    (D(2026, 8, 14, 13), 8, D(2026, 8, 18, 12)),       # 금 13~17(4h) → 주말 → 8/17 공휴일 → 화 08~12(4h)
    (D(2026, 9, 23, 8), 16, D(2026, 9, 28, 17)),       # 9/23(8h) → 추석·주말 → 9/28(8h)
    (D(2026, 8, 10, 17), 1, D(2026, 8, 11, 9)),        # 17시 시작 = 다음날 08시
    (D(2026, 8, 8, 9), 1, D(2026, 8, 10, 9)),          # 토요일 시작
])
def test_add_work_hours(start, hours, end):
    assert W.add_work_hours(start, hours) == end
    assert W.work_hours_between(W.next_work_moment(start), end) == hours


def test_outsource_arrival_on_holiday_moves_to_next_workday():
    assert W.add_calendar_days_delivery(D(2026, 8, 10, 8), 7) == D(2026, 8, 18, 8)   # 8/17 공휴일


# ---------------------------------------------------------------- 일정 규칙 (작은 손 계산 사례)
def mini(pid, due, laser_h, wo="2026-08-10T08:00"):
    ops = [{"op": "LASER", "resource": "LASER", "hours": laser_h},
           {"op": "PACK", "resource": "PACK", "hours": 4, "after": ["LASER"]},
           {"op": "SHIP", "resource": "SHIP", "hours": 4, "after": ["PACK"]}]
    return project(pid, pid, "2026-08-03T09:00", wo, due, ops)


def test_edd_priority_and_waiting():
    """납기 빠른 B 가 먼저 레이저를 쓰고, A 는 기다린다."""
    r = S.run([mini("A", "2026-09-30", 8), mini("B", "2026-09-01", 8)])
    a = {o["op"]: o for o in r.ops["A"]}
    assert r.ops["B"][0]["start"] == D(2026, 8, 10, 8)
    assert a["LASER"]["start"] == D(2026, 8, 11, 8) and a["LASER"]["wait_h"] == 24


def test_gap_insertion():
    """우선순위 높은 작업 사이 빈틈(8/11)에 낮은 우선순위의 짧은 작업이 들어간다."""
    hi1 = mini("H1", "2026-09-01", 8)
    hi2 = mini("H2", "2026-09-02", 8, wo="2026-08-12T08:00")
    lo = mini("L", "2026-09-30", 4)
    r = S.run([hi1, hi2, lo])
    assert r.ops["L"][0]["start"] == D(2026, 8, 11, 8)


def test_two_welding_teams_run_in_parallel():
    def weld(pid, due):
        ops = [{"op": "WELD", "resource": "WELD", "hours": 16},
               {"op": "PACK", "resource": "PACK", "hours": 1, "after": ["WELD"]},
               {"op": "SHIP", "resource": "SHIP", "hours": 1, "after": ["PACK"]}]
        return project(pid, pid, "2026-08-03T09:00", "2026-08-10T08:00", due, ops)
    r = S.run([weld("A", "2026-09-01"), weld("B", "2026-09-02"), weld("C", "2026-09-03")])
    starts = [r.ops[p][0]["start"] for p in "ABC"]
    assert starts[0] == starts[1] == D(2026, 8, 10, 8) and starts[2] == D(2026, 8, 12, 8)


# ---------------------------------------------------------------- 검증·경고
def test_cycle_is_unschedulable_and_excluded_from_kpi():
    p = project("X", "x", "2026-08-03T09:00", "2026-08-10T08:00", "2026-09-30", std_ops(5, 8, 8, 8, 8))
    next(o for o in p["ops"] if o["op"] == "BUFF")["after"] = ["WELD", "PACK"]
    r = S.run([p, mini("A", "2026-09-30", 8)])
    assert "X" in r.unschedulable and "X" not in r.milestones
    k = S.kpi(r, D(2027, 1, 1), 2)
    assert k["n_unschedulable"] == 1 and k["mfg_h"]["n"] == 1


@pytest.mark.parametrize("mutate, code", [
    (lambda p: p["ops"][1].update(resource="PAINT"), "NO_RESOURCE"),
    (lambda p: p["ops"][2].update(after=["NOPE"]), "UNKNOWN_PREDECESSOR"),
    (lambda p: p["ops"].pop(), "MISSING_MILESTONE_OP"),
    (lambda p: p["ops"][1].update(hours=0), "DURATION_INVALID"),
])
def test_input_errors(mutate, code):
    p = project("X", "x", "2026-08-03T09:00", "2026-08-10T08:00", "2026-09-30", std_ops(5, 8, 8, 8, 8))
    mutate(p)
    assert code in {w.code for w in S.validate_project(p)}


def test_impossible_vs_unrecoverable():
    tight = project("T", "t", "2026-08-03T09:00", "2026-08-10T08:00", "2026-08-20", std_ops(14, 8, 8, 8, 8))
    late = project("L", "l", "2026-08-03T09:00", "2026-08-10T08:00", "2026-09-15", std_ops(14, 8, 8, 8, 8, delay=30))
    codes = {(w.project_id, w.code) for w in S.run([tight, late]).warnings}
    assert ("T", "DUE_IMPOSSIBLE") in codes and ("T", "DUE_UNRECOVERABLE") not in codes
    assert ("L", "DUE_UNRECOVERABLE") in codes and ("L", "DUE_IMPOSSIBLE") not in codes
    assert not S.run([late], with_delay=False).warnings          # 지연 전 계획은 납기 준수


# ---------------------------------------------------------------- KPI
def test_kpi_as_of_boundary_and_formula():
    r = S.run([mini("A", "2026-09-30", 8)])
    pack = r.milestones["A"]["pack_done"]
    assert S.kpi(r, pack, 1)["mfg_h"]["n"] == 1                       # 기준 시점과 같으면 완료
    k = S.kpi(r, pack.replace(minute=pack.minute) - (pack - pack.replace(hour=pack.hour - 1)), 1)
    assert k["mfg_h"]["n"] == 0 and k["mfg_h"]["mean_h"] is None and k["mfg_h"]["excluded_incomplete"] == ["A"]


def test_lead_time_is_elapsed_not_working_hours():
    r = S.run([mini("A", "2026-09-30", 8)])
    lt = S.lead_times(r, D(2027, 1, 1))[0]
    # 작업지시 8/10 08:00, 레이저 8/10 17:00 종료, 포장 4h 8/11 08~12 → 경과 28h (근무시간 12h 가 아님)
    assert lt["mfg_h"] == 28


def test_full_verification_passes():
    assert all(c.ok for c in verify.run(None))
