"""과제 03 명령행.

  python -m task03_bom_revision.cli demo     # out/task03.db 를 새로 만들고 개정 A~D 등록
  python -m task03_bom_revision.cli serve    # http://127.0.0.1:8003 개정 비교 화면 + API
  python -m task03_bom_revision.cli verify   # 자동 검증
  python -m task03_bom_revision.cli import FILE.json   # 추출 JSON 1개 등록 (오류 목록 출력)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import bom, verify
from .api import make_server

HERE = Path(__file__).parent
DEFAULT_DB = HERE.parent / "out" / "task03.db"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task03", description="과제 03 BOM 개정 관리")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("demo", "serve", "import"):
        s = sub.add_parser(name)
        s.add_argument("--db", default=str(DEFAULT_DB))
        if name == "serve":
            s.add_argument("--port", type=int, default=8003)
        if name == "import":
            s.add_argument("file")
    v = sub.add_parser("verify")
    v.add_argument("--out", default=str(HERE / "reports"))
    a = p.parse_args(argv)

    if a.cmd == "verify":
        checks, _ = verify.run(a.out)
        for c in checks:
            print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
        print(f"보고서: {a.out}/verification.md, quantity_verification.md")
        return 0 if all(c.ok for c in checks) else 1

    if a.cmd == "serve":
        server = make_server(a.db, port=a.port)
        print(f"http://127.0.0.1:{a.port}  (DB: {a.db})  Ctrl+C 로 종료")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    if a.cmd == "demo":
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(a.db + suffix).unlink(missing_ok=True)
    conn = bom.connect(a.db)
    bom.init_db(conn)
    files = [HERE / "fixtures" / f"rev_{r}.json" for r in "ABCD"] if a.cmd == "demo" else [Path(a.file)]
    rc = 0
    for f in files:
        try:
            r = bom.register(conn, json.loads(f.read_text(encoding="utf-8")))
            print(f"{'등록' if r['created'] else '동일(변경없음)'}: {r['product_no']} 개정 {r['revision']} "
                  f"({r['effective_date']}) 부품 {r['counts'].get('PART')} / 조립 {r['counts'].get('ASSEMBLY')}")
        except bom.BomInvalid as e:
            rc = 1
            print(f"거부: {f.name} — {e.message}")
            for err in e.errors:
                print(f"  [{err['code']}] {err['where']}: {err['message']}")
        except bom.BomError as e:
            rc = 1
            print(f"거부: {f.name} — [{e.code}] {e.message}")
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
