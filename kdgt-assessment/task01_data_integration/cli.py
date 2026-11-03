"""과제 01 명령행.

  python -m task01_data_integration.cli generate            # 합성 데이터 + 정답 생성
  python -m task01_data_integration.cli load   [--db ...]   # 적재 (여러 번 실행해도 건수 불변)
  python -m task01_data_integration.cli report [--db ...]   # 오류 보고서 작성
  python -m task01_data_integration.cli broken [--db ...]   # 관계가 끊긴 행 조회
  python -m task01_data_integration.cli verify              # 완료 기준 자동 검증 (실패 시 종료코드 1)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common.db import connect, table_counts
from . import generate_data, verify
from .loader import BUSINESS_TABLES, load_directory
from .report import broken_relations, write_error_report

HERE = Path(__file__).parent
DEFAULT_DATA = HERE / "data" / "sample"
DEFAULT_DB = HERE.parent / "out" / "task01.db"
DEFAULT_REPORTS = HERE / "reports"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task01", description="과제 01 제조 데이터 통합")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="합성 데이터 생성")
    g.add_argument("--out", default=str(DEFAULT_DATA))
    g.add_argument("--seed", type=int, default=20261102)

    for name in ("load", "report", "broken"):
        s = sub.add_parser(name)
        s.add_argument("--db", default=str(DEFAULT_DB))
        if name == "load":
            s.add_argument("--data", default=str(DEFAULT_DATA))
        if name == "report":
            s.add_argument("--out", default=str(DEFAULT_REPORTS))

    v = sub.add_parser("verify", help="완료 기준 자동 검증")
    v.add_argument("--data", default=str(DEFAULT_DATA))
    v.add_argument("--out", default=str(DEFAULT_REPORTS))

    a = p.parse_args(argv)

    if a.cmd == "generate":
        exp = generate_data.generate(a.out, a.seed)
        print(f"생성: {a.out}  정상 {exp['totals']['normal']}행 / 오류 {exp['totals']['error']}행")
        return 0

    if a.cmd == "verify":
        checks = verify.run(a.data, a.out)
        for c in checks:
            print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
        print(f"보고서: {Path(a.out) / 'verification.md'}, {Path(a.out) / 'error_report.md'}")
        return 0 if all(c.ok for c in checks) else 1

    conn = connect(a.db)
    if a.cmd == "load":
        results = load_directory(conn, a.data)
        print(f"{'파일':<18}{'읽음':>6}{'신규':>6}{'변경없음':>8}{'격리':>6}")
        for r in results:
            print(f"{r.file_name:<18}{r.rows_read:>6}{r.inserted:>6}{r.unchanged:>8}{r.quarantined:>6}")
        counts = table_counts(conn, BUSINESS_TABLES + ["quarantine"])
        print("DB 건수:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    elif a.cmd == "report":
        csv_path, md_path = write_error_report(conn, a.out)
        print(f"작성: {csv_path}, {md_path}")
    elif a.cmd == "broken":
        rows = broken_relations(conn)
        print(f"관계가 끊긴 행 {len(rows)}건")
        for r in rows:
            print(f"  {r['source_file']}:{r['source_row']}  {r['record_key']}  "
                  f"LOT={r['ref_lot_no']}  PROJECT={r['ref_project_id']}  ({r['reason_detail']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
