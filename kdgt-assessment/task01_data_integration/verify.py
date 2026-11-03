"""자동 검증: 새 DB에 2회 적재하고 완료 기준을 하나씩 확인한다.

완료 기준 (과제 슬라이드)
  1. 동일 파일 재적재 시 건수 불변
  2. 오류 20행 격리 (정답 expected.json 과 파일·행·오류코드까지 일치)
  3. 관계가 끊긴 행 조회 가능
추가로 정상 100행 적재, 외래키 무결성, 원본 행 번호 보존을 확인한다.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from common.db import connect, table_counts
from .loader import BUSINESS_TABLES, load_directory
from .report import broken_relations, write_error_report

COUNT_TABLES = BUSINESS_TABLES + ["quarantine"]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run(data_dir: str | Path, report_dir: str | Path | None = None) -> list[Check]:
    data_dir = Path(data_dir)
    expected = json.loads((data_dir / "expected.json").read_text(encoding="utf-8"))
    checks: list[Check] = []

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "verify.db")
        first = load_directory(conn, data_dir)
        c1 = table_counts(conn, COUNT_TABLES)
        second = load_directory(conn, data_dir)
        c2 = table_counts(conn, COUNT_TABLES)

        # 1) 재적재 건수 불변
        checks.append(Check(
            "재적재 시 건수 불변", c1 == c2,
            "1차 " + json.dumps(c1, ensure_ascii=False) + " / 2차 " + json.dumps(c2, ensure_ascii=False)))
        ins2 = sum(r.inserted for r in second)
        unch2 = sum(r.unchanged for r in second)
        checks.append(Check(
            "2차 적재는 신규 0건, 전부 '변경없음'", ins2 == 0 and unch2 == sum(r.inserted for r in first),
            f"2차 신규 {ins2}건, 변경없음 {unch2}건"))

        # 2) 정상 100행 적재
        normal_expected = expected["totals"]["normal"]
        normal_loaded = sum(c1[t] for t in BUSINESS_TABLES)
        per_file_ok = all(
            r.inserted == expected["normal_rows"][r.file_name] for r in first)
        checks.append(Check(
            f"정상 {normal_expected}행 적재 (파일별 일치)", normal_loaded == normal_expected and per_file_ok,
            ", ".join(f"{r.file_name}={r.inserted}/{expected['normal_rows'][r.file_name]}" for r in first)))

        # 3) 오류 20행 격리 + 정답 대조
        got = {(r["source_file"], r["source_row"]): r["reason_codes"].split(",")
               for r in map(dict, conn.execute(
                   "SELECT source_file, source_row, reason_codes FROM quarantine"))}
        exp = {(e["file"], e["source_row"]): e["expected_code"] for e in expected["error_rows"]}
        missing = [k for k in exp if k not in got]
        extra = [k for k in got if k not in exp]
        wrong = [(k, exp[k], got[k]) for k in exp if k in got and exp[k] not in got[k]]
        checks.append(Check(
            f"오류 {len(exp)}행 격리 (파일·행·코드 정답 일치)",
            len(got) == len(exp) and not missing and not extra and not wrong,
            f"격리 {len(got)}행, 누락 {missing}, 초과 {extra}, 코드불일치 {wrong}"))

        # 4) 관계 단절 조회
        broken = broken_relations(conn)
        exp_broken = sum(1 for e in expected["error_rows"] if e["expected_code"] == "UNKNOWN_LOT")
        checks.append(Check(
            "관계가 끊긴 행 조회 (v_broken_relations)", len(broken) == exp_broken,
            f"{len(broken)}행 조회됨 (기대 {exp_broken}) : "
            + ", ".join(f"{b['source_file']}:{b['source_row']}→{b['ref_lot_no']}" for b in broken)))

        # 5) 외래키 무결성
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        checks.append(Check("외래키 무결성 (PRAGMA foreign_key_check)", not fk, f"위반 {len(fk)}건"))

        # 6) 원본 행 번호 보존: 적재된 모든 행에 source_row 가 있고 파일 안에서 유일
        dup_rows = 0
        for t in BUSINESS_TABLES:
            dup_rows += conn.execute(
                f"SELECT COUNT(*) FROM (SELECT source_file, source_row FROM {t} "
                f"GROUP BY 1,2 HAVING COUNT(*) > 1)").fetchone()[0]
        null_rows = sum(conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE source_row IS NULL").fetchone()[0] for t in BUSINESS_TABLES)
        checks.append(Check("원본 행 번호 보존", dup_rows == 0 and null_rows == 0,
                            f"누락 {null_rows}, 중복 {dup_rows}"))

        if report_dir:
            write_error_report(conn, report_dir)
            _write_md(Path(report_dir) / "verification.md", data_dir, first, second, c1, c2, checks)
        conn.close()
    return checks


def _rel(p) -> str:
    """보고서에 개인 PC 절대경로가 남지 않도록 프로젝트 기준 상대경로로 표시."""
    root = Path(__file__).resolve().parents[1]
    try:
        return Path(p).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(p).name


def _write_md(path, data_dir, first, second, c1, c2, checks):
    def table(results):
        out = ["| 파일 | 읽은 행 | 신규 | 변경없음 | 격리 |", "|---|---:|---:|---:|---:|"]
        out += [f"| {r.file_name} | {r.rows_read} | {r.inserted} | {r.unchanged} | {r.quarantined} |"
                for r in results]
        return out

    lines = [
        "# 과제 01 자동 검증 결과",
        "",
        f"- 입력: `{_rel(data_dir)}` (+ 정답 `expected.json`)",
        "- 방법: 빈 DB에 같은 파일을 2회 적재한 뒤 아래 항목을 확인",
        "- 재실행: `python -m task01_data_integration.cli verify`",
        "",
        "## 판정",
        "",
        "| 결과 | 항목 | 근거 |", "|---|---|---|",
        *[f"| {'PASS' if c.ok else 'FAIL'} | {c.name} | {c.detail} |" for c in checks],
        "",
        "## 1차 적재",
        "",
        *table(first),
        "",
        "## 2차 적재 (동일 파일)",
        "",
        *table(second),
        "",
        "## 테이블 건수",
        "",
        "| 테이블 | 1차 후 | 2차 후 |", "|---|---:|---:|",
        *[f"| {t} | {c1[t]} | {c2[t]} |" for t in c1],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
