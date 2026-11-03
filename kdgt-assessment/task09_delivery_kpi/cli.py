"""과제 09 명령행.

  python -m task09_delivery_kpi.cli verify     # 수작업 정답 5건 대조 + 일정 규칙 검사 → reports/
  python -m task09_delivery_kpi.cli kpi        # KPI 와 경고 출력 (--as-of 2026-11-03T00:00)
  python -m task09_delivery_kpi.cli serve      # http://127.0.0.1:8009 일정 비교 화면
  python -m task09_delivery_kpi.cli generate   # 프로젝트 20건 재생성 (seed 고정)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import generate, schedule as S, verify
from .api import make_server

HERE = Path(__file__).parent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task09")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").add_argument("--out", default=str(HERE / "reports"))
    sub.add_parser("kpi").add_argument("--as-of", default="2026-11-03T00:00")
    sub.add_parser("serve").add_argument("--port", type=int, default=8009)
    sub.add_parser("generate")
    a = p.parse_args(argv)
    if a.cmd == "generate":
        generate.main()
        return 0
    if a.cmd == "serve":
        server = make_server(port=a.port)
        print(f"http://127.0.0.1:{a.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if a.cmd == "kpi":
        ps = S.load_projects()
        res = S.run(ps)
        k = S.kpi(res, datetime.fromisoformat(a.as_of), len(ps))
        for key in ("mfg_h", "o2s_h"):
            v = k[key]
            print(f"{v['label']}: 평균 {v['mean_h']}h (완료 {v['n']}건, 미완료 제외 {len(v['excluded_incomplete'])}건) — {v['formula']}")
        print(f"일정 계산 불가 {k['n_unschedulable']}건: {sorted(res.unschedulable)}")
        for w in res.warnings:
            print(f"  [{w.code}] {w.project_id}: {w.message}")
        return 0
    checks = verify.run(Path(a.out))
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
    print(f"보고서: {a.out}/verification.md, kpi_report.md, hand_comparison.md")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
