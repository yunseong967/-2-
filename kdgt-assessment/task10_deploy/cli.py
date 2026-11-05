"""과제 10 운영 명령. 컨테이너 안에서는 `docker exec <컨테이너> python -m task10_deploy.cli <명령>`.

  serve                         서비스 실행 (환경변수: KDGT_DATA_DIR, KDGT_LLM_TIMEOUT, KDGT_LLM_BACKEND, ANTHROPIC_API_KEY)
  keys add --name 이름 --role admin|viewer   키 발급 (원문은 이때 한 번만 출력)
  keys list | keys revoke KEY_ID
  backup                        온라인 백업 + manifest(건수·합계)
  restore [--file 백업파일]      백업 검증 후 원자적 복원 (기본: 최신 백업)
  stats                         현재 DB 건수·합계
  db-lock --seconds N           [장애 시나리오] DB 를 N초간 배타 잠금 (DB 중단 재현)
  corrupt                       [장애 시나리오] DB 파일 앞부분 훼손 (디스크 손상 재현)
  slow-llm --port P --delay S   [장애 시나리오] 응답하지 않는 가짜 LLM 서버
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from . import backup as B
from .app import Config, make_server
from .security import KeyStore


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task10")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=int(os.environ.get("KDGT_PORT", "8010")))
    k = sub.add_parser("keys")
    ks = k.add_subparsers(dest="kcmd", required=True)
    ka = ks.add_parser("add")
    ka.add_argument("--name", required=True)
    ka.add_argument("--role", required=True, choices=["admin", "viewer"])
    ks.add_parser("list")
    ks.add_parser("revoke").add_argument("key_id")
    sub.add_parser("backup")
    sub.add_parser("restore").add_argument("--file")
    sub.add_parser("stats")
    sub.add_parser("db-lock").add_argument("--seconds", type=float, default=15)
    sub.add_parser("corrupt")
    sl = sub.add_parser("slow-llm")
    sl.add_argument("--port", type=int, default=9999)
    sl.add_argument("--delay", type=float, default=60)
    a = p.parse_args(argv)
    cfg = Config.from_env()

    if a.cmd == "serve":
        server, app = make_server(cfg, a.host, a.port)
        print(json.dumps({"event": "started", "port": a.port, "data_dir": str(cfg.data_dir),
                          "llm_backend": cfg.llm_backend, "llm_timeout_s": cfg.llm_timeout,
                          "anthropic_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY"))}, ensure_ascii=False), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if a.cmd == "keys":
        store = KeyStore(cfg.keys)
        if a.kcmd == "add":
            key_id, key = store.add(a.name, a.role)
            print(json.dumps({"key_id": key_id, "role": a.role, "api_key": key,
                              "note": "이 키는 지금 한 번만 표시됩니다. 서버에는 해시만 저장됩니다."}, ensure_ascii=False))
        elif a.kcmd == "list":
            print(json.dumps(store.list(), ensure_ascii=False, indent=1))
        else:
            print(json.dumps({"revoked": store.revoke(a.key_id)}))
        return 0
    if a.cmd == "backup":
        print(json.dumps(B.backup(cfg.db, cfg.backups), ensure_ascii=False))
        return 0
    if a.cmd == "restore":
        f = Path(a.file) if a.file else B.latest_backup(cfg.backups)
        if f is None:
            print("백업 파일이 없습니다", file=sys.stderr)
            return 1
        f = f if f.is_absolute() else cfg.backups / f.name
        try:
            print(json.dumps(B.restore(f, cfg.db, cfg.maintenance), ensure_ascii=False))
        except B.RestoreError as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 1
        return 0
    if a.cmd == "stats":
        try:
            print(json.dumps({"stats": B.stats(cfg.db), "integrity": B.integrity(cfg.db)}, ensure_ascii=False))
        except sqlite3.DatabaseError as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
            return 1
        return 0
    if a.cmd == "db-lock":
        conn = sqlite3.connect(str(cfg.db), isolation_level=None)
        conn.execute("BEGIN EXCLUSIVE")
        print(json.dumps({"locked": True, "seconds": a.seconds}), flush=True)
        time.sleep(a.seconds)
        conn.execute("ROLLBACK")
        conn.close()
        print(json.dumps({"locked": False}), flush=True)
        return 0
    if a.cmd == "corrupt":
        with open(cfg.db, "r+b") as f:
            f.write(os.urandom(4096))
        for suf in ("-wal", "-shm"):
            Path(str(cfg.db) + suf).unlink(missing_ok=True)
        print(json.dumps({"corrupted": str(cfg.db)}))
        return 0
    if a.cmd == "slow-llm":
        from .slow_llm import make
        srv = make(a.delay, a.port)
        print(json.dumps({"slow_llm": a.port, "delay_s": a.delay}), flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
