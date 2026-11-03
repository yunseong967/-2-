"""과제 09 프로젝트 20건 생성.

P01~P05: 손으로 일정을 추적해 정답을 만든 프로젝트 (fixtures/hand_answers.md). 납기를 가장 빠르게 두어
         EDD 우선순위상 다른 15건보다 먼저 배정되므로, 다른 프로젝트의 영향을 받지 않는다.
  P01 기본 (경합·지연 없음)
  P02 외주 5일 지연 → 계획 대비 출하 변화
  P03 P01·P02 와 레이저 경합(대기), 외주 입고일이 공휴일(8/17) → 다음 근무일
  P04 납기 9/10 — 자원 경합이 없어도 맞출 수 없는 불가능 일정
  P05 외주 60일 지연 → 기준일(11/3)까지 미완료 → KPI 제외
P06~P20: seed 고정 무작위. 외주 지연, 추가 외주(RT 검사)로 선후 관계 변형, 순환 오류 1건, 작업지시가 수주보다 빠른 입력 오류 1건 포함.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent


def std_ops(out_days, laser_h, weld_h, buff_h, insp_h, delay=0, rt_days=None):
    ops = [
        {"op": "OUT", "name": "외주 소재가공(경판 성형)", "resource": "OUTSOURCE", "days": out_days, "delay_days": delay},
        {"op": "LASER", "name": "레이저 절단", "resource": "LASER", "hours": laser_h},
        {"op": "WELD", "name": "제관·용접", "resource": "WELD", "hours": weld_h, "after": ["OUT", "LASER"]},
        {"op": "BUFF", "name": "버핑", "resource": "BUFF", "hours": buff_h, "after": ["WELD"]},
        {"op": "INSP", "name": "검사", "resource": "INSP", "hours": insp_h, "after": ["BUFF"]},
        {"op": "PACK", "name": "포장", "resource": "PACK", "hours": 8, "after": ["INSP"]},
        {"op": "SHIP", "name": "출하", "resource": "SHIP", "hours": 4, "after": ["PACK"]},
    ]
    if rt_days:
        ops.insert(3, {"op": "RT", "name": "외주 방사선투과검사", "resource": "OUTSOURCE", "days": rt_days,
                       "delay_days": 0, "after": ["WELD"]})
        ops[5]["after"] = ["BUFF", "RT"]          # INSP 는 BUFF 와 RT 둘 다 끝나야
    return ops


def project(pid, name, order, wo, due, ops):
    return {"project_id": pid, "name": name, "order_confirmed_at": order, "wo_confirmed_at": wo,
            "due_date": due, "ops": ops}


def generate(seed: int = 9) -> list[dict]:
    ps = [
        project("P01", "반응기 500L (기본)", "2026-08-03T09:00", "2026-08-10T08:00", "2026-09-30", std_ops(14, 8, 40, 16, 8)),
        project("P02", "교반기 300L (외주 지연)", "2026-08-05T10:00", "2026-08-12T08:00", "2026-09-30", std_ops(14, 8, 24, 8, 8, delay=5)),
        project("P03", "건조기 300L (레이저 경합)", "2026-08-04T14:00", "2026-08-10T08:00", "2026-09-30", std_ops(7, 16, 40, 16, 8)),
        project("P04", "반응기 200L (불가능 납기)", "2026-08-18T09:00", "2026-08-19T08:00", "2026-09-10", std_ops(21, 8, 16, 8, 8)),
        project("P05", "PSG 500L (외주 장기 지연)", "2026-08-20T09:00", "2026-08-24T08:00", "2026-10-15", std_ops(20, 8, 40, 16, 8, delay=60)),
    ]
    rng = random.Random(seed)
    names = ["반응기", "교반기", "건조기", "PSG"]
    for i in range(6, 21):
        order = datetime(2026, 8, 20, 9) + timedelta(days=rng.randint(0, 60), hours=rng.choice([0, 2, 5]))
        wo = order + timedelta(days=rng.randint(3, 10))
        wo = datetime.combine(wo.date(), datetime.min.time()).replace(hour=8)
        due = max(date(2026, 10, 16), (order + timedelta(days=rng.randint(70, 95))).date())
        delay = rng.choice([0, 0, 0, 3, 7, 15]) if i != 7 else 10
        ops = std_ops(rng.randint(14, 25), rng.choice([8, 16, 24]), rng.choice([40, 80, 120, 160]),
                      rng.choice([16, 24, 40]), rng.choice([8, 16]), delay=delay,
                      rt_days=rng.choice([5, 7]) if i in (9, 13, 16) else None)
        name = f"{rng.choice(names)} {rng.choice([200, 300, 500, 1000, 2000])}L"
        p = project(f"P{i:02d}", name, order.isoformat(timespec="minutes"), wo.isoformat(timespec="minutes"),
                    due.isoformat(), ops)
        if i == 18:                     # 순환: BUFF 가 INSP 뒤, INSP 가 BUFF 뒤
            p["name"] += " (공정 순환 오류)"
            next(o for o in ops if o["op"] == "BUFF")["after"] = ["WELD", "INSP"]
        if i == 19:                     # 입력 오류: 작업지시가 수주 확정보다 이틀 빠름
            p["name"] += " (작업지시 일자 오류)"
            p["wo_confirmed_at"] = (order - timedelta(days=2)).replace(hour=8, minute=0).isoformat(timespec="minutes")
        ps.append(p)
    return ps


def main():
    ps = generate()
    out = {"_설명": "프로젝트 20건. P01~P05 는 손 계산 정답 대상 (hand_answers.md). 시각은 KST.",
           "as_of": "2026-11-03T00:00", "projects": ps}
    (HERE / "fixtures" / "projects.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"프로젝트 {len(ps)}건 → fixtures/projects.json")


if __name__ == "__main__":
    main()
