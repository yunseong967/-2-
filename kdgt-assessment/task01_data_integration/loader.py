"""과제 01 적재기.

처리 순서: 수주(projects) → 자재 LOT(material_lots) → 입고 → 검사 → 출하.
앞 단계에서 정상 적재된 행만 뒤 단계의 참조 대상이 된다.

행 단위 판정 결과는 셋 중 하나다.
  - INSERTED   : 새로 저장
  - UNCHANGED  : 같은 키·같은 내용이 이미 DB에 있음 (재적재 → 아무것도 하지 않음)
  - QUARANTINE : 오류. 원본 행 번호와 사유를 quarantine 에 보존 (같은 행은 1회만)

배치 전체가 하나의 트랜잭션이다. 중간에 예외가 나면 아무것도 저장되지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from common import csvio
from common.dates import parse_iso_date
from common.db import apply_schema

SCHEMA = Path(__file__).with_name("schema.sql")


# ---------------------------------------------------------------- 오류 코드
class Code:
    MISSING_FIELD = "MISSING_FIELD"        # 필수값 누락
    DUPLICATE_IN_FILE = "DUPLICATE_IN_FILE"  # 같은 파일 안에서 키 중복 (뒤에 나온 행을 격리)
    KEY_CONFLICT = "KEY_CONFLICT"          # DB에 같은 키가 다른 내용으로 이미 있음
    UNKNOWN_LOT = "UNKNOWN_LOT"            # 자재 LOT 등록부에 없는 LOT
    UNKNOWN_PROJECT = "UNKNOWN_PROJECT"    # 수주에 없는 프로젝트
    NO_RECEIPT = "NO_RECEIPT"              # 등록된 LOT 이지만 정상 입고 기록 없이 검사됨
    QTY_NEGATIVE = "QTY_NEGATIVE"          # 수량 < 0
    QTY_ZERO = "QTY_ZERO"                  # 수량 = 0
    QTY_INVALID = "QTY_INVALID"            # 숫자가 아님 / 정수여야 하는데 소수
    DATE_INVALID = "DATE_INVALID"          # 형식 오류 또는 존재하지 않는 날짜
    DATE_SEQUENCE = "DATE_SEQUENCE"        # 날짜 선후 관계 위반
    VALUE_INVALID = "VALUE_INVALID"        # 허용값 목록 밖


CATEGORY_OF_CODE = {
    Code.DUPLICATE_IN_FILE: "중복", Code.KEY_CONFLICT: "중복",
    Code.UNKNOWN_LOT: "미등록 LOT", Code.UNKNOWN_PROJECT: "미등록 프로젝트",
    Code.NO_RECEIPT: "입고 없는 검사",
    Code.QTY_NEGATIVE: "음수수량", Code.QTY_ZERO: "수량오류", Code.QTY_INVALID: "수량오류",
    Code.DATE_INVALID: "날짜오류", Code.DATE_SEQUENCE: "날짜오류",
    Code.MISSING_FIELD: "필수값 누락", Code.VALUE_INVALID: "허용값 오류",
}


@dataclass
class Issue:
    code: str
    detail: str


@dataclass
class EntitySpec:
    table: str
    file_name: str
    key: str
    columns: list[str]                       # CSV 업무 컬럼 = DB 업무 컬럼
    dates: list[str] = field(default_factory=list)
    qty: dict[str, type] = field(default_factory=dict)   # 컬럼 → int | float
    enums: dict[str, set[str]] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)   # 컬럼 → 'lot' | 'project'
    # 행 하나와 DB 커서를 받아 선후 관계 등 추가 검사를 하는 함수
    extra_check: Callable[[dict, sqlite3.Connection], list[Issue]] | None = None


# ---------------------------------------------------------------- 엔터티별 추가 검사
def _check_project_dates(rec: dict, conn: sqlite3.Connection) -> list[Issue]:
    o, d = rec.get("_order_date"), rec.get("_due_date")
    if o and d and d < o:
        return [Issue(Code.DATE_SEQUENCE, f"납기일({d})이 수주일({o})보다 빠름")]
    return []


def _check_inspection_after_receipt(rec: dict, conn: sqlite3.Connection) -> list[Issue]:
    when: date | None = rec.get("_inspected_date")
    if when is None:
        return []
    row = conn.execute(
        "SELECT MIN(received_date) FROM receipts WHERE lot_no = ?", (rec["lot_no"],)
    ).fetchone()
    first = row[0]
    if first is None:
        # 등록된 LOT 이지만 정상 입고 기록이 없음 → 날짜 문제가 아니라 관계(검사→입고) 단절
        return [Issue(Code.NO_RECEIPT, f"LOT {rec['lot_no']} 의 정상 입고 기록 없이 검사됨")]
    if when.isoformat() < first:
        return [Issue(Code.DATE_SEQUENCE, f"검사일({when})이 최초 입고일({first})보다 빠름")]
    return []


def _check_shipment(rec: dict, conn: sqlite3.Connection) -> list[Issue]:
    issues = []
    when: date | None = rec.get("_shipped_date")
    proj = conn.execute(
        "SELECT order_date FROM projects WHERE project_id = ?", (rec["project_id"],)
    ).fetchone()
    if when is not None and proj is not None and when.isoformat() < proj[0]:
        issues.append(Issue(Code.DATE_SEQUENCE, f"출하일({when})이 수주일({proj[0]})보다 빠름"))
    lot = conn.execute(
        "SELECT project_id FROM material_lots WHERE lot_no = ?", (rec["lot_no"],)
    ).fetchone()
    if lot is not None and proj is not None and lot[0] != rec["project_id"]:
        issues.append(Issue(
            Code.VALUE_INVALID,
            f"LOT {rec['lot_no']} 은(는) 프로젝트 {lot[0]} 소속인데 {rec['project_id']} 로 출하됨",
        ))
    return issues


SPECS: list[EntitySpec] = [
    EntitySpec(
        table="projects", file_name="orders.csv", key="project_id",
        columns=["project_id", "customer", "product", "order_date", "due_date", "order_qty"],
        dates=["order_date", "due_date"], qty={"order_qty": int},
        extra_check=_check_project_dates,
    ),
    EntitySpec(
        table="material_lots", file_name="materials.csv", key="lot_no",
        columns=["lot_no", "project_id", "material_code", "material", "thickness_mm", "unit"],
        qty={"thickness_mm": float}, enums={"unit": {"KG", "EA"}},
        refs={"project_id": "project"},
    ),
    EntitySpec(
        table="receipts", file_name="receipts.csv", key="receipt_id",
        columns=["receipt_id", "lot_no", "supplier", "received_date", "qty"],
        dates=["received_date"], qty={"qty": float}, refs={"lot_no": "lot"},
    ),
    EntitySpec(
        table="inspections", file_name="inspections.csv", key="inspection_id",
        columns=["inspection_id", "lot_no", "inspected_date", "result", "inspected_qty"],
        dates=["inspected_date"], qty={"inspected_qty": float},
        enums={"result": {"PASS", "FAIL"}}, refs={"lot_no": "lot"},
        extra_check=_check_inspection_after_receipt,
    ),
    EntitySpec(
        table="shipments", file_name="shipments.csv", key="shipment_id",
        columns=["shipment_id", "project_id", "lot_no", "shipped_date", "qty"],
        dates=["shipped_date"], qty={"qty": int},
        refs={"project_id": "project", "lot_no": "lot"},
        extra_check=_check_shipment,
    ),
]
BUSINESS_TABLES = [s.table for s in SPECS]
REF_LOOKUP = {
    "lot": ("material_lots", "lot_no", Code.UNKNOWN_LOT, "자재 LOT 등록부에 없는 LOT"),
    "project": ("projects", "project_id", Code.UNKNOWN_PROJECT, "수주에 없는 프로젝트"),
}


# ---------------------------------------------------------------- 행 검증
def validate(spec: EntitySpec, rec: dict, conn: sqlite3.Connection) -> list[Issue]:
    issues: list[Issue] = []

    missing = [c for c in spec.columns if not rec.get(c)]
    if missing:
        issues.append(Issue(Code.MISSING_FIELD, f"필수값 누락: {', '.join(missing)}"))

    for col in spec.dates:
        if not rec.get(col):
            continue
        try:
            rec[f"_{col}"] = parse_iso_date(rec[col])
        except ValueError as e:
            issues.append(Issue(Code.DATE_INVALID, f"{col}: {e}"))

    for col, typ in spec.qty.items():
        raw = rec.get(col, "")
        if not raw:
            continue
        try:
            num = float(raw)
            if typ is int and not num.is_integer():
                raise ValueError
        except ValueError:
            issues.append(Issue(Code.QTY_INVALID, f"{col}: 숫자가 아님 또는 정수가 아님 ({raw!r})"))
            continue
        if num < 0:
            issues.append(Issue(Code.QTY_NEGATIVE, f"{col}: 음수 ({raw})"))
        elif num == 0:
            issues.append(Issue(Code.QTY_ZERO, f"{col}: 0"))

    for col, allowed in spec.enums.items():
        if rec.get(col) and rec[col] not in allowed:
            issues.append(Issue(Code.VALUE_INVALID, f"{col}: {rec[col]!r} (허용: {sorted(allowed)})"))

    ref_ok = True
    for col, kind in spec.refs.items():
        if not rec.get(col):
            continue
        table, pk, code, msg = REF_LOOKUP[kind]
        if conn.execute(f"SELECT 1 FROM {table} WHERE {pk} = ?", (rec[col],)).fetchone() is None:
            issues.append(Issue(code, f"{msg}: {rec[col]}"))
            ref_ok = False

    # 참조가 살아 있고 기본 검사를 통과한 경우에만 교차 검사 (원인 중복 보고 방지)
    if spec.extra_check and ref_ok and not any(i.code == Code.DATE_INVALID for i in issues):
        issues.extend(spec.extra_check(rec, conn))
    return issues


# ---------------------------------------------------------------- 적재
@dataclass
class FileResult:
    file_name: str
    rows_read: int = 0
    inserted: int = 0
    unchanged: int = 0
    quarantined: int = 0


def _quarantine(conn, batch_id, spec, file_name, line_no, rec, h, issues) -> None:
    raw = {c: rec.get(c, "") for c in rec if not c.startswith("_")}
    conn.execute(
        """INSERT OR IGNORE INTO quarantine
           (first_batch_id, entity, source_file, source_row, record_key,
            reason_codes, reason_detail, raw_json, row_hash)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (batch_id, spec.table, file_name, line_no, rec.get(spec.key),
         ",".join(dict.fromkeys(i.code for i in issues)),
         " / ".join(i.detail for i in issues),
         json.dumps(raw, ensure_ascii=False), h),
    )


