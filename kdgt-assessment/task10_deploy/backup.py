"""SQLite 온라인 백업·복원과 건수·합계 대조.

backup : sqlite3 backup API 로 운영 중에도 일관된 사본 생성 → 파일 SHA-256 + 건수·합계 manifest 저장
restore: ① 백업 파일 해시 확인 ② PRAGMA integrity_check ③ 백업의 건수·합계 = manifest 확인
         ④ 유지보수 표시(MAINTENANCE 파일)를 켠 상태에서 임시 파일로 복사 후 os.replace(원자적 교체) ⑤ 교체 후 다시 대조
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# 과제 06 DB 의 '건수·합계' 기준 (복원 검증에 사용)
STAT_QUERIES = {
    "lots.count": "SELECT COUNT(*) FROM lots",
    "lots.qty_m_sum": "SELECT COALESCE(SUM(qty_m),0) FROM lots",
    "shipments.count": "SELECT COUNT(*) FROM shipments",
    "shipments.qty_m_sum": "SELECT COALESCE(SUM(qty_m),0) FROM shipments",
    "inspections.count": "SELECT COUNT(*) FROM inspections",
    "ledger.count": "SELECT COUNT(*) FROM ledger",
    "ledger.qty_delta_m_sum": "SELECT COALESCE(SUM(qty_delta_m),0) FROM ledger",
    "events.count": "SELECT COUNT(*) FROM events",
    "lot_links.count": "SELECT COUNT(*) FROM lot_links",
}


class RestoreError(Exception):
    pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stats(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        return {k: conn.execute(q).fetchone()[0] for k, q in STAT_QUERIES.items()}
    finally:
        conn.close()


def integrity(db: Path) -> str:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def backup(db: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = out_dir / f"lot-{stamp}.db"
    src = sqlite3.connect(str(db), timeout=10)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)                 # 쓰기 중에도 일관된 스냅숏
    finally:
        dst.close()
        src.close()
    manifest = {"file": target.name, "created_at": stamp, "sha256": sha256_file(target),
                "integrity": integrity(target), "stats": stats(target)}
    (out_dir / f"{target.name}.manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def restore(backup_file: Path, db: Path, maintenance_flag: Path) -> dict:
    mf = json.loads(Path(str(backup_file) + ".manifest.json").read_text(encoding="utf-8"))
    if sha256_file(backup_file) != mf["sha256"]:
        raise RestoreError("백업 파일 해시가 manifest 와 다름 — 손상된 백업")
    if integrity(backup_file) != "ok":
        raise RestoreError("백업 파일 integrity_check 실패")
    if stats(backup_file) != mf["stats"]:
        raise RestoreError("백업 파일 건수·합계가 manifest 와 다름")
    maintenance_flag.write_text("restore in progress", encoding="utf-8")
    try:
        tmp = db.with_name(db.name + ".restoring")
        shutil.copyfile(backup_file, tmp)
        for suffix in ("-wal", "-shm"):                 # 손상된 DB 의 WAL 이 복원본에 섞이지 않게
            Path(str(db) + suffix).unlink(missing_ok=True)
        os.replace(tmp, db)
        after = stats(db)
        if after != mf["stats"] or integrity(db) != "ok":
            raise RestoreError(f"복원 후 대조 실패: {after} != {mf['stats']}")
    finally:
        maintenance_flag.unlink(missing_ok=True)
    return {"restored_from": backup_file.name, "stats": after, "expected": mf["stats"], "match": after == mf["stats"]}


def latest_backup(out_dir: Path) -> Path | None:
    files = sorted(out_dir.glob("lot-*.db"))
    return files[-1] if files else None
