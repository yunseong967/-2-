"""작업 달력: 평일 08:00~17:00, 점심 12:00~13:00 제외 → 하루 8시간. 공휴일 제외.
시각은 모두 한국 시간(KST, 서머타임 없음) 기준의 naive datetime 으로 다룬다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

# 2026년 하반기 공휴일 (대체공휴일 포함). 운영 시에는 회사 달력 파일로 교체.
HOLIDAYS = {
    date(2026, 8, 17),   # 광복절 대체공휴일 (8/15 토)
    date(2026, 9, 24), date(2026, 9, 25),   # 추석 (9/26 토)
    date(2026, 10, 5),   # 개천절 대체공휴일 (10/3 토)
    date(2026, 10, 9),   # 한글날
    date(2026, 12, 25),  # 성탄절
}
SHIFTS = [(time(8), time(12)), (time(13), time(17))]   # 근무 구간
HOURS_PER_DAY = 8


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def next_work_moment(t: datetime) -> datetime:
    """t 이후(포함) 가장 가까운 근무 시각."""
    while True:
        if is_workday(t.date()):
            for a, b in SHIFTS:
                start, end = datetime.combine(t.date(), a), datetime.combine(t.date(), b)
                if t < end:
                    return max(t, start)
        t = datetime.combine(t.date() + timedelta(days=1), time(0))


def add_work_hours(start: datetime, hours: float) -> datetime:
    """start 부터 근무시간 hours 만큼 일한 뒤의 종료 시각. (근무 외 시간은 건너뜀)"""
    t = next_work_moment(start)
    left = timedelta(hours=hours)
    while True:
        for a, b in SHIFTS:
            seg_end = datetime.combine(t.date(), b)
            if datetime.combine(t.date(), a) <= t < seg_end:
                avail = seg_end - t
                if left <= avail:
                    return t + left
                left -= avail
                t = seg_end
        t = next_work_moment(t)


def work_hours_between(a: datetime, b: datetime) -> float:
    """a~b 사이 근무시간 (검증용)."""
    total, t = timedelta(0), a
    while t < b:
        d = t.date()
        if is_workday(d):
            for s, e in SHIFTS:
                lo, hi = max(t, datetime.combine(d, s)), min(b, datetime.combine(d, e))
                if hi > lo:
                    total += hi - lo
        t = datetime.combine(d + timedelta(days=1), time(0))
    return total.total_seconds() / 3600


def add_calendar_days_delivery(start: datetime, days: int) -> datetime:
    """외주: 시작일 + 달력일 days 후 입고. 입고는 그날(또는 다음 근무일) 08:00."""
    return next_work_moment(datetime.combine(start.date() + timedelta(days=days), time(8)))