def _load_file(conn, batch_id, spec: EntitySpec, path: Path) -> FileResult:
    res = FileResult(spec.file_name)
    rows = csvio.read_rows(path)
    res.rows_read = len(rows)
    accepted_keys: dict[str, int] = {}   # 이 파일에서 이미 받아들인 키 → 줄 번호

    for line_no, rec in rows:
        h = csvio.row_hash(rec, spec.columns)
        key = rec.get(spec.key, "")
        issues = validate(spec, rec, conn)

        if key and key in accepted_keys:
            issues.insert(0, Issue(Code.DUPLICATE_IN_FILE,
                                   f"{spec.key}={key} 가 {accepted_keys[key]}행과 중복"))
        elif key and not issues:
            existing = conn.execute(
                f"SELECT row_hash, source_file, source_row FROM {spec.table} WHERE {spec.key} = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["row_hash"] == h:
                    accepted_keys[key] = line_no
                    res.unchanged += 1
                    continue
                issues.append(Issue(
                    Code.KEY_CONFLICT,
                    f"{spec.key}={key} 가 이미 다른 내용으로 적재됨 "
                    f"({existing['source_file']} {existing['source_row']}행)",
                ))

        if issues:
            _quarantine(conn, batch_id, spec, spec.file_name, line_no, rec, h, issues)
            res.quarantined += 1
            continue

        cols = spec.columns + ["source_file", "source_row", "row_hash", "batch_id"]
        vals = [rec[c] for c in spec.columns] + [spec.file_name, line_no, h, batch_id]
        conn.execute(
            f"INSERT INTO {spec.table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            vals,
        )
        # 이전 배치에서 같은 키로 격리됐던 행(중복 제외)은 '해결됨'으로 표시
        conn.execute(
            """UPDATE quarantine SET resolved_batch_id = ?
               WHERE entity = ? AND record_key = ? AND resolved_batch_id IS NULL
                 AND reason_codes NOT LIKE '%DUPLICATE_IN_FILE%'""",
            (batch_id, spec.table, key),
        )
        accepted_keys[key] = line_no
        res.inserted += 1
    return res


def load_directory(conn: sqlite3.Connection, data_dir: str | Path) -> list[FileResult]:
    data_dir = Path(data_dir)
    missing = [s.file_name for s in SPECS if not (data_dir / s.file_name).exists()]
    if missing:
        raise FileNotFoundError(f"{data_dir} 에 파일 없음: {', '.join(missing)}")

    apply_schema(conn, SCHEMA)
    results: list[FileResult] = []
    with conn:  # 배치 전체 = 하나의 트랜잭션
        batch_id = conn.execute(
            "INSERT INTO load_batches (started_at, data_dir) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), str(data_dir)),
        ).lastrowid
        for spec in SPECS:
            path = data_dir / spec.file_name
            r = _load_file(conn, batch_id, spec, path)
            _, enc = csvio.read_text(path)
            conn.execute(
                "INSERT INTO load_files VALUES (?,?,?,?,?,?,?,?)",
                (batch_id, spec.file_name, csvio.file_sha256(path), enc,
                 r.rows_read, r.inserted, r.unchanged, r.quarantined),
            )
            results.append(r)
    return results
