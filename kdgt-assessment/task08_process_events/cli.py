"""과제 08 명령행.

  python -m task08_process_events.cli verify                 # 자동 검증 → reports/
  python -m task08_process_events.cli demo                   # out/task08.db 에 전체 시뮬레이션 적재 (시점별 화면 보기용)
  python -m task08_process_events.cli serve --sim            # http://127.0.0.1:8008 현황판 (--sim: 시뮬레이션 시계)
  python -m task08_process_events.cli stream --url http://127.0.0.1:8008 --speed 600   # 실행 중 서버로 실시간 전송 (시연용)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from common.jsonapi import http_client
from . import events as E, simulator as S, verify
from .api import make_server

HERE = Path(__file__).parent
DB = HERE.parent / "out" / "task08.db"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task08")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").add_argument("--out", default=str(HERE / "reports"))
    sub.add_parser("demo").add_argument("--db", default=str(DB))
    s = sub.add_parser("serve")
    s.add_argument("--db", default=str(DB))
    s.add_argument("--sim", action="store_true")
    s.add_argument("--port", type=int, default=8008)
    st = sub.add_parser("stream")
    st.add_argument("--url", default="http://127.0.0.1:8008")
    st.add_argument("--speed", type=float, default=600, help="시뮬레이션 배속 (600 = 실제 1초에 10분)")
    a = p.parse_args(argv)

    if a.cmd == "verify":
        checks, _ = verify.run(Path(a.out))
        for c in checks:
            print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
        print(f"보고서: {a.out}/verification.md, recovery_compare.md, fault_injection_log.csv")
        return 0 if all(c.ok for c in checks) else 1

    events, plan = S.generate_truth()
    deliveries, _ = S.delivery_plan(events)
    if a.cmd == "demo":
        for suf in ("", "-wal", "-shm", "-journal"):
            Path(a.db + suf).unlink(missing_ok=True)
        conn = E.connect(a.db)
        E.set_plan(conn, plan)
        S.run(deliveries, lambda batch, now: E.ingest(conn, batch, now))
        print(f"{a.db}: {conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]}건 적재. "
              f"보기: python -m task08_process_events.cli serve --sim  →  시점 버튼(10:18 / 20:05 / 20:15)")
        return 0
    if a.cmd == "serve":
        server, clock = make_server(a.db, sim=a.sim, port=a.port)
        if a.sim:
            conn = E.connect(a.db)
            clock.sim_now = conn.execute("SELECT MAX(received_at) FROM events").fetchone()[0]
            conn.close()
        print(f"http://127.0.0.1:{a.port}  (DB {a.db}{', 시뮬레이션 시계' if a.sim else ''})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    # stream: 빈 DB 로 띄운 서버(--sim)에 시뮬레이션 시간 비율대로 전송
    call = http_client(a.url)
    call("POST", "/api/plan", plan)
    last = [None]

    def send(batch, now):
        if last[0] is not None:
            gap = (datetime.fromisoformat(now) - datetime.fromisoformat(last[0])).total_seconds() / a.speed
            time.sleep(min(gap, 2.0))
        last[0] = now
        r = call("POST", "/api/events", {"events": batch, "sim_now": now})[1]
        print(f"{now[11:16]} {len(batch):>3}건 → {r}")
    S.run(deliveries, send)
    return 0


if __name__ == "__main__":
    sys.exit(main())
