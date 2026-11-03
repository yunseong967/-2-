"""과제 04 명령행.

  python -m task04_auto_quote.cli verify        # 자동 검증 + 견적 Excel 10개 생성 (reports/quotes/)
  python -m task04_auto_quote.cli answers       # 독립 계산기로 정답(expected.json)·계산서 다시 만들기
  python -m task04_auto_quote.cli demo          # out/task04.db 에 BOM·단가·견적 10건 적재
  python -m task04_auto_quote.cli serve         # http://127.0.0.1:8004 API
  python -m task04_auto_quote.cli show Q04      # 견적 1건과 근거 출력 (demo 후)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from task03_bom_revision import bom as bomlib
from . import quote as Q, reference_calc, verify
from .api import make_server

HERE = Path(__file__).parent
DEFAULT_DB = HERE.parent / "out" / "task04.db"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task04")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").add_argument("--out", default=str(HERE / "reports"))
    sub.add_parser("answers")
    for name in ("demo", "serve", "show"):
        s = sub.add_parser(name)
        s.add_argument("--db", default=str(DEFAULT_DB))
        if name == "serve":
            s.add_argument("--port", type=int, default=8004)
        if name == "show":
            s.add_argument("quote_no")
    a = p.parse_args(argv)

    if a.cmd == "verify":
        checks, _ = verify.run(a.out)
        for c in checks:
            print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
        print(f"보고서: {a.out}/verification.md, answer_comparison.md, quotes/")
        return 0 if all(c.ok for c in checks) else 1
    if a.cmd == "answers":
        reference_calc.main()
        return 0
    if a.cmd == "serve":
        server = make_server(a.db, port=a.port)
        print(f"http://127.0.0.1:{a.port}  (DB: {a.db})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    conn = Q.connect(a.db) if a.cmd == "show" else None
    if a.cmd == "demo":
        for s in ("", "-wal", "-shm", "-journal"):
            Path(a.db + s).unlink(missing_ok=True)
        conn = Q.connect(a.db)
        Q.init_db(conn)
        for r in "ABCD":
            bomlib.register(conn, json.loads((HERE.parent / "task03_bom_revision/fixtures" / f"rev_{r}.json").read_text("utf-8")))
        for f in sorted((HERE / "fixtures/prices").glob("*.json")):
            Q.register_price_version(conn, json.loads(f.read_text("utf-8")))
        for r in json.loads((HERE / "fixtures/quote_requests.json").read_text("utf-8")):
            q = Q.create_quote(conn, r)
            print(f"{q['quote_no']} {q['status']:<12} 공급가액 {q['supply']:>12,}원  합계 {q['total']:>12,}원")
        return 0
    q = Q.get_quote(conn, a.quote_no)
    print(f"{q['quote_no']} {q['product_no']} 개정 {q['revision']} × {q['qty']} ({q['quote_date']}) — {q['status']}")
    print(f"단가 버전: {q['price_versions']}")
    for l in q["lines"]:
        print(f"  [{Q.CATEGORY_LABEL[l['category']]}] {l['item']:<10} {l['formula']}"
              + (f"   ⚠ {l['review_reason']}" if l["status"] == "REVIEW" else ""))
    print(f"재료 {q['material_total']:,} / 가공 {q['process_total']:,} / 외주 {q['outsource_total']:,} → "
          f"공급가액 {q['supply']:,} + 부가세 {q['vat']:,} = {q['total']:,}원")
    return 0


if __name__ == "__main__":
    sys.exit(main())
