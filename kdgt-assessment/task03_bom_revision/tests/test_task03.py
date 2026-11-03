"""과제 03 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
import copy
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from task03_bom_revision import bom, verify

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    c = bom.connect(":memory:")
    bom.init_db(c)
    yield c
    c.close()


def codes(doc):
    with pytest.raises(bom.BomInvalid) as e:
        bom.validate(doc)
    return sorted(x["code"] for x in e.value.errors)


def mini(lines, items=None, **kw):
    items = items or [{"part_no": "TOP", "name": "최상위", "kind": "ASSEMBLY"},
                      {"part_no": "S1", "name": "조립1", "kind": "ASSEMBLY"},
                      {"part_no": "S2", "name": "조립2", "kind": "ASSEMBLY"},
                      {"part_no": "P1", "name": "부품1", "kind": "PART"}]
    return {"product_no": "TOP", "drawing_no": "D", "revision": kw.get("rev", "A"),
            "effective_date": kw.get("date", "2026-01-01"), "items": items, "lines": lines}


# ---------------------------------------------------------------- 전체 검증
def test_full_verification_passes():
    checks, _ = verify.run(None)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


# ---------------------------------------------------------------- 순환 참조
def test_three_level_cycle_reports_path():
    doc = mini([{"parent": "TOP", "child": "S1", "qty": 1}, {"parent": "S1", "child": "S2", "qty": 1},
                {"parent": "S2", "child": "S1", "qty": 1}, {"parent": "S2", "child": "P1", "qty": 1}])
    with pytest.raises(bom.BomInvalid) as e:
        bom.validate(doc)
    cyc = [x for x in e.value.errors if x["code"] == "CYCLE"]
    assert len(cyc) == 1 and cyc[0]["path"][0] == cyc[0]["path"][-1] and set(cyc[0]["path"]) == {"S1", "S2"}


def test_root_inside_cycle():
    doc = mini([{"parent": "TOP", "child": "S1", "qty": 1}, {"parent": "S1", "child": "TOP", "qty": 1},
                {"parent": "S1", "child": "P1", "qty": 1}, {"parent": "TOP", "child": "S2", "qty": 1},
                {"parent": "S2", "child": "P1", "qty": 1}])
    assert "CYCLE" in codes(doc)


def test_shared_child_is_not_a_cycle():
    """다이아몬드 구조(같은 부품을 두 조립품이 공유)는 순환이 아니다."""
    doc = mini([{"parent": "TOP", "child": "S1", "qty": 1}, {"parent": "TOP", "child": "S2", "qty": 1},
                {"parent": "S1", "child": "P1", "qty": 2}, {"parent": "S2", "child": "P1", "qty": 3}])
    bom.validate(doc)


# ---------------------------------------------------------------- 검증 규칙
def test_all_errors_returned_at_once():
    doc = mini([{"parent": "TOP", "child": "S1", "qty": 0}, {"parent": "TOP", "child": "S1", "qty": 1},
                {"parent": "S1", "child": "P1", "qty": 1, "length": {"value": 2, "unit": "ft"}},
                {"parent": "TOP", "child": "X9", "qty": 1}])
    got = codes(doc)
    for c in ("QTY_NOT_POSITIVE", "DUPLICATE_LINE", "UNIT_UNSUPPORTED", "UNKNOWN_PART", "EMPTY_ASSEMBLY"):
        assert c in got


def test_bad_qty_does_not_create_false_orphan():
    """수량 오류 줄 때문에 자식이 '연결 안 됨'으로 잘못 보고되면 안 된다 (AI_LOG 오류 #1)."""
    doc = mini([{"parent": "TOP", "child": "S1", "qty": "abc"}, {"parent": "S1", "child": "P1", "qty": 1},
                {"parent": "TOP", "child": "S2", "qty": 1}, {"parent": "S2", "child": "P1", "qty": 1}])
    assert codes(doc) == ["NUMBER_INVALID"]


def test_root_with_parent_rejected():
    items = [{"part_no": "TOP", "name": "t", "kind": "ASSEMBLY"}, {"part_no": "S1", "name": "s", "kind": "ASSEMBLY"},
             {"part_no": "P1", "name": "p", "kind": "PART"}]
    doc = mini([{"parent": "TOP", "child": "P1", "qty": 1}, {"parent": "S1", "child": "TOP", "qty": 1}], items)
    got = codes(doc)
    assert "ROOT_HAS_PARENT" in got or "ORPHAN_ITEM" in got


@pytest.mark.parametrize("field", ["product_no", "revision", "effective_date", "drawing_no"])
def test_missing_header(field):
    doc = load("rev_A")
    doc[field] = ""
    assert "MISSING_FIELD" in codes(doc)


# ---------------------------------------------------------------- 단위/수량
@pytest.mark.parametrize("value, unit, mm", [(1250, "mm", "1250"), ("1.25", "M", "1250"), (125, "cm", "1250"),
                                             ("0.001", "m", "1"), ("2.5", "mm", "2.5")])
def test_unit_conversion(value, unit, mm):
    errs = []
    assert bom.canon(bom.to_mm({"value": value, "unit": unit}, "x", errs)) == mm and not errs


def test_float_input_has_no_binary_error():
    errs = []
    v = bom.to_mm({"value": 0.1, "unit": "m"}, "x", errs) * 3
    assert bom.canon(v) == "300"


def test_decimal_qty_and_shared_child_lengths(conn):
    """같은 부품이 두 조립품에 다른 길이로 들어가면 총 길이는 경로별 합."""
    items = [{"part_no": "TOP", "name": "t", "kind": "ASSEMBLY"}, {"part_no": "S1", "name": "a", "kind": "ASSEMBLY"},
             {"part_no": "S2", "name": "b", "kind": "ASSEMBLY"}, {"part_no": "PIPE", "name": "p", "kind": "PART"},
             {"part_no": "PAINT", "name": "도료(L)", "kind": "PART"}]
    doc = mini([{"parent": "TOP", "child": "S1", "qty": 2}, {"parent": "TOP", "child": "S2", "qty": 1},
                {"parent": "S1", "child": "PIPE", "qty": 3, "length": {"value": 1, "unit": "m"}},
                {"parent": "S2", "child": "PIPE", "qty": 1, "length": {"value": 500, "unit": "mm"}},
                {"parent": "S1", "child": "PAINT", "qty": "0.25"}], items)
    bom.register(conn, doc)
    ex = {e["part_no"]: e for e in bom.get_bom(conn, "TOP", unit="m")["exploded"]}
    assert ex["PIPE"]["total_qty"] == "7"            # 2×3 + 1×1
    assert ex["PIPE"]["total_length"] == "6.5"       # 2×3×1m + 1×0.5m
    assert ex["PAINT"]["total_qty"] == "0.5"         # 2 × 0.25


# ---------------------------------------------------------------- 개정 등록 규칙
def test_same_bom_written_differently_is_idempotent(conn):
    a = load("rev_A")
    bom.register(conn, a)
    b = copy.deepcopy(a)
    b["lines"] = list(reversed(b["lines"]))
    for ln in b["lines"]:
        if ln["child"] == "PIPE-25A":
            ln["length"] = {"value": 125, "unit": "cm"}
    assert bom.register(conn, b)["created"] is False


def test_revision_cannot_be_overwritten(conn):
    bom.register(conn, load("rev_A"))
    a = load("rev_A")
    a["lines"][0]["qty"] = 2
    with pytest.raises(bom.BomError) as e:
        bom.register(conn, a)
    assert e.value.code == "REVISION_EXISTS"


def test_revision_must_be_later(conn):
    bom.register(conn, load("rev_B"))
    with pytest.raises(bom.BomError) as e:
        bom.register(conn, load("rev_A"))     # A 가 B 보다 이른 날짜
    assert e.value.code == "REVISION_ORDER"


def test_db_triggers_block_edit_and_delete(conn):
    bom.register(conn, load("rev_A"))
    for sql in ("UPDATE rev_items SET material='X'", "DELETE FROM rev_lines", "UPDATE revisions SET note='x'"):
        with pytest.raises(sqlite3.DatabaseError, match="REVISION_IMMUTABLE"):
            conn.execute(sql)


def test_invalid_revision_leaves_db_unchanged(conn):
    bom.register(conn, load("rev_A"))
    with pytest.raises(bom.BomInvalid):
        bom.register(conn, load("invalid/cycle_nested"))
    assert conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 1


# ---------------------------------------------------------------- 조회
def test_as_of_between_revisions(conn):
    for r in "ABCD":
        bom.register(conn, load(f"rev_{r}"))
    assert bom.get_bom(conn, "RX-500", as_of="2026-05-15")["revision"] == "C"
    with pytest.raises(bom.NotFound):
        bom.get_bom(conn, "RX-500", as_of="2025-12-31")


def test_diff_reverse_direction(conn):
    for r in "CD":
        d = load(f"rev_{r}")
        bom.register(conn, d)
    d = bom.diff(conn, "RX-500", "D", "C")
    assert d["summary"]["ADDED"] == 1 and d["summary"]["QTY_CHANGED"] == 2   # LUG 다시 추가, 수량 되돌림


def test_thickness_always_mm(conn):
    bom.register(conn, load("rev_A"))
    ex = {e["part_no"]: e for e in bom.get_bom(conn, "RX-500", unit="m")["exploded"]}
    assert ex["LUG"]["thickness_mm"] == "10" and ex["LEG"]["total_length"] == "3.2"


def test_bad_unit_query(conn):
    bom.register(conn, load("rev_A"))
    with pytest.raises(bom.BomError):
        bom.get_bom(conn, "RX-500", unit="inch")
