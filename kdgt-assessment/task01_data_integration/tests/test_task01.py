"""과제 01 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
import csv
import shutil
from pathlib import Path

import pytest

from common.db import connect, table_counts
from task01_data_integration import generate_data, verify
from task01_data_integration.loader import BUSINESS_TABLES, load_directory
from task01_data_integration.report import broken_relations

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
ALL = BUSINESS_TABLES + ["quarantine"]


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(SAMPLE, d)
    return d


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


def append_row(path: Path, row: dict):
    with open(path, encoding="utf-8-sig") as f:
        header = next(csv.reader(f))
    with open(path, "a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=header).writerow(row)


def replace_in_file(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def codes(conn, file_name):
    return [r[0] for r in conn.execute(
        "SELECT reason_codes FROM quarantine WHERE source_file=? ORDER BY source_row", (file_name,))]


# ---------------------------------------------------------------- 완료 기준
def test_sample_passes_all_checks():
    checks = verify.run(SAMPLE)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


@pytest.mark.parametrize("seed", [1, 2, 3, 42, 777])
def test_other_seeds_also_pass(tmp_path, seed):
    """특정 샘플에만 맞춘 코드가 아닌지 확인: 다른 seed 데이터로도 정답과 일치해야 한다."""
    generate_data.generate(tmp_path, seed)
    checks = verify.run(tmp_path)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_reload_three_times_counts_unchanged(conn, data_dir):
    load_directory(conn, data_dir)
    before = table_counts(conn, ALL)
    load_directory(conn, data_dir)
    load_directory(conn, data_dir)
    assert table_counts(conn, ALL) == before
    assert conn.execute("SELECT COUNT(*) FROM load_batches").fetchone()[0] == 3  # 이력은 남음


# ---------------------------------------------------------------- 재적재 변형
def test_changed_content_same_key_is_conflict_not_overwrite(conn, data_dir):
    load_directory(conn, data_dir)
    before = table_counts(conn, BUSINESS_TABLES)
    row = conn.execute("SELECT * FROM shipments WHERE shipment_id='S2026-0001'").fetchone()
    old = f"S2026-0001,{row['project_id']},{row['lot_no']},{row['shipped_date']},{row['qty']}"
    new_qty = row["qty"] + 1
    replace_in_file(data_dir / "shipments.csv", old, old.rsplit(",", 1)[0] + f",{new_qty}")

    load_directory(conn, data_dir)
    assert table_counts(conn, BUSINESS_TABLES) == before
    assert conn.execute("SELECT qty FROM shipments WHERE shipment_id='S2026-0001'").fetchone()[0] == row["qty"]
    assert "KEY_CONFLICT" in codes(conn, "shipments.csv")


def test_incremental_new_row_only_adds_one(conn, data_dir):
    load_directory(conn, data_dir)
    before = table_counts(conn, ALL)
    append_row(data_dir / "receipts.csv", {
        "receipt_id": "R2026-0100", "lot_no": "L2026-0001", "supplier": "포스코강판",
        "received_date": "2026-12-01", "qty": "120"})
    res = {r.file_name: r for r in load_directory(conn, data_dir)}
    assert res["receipts.csv"].inserted == 1
    after = table_counts(conn, ALL)
    assert after["receipts"] == before["receipts"] + 1
    assert after["quarantine"] == before["quarantine"]


def test_registering_missing_lot_resolves_broken_row(conn, data_dir):
    load_directory(conn, data_dir)
    broken = broken_relations(conn)
    target = next(b for b in broken if b["source_file"] == "receipts.csv")
    append_row(data_dir / "materials.csv", {
        "lot_no": target["ref_lot_no"], "project_id": "P2026-001", "material_code": "M-SUS304",
        "material": "SUS304", "thickness_mm": "6", "unit": "KG"})

    load_directory(conn, data_dir)
    assert conn.execute("SELECT 1 FROM receipts WHERE receipt_id=?", (target["record_key"],)).fetchone()
    assert len(broken_relations(conn)) == len(broken) - 1
    resolved = conn.execute(
        "SELECT resolved_batch_id FROM quarantine WHERE record_key=?", (target["record_key"],)).fetchone()[0]
    assert resolved == 2


# ---------------------------------------------------------------- 개별 검증 규칙
@pytest.mark.parametrize("row, expected", [
    ({"qty": "0"}, "QTY_ZERO"),
    ({"qty": "1.5"}, "QTY_INVALID"),          # 출하 수량은 정수
    ({"qty": "abc"}, "QTY_INVALID"),
    ({"shipped_date": ""}, "MISSING_FIELD"),
    ({"shipped_date": "2026-04-31"}, "DATE_INVALID"),
    ({"project_id": "P2026-999"}, "UNKNOWN_PROJECT"),
    ({"lot_no": "L2026-0003"}, "VALUE_INVALID"),  # 다른 프로젝트 소속 LOT
])
def test_shipment_rules(conn, data_dir, row, expected):
    base = {"shipment_id": "S2026-0500", "project_id": "P2026-001", "lot_no": "L2026-0001",
            "shipped_date": "2026-12-01", "qty": "1"}
    append_row(data_dir / "shipments.csv", {**base, **row})
    load_directory(conn, data_dir)
    got = conn.execute("SELECT reason_codes FROM quarantine WHERE record_key='S2026-0500'").fetchone()
    assert got is not None and expected in got[0].split(",")


def test_inspection_result_enum(conn, data_dir):
    append_row(data_dir / "inspections.csv", {
        "inspection_id": "I2026-0500", "lot_no": "L2026-0001", "inspected_date": "2026-12-01",
        "result": "OK", "inspected_qty": "5"})
    load_directory(conn, data_dir)
    got = conn.execute("SELECT reason_codes FROM quarantine WHERE record_key='I2026-0500'").fetchone()
    assert got[0] == "VALUE_INVALID"


def test_multiple_errors_in_one_row_are_all_reported(conn, data_dir):
    append_row(data_dir / "receipts.csv", {
        "receipt_id": "R2026-0500", "lot_no": "L2026-0001", "supplier": "x",
        "received_date": "2026/12/01", "qty": "-3"})
    load_directory(conn, data_dir)
    got = conn.execute("SELECT reason_codes FROM quarantine WHERE record_key='R2026-0500'").fetchone()[0]
    assert set(got.split(",")) == {"DATE_INVALID", "QTY_NEGATIVE"}


# ---------------------------------------------------------------- 입력 형식 / 트랜잭션
def test_cp949_file_is_readable(conn, data_dir):
    p = data_dir / "orders.csv"
    p.write_bytes(p.read_text(encoding="utf-8").encode("cp949"))
    res = {r.file_name: r for r in load_directory(conn, data_dir)}
    assert res["orders.csv"].inserted == 10
    enc = conn.execute("SELECT encoding FROM load_files WHERE file_name='orders.csv'").fetchone()[0]
    assert enc == "cp949"


def test_missing_file_writes_nothing(conn, data_dir):
    (data_dir / "shipments.csv").unlink()
    with pytest.raises(FileNotFoundError):
        load_directory(conn, data_dir)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "projects" not in tables or table_counts(conn, ["projects"])["projects"] == 0


def test_failure_mid_batch_rolls_back(conn, data_dir, monkeypatch):
    load_directory(conn, data_dir)
    before = table_counts(conn, ALL)
    append_row(data_dir / "receipts.csv", {
        "receipt_id": "R2026-0101", "lot_no": "L2026-0001", "supplier": "a",
        "received_date": "2026-12-01", "qty": "1"})
    import task01_data_integration.loader as L
    orig = L._load_file

    def boom(conn_, batch_id, spec, path):
        if spec.table == "shipments":
            raise RuntimeError("중간 장애")
        return orig(conn_, batch_id, spec, path)
    monkeypatch.setattr(L, "_load_file", boom)
    with pytest.raises(RuntimeError):
        L.load_directory(conn, data_dir)
    assert table_counts(conn, ALL) == before  # 앞 파일(receipts) 신규 행도 롤백
