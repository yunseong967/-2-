"""CSV 읽기/쓰기와 행 해시.

- 현장 엑셀 저장본을 고려해 UTF-8(BOM 포함)과 CP949를 순서대로 시도한다.
- 원본 행 번호는 '파일의 물리적 줄 번호'(헤더 = 1행)로 보존한다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

ENCODINGS = ("utf-8-sig", "cp949")


def read_text(path: str | Path) -> tuple[str, str]:
    raw = Path(path).read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"지원하지 않는 인코딩: {path} (시도: {', '.join(ENCODINGS)})")


def read_rows(path: str | Path) -> list[tuple[int, dict[str, str]]]:
    """[(원본 줄 번호, {컬럼: 값})] 반환. 값 앞뒤 공백은 제거한다."""
    text, _ = read_text(path)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = []
    for record in reader:
        clean = {k.strip(): (v or "").strip() for k, v in record.items() if k is not None}
        rows.append((reader.line_num, clean))
    return rows


def write_rows(path: str | Path, header: list[str], rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def row_hash(record: dict[str, str], columns: list[str]) -> str:
    """업무 컬럼만으로 계산한 해시. 줄 위치가 바뀌어도 같은 내용이면 같은 값."""
    payload = json.dumps([record.get(c, "") for c in columns], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
