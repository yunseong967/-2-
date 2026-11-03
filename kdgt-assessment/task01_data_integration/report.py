"""오류 보고서 / 관계 단절 조회."""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from common.csvio import write_rows
from .loader import CATEGORY_OF_CODE


def quarantine_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT source_file, source_row, entity, record_key, reason_codes, reason_detail,
                  raw_json, first_batch_id, resolved_batch_id
           FROM quarantine ORDER BY source_file, source_row"""
    ).fetchall()
    return [dict(r) for r in rows]


def broken_relations(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM v_broken_relations").fetchall()]


def category_of(reason_codes: str) -> str:
    return CATEGORY_OF_CODE.get(reason_codes.split(",")[0], "기타")


def write_error_report(conn: sqlite3.Connection, out_dir: str | Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    rows = quarantine_rows(conn)
    csv_path = out_dir / "error_report.csv"
    write_rows(
        csv_path,
        ["파일", "원본행", "엔터티", "키", "분류", "오류코드", "오류사유", "원본데이터", "최초배치", "해결배치"],
        [{
            "파일": r["source_file"], "원본행": r["source_row"], "엔터티": r["entity"],
            "키": r["record_key"], "분류": category_of(r["reason_codes"]),
            "오류코드": r["reason_codes"], "오류사유": r["reason_detail"],
            "원본데이터": r["raw_json"], "최초배치": r["first_batch_id"],
            "해결배치": r["resolved_batch_id"] or "",
        } for r in rows],
    )

    by_cat = Counter(category_of(r["reason_codes"]) for r in rows)
    by_file = Counter(r["source_file"] for r in rows)
    lines = [
        "# 과제 01 오류 보고서",
        "",
        f"격리된 행: **{len(rows)}행** (전체 목록: `error_report.csv`)",
        "",
        "## 분류별",
        "",
        "| 분류 | 건수 |", "|---|---:|",
        *[f"| {k} | {v} |" for k, v in sorted(by_cat.items())],
        "",
        "## 파일별",
        "",
        "| 파일 | 건수 |", "|---|---:|",
        *[f"| {k} | {v} |" for k, v in sorted(by_file.items())],
        "",
        "## 상세",
        "",
        "| 파일 | 원본행 | 키 | 오류코드 | 사유 |", "|---|---:|---|---|---|",
        *[f"| {r['source_file']} | {r['source_row']} | {r['record_key']} | "
          f"{r['reason_codes']} | {r['reason_detail']} |" for r in rows],
        "",
        "## 관계가 끊긴 행 (v_broken_relations)",
        "",
        "| 파일 | 원본행 | 키 | 참조 LOT | 참조 프로젝트 |", "|---|---:|---|---|---|",
        *[f"| {b['source_file']} | {b['source_row']} | {b['record_key']} | "
          f"{b['ref_lot_no'] or ''} | {b['ref_project_id'] or ''} |" for b in broken_relations(conn)],
        "",
    ]
    md_path = out_dir / "error_report.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path
