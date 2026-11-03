"""과제 06 명령행.

  python -m task06_lot_traceability.cli demo     # 데모 DB 초기화 + 시나리오 적재 (out/task06.db)
  python -m task06_lot_traceability.cli serve    # http://127.0.0.1:8006 추적 화면 + API
  python -m task06_lot_traceability.cli verify   # 시나리오·동시성·수불 대조 자동 검증
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .api import make_server, start_in_background
from .scenario import http_client, run as run_scenario
from . import verify

HERE = Path(__file__).parent
DEFAULT_DB = HERE.parent / "out" / "task06.db"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task06", description="과제 06 LOT 추적")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="데모 DB 를 새로 만들고 시나리오 적재")
    d.add_argument("--db", default=str(DEFAULT_DB))
    s = sub.add_parser("serve", help="API + 추적 화면 실행")
    s.add_argument("--db", default=str(DEFAULT_DB))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8006)
    v = sub.add_parser("verify", help="자동 검증")
    v.add_argument("--out", default=str(HERE / "reports"))
    a = p.parse_args(argv)

    if a.cmd == "demo":
        db = Path(a.db)
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        server, base = start_in_background(str(db))
        try:
            for n, st in enumerate(run_scenario(http_client(base)), 1):
                mark = "OK " if st.result["ok"] else "NG "
                print(f"{mark}{n:>2}. {st.title}  [{st.result['status']}] {st.result['note']}")
        finally:
            server.shutdown()
        print(f"\n데모 DB: {db}\n화면: python -m task06_lot_traceability.cli serve  →  http://127.0.0.1:8006")
        return 0

    if a.cmd == "serve":
        server = make_server(a.db, a.host, a.port)
        print(f"http://{a.host}:{a.port}  (DB: {a.db})  Ctrl+C 로 종료")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    checks, _ = verify.run(a.out)
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
    print(f"보고서: {Path(a.out)}/verification.md, inventory_reconciliation.md")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
