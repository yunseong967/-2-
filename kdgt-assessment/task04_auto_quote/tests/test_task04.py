"""과제 04 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
import copy
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from task03_bom_revision import bom as bomlib
from task04_auto_quote import quote as Q, reference_calc, rules as R, verify

HERE = Path(__file__).resolve().parents[1]
BOM_FIX = HERE.parent / "task03_bom_revision" / "fixtures"


def j(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    c = Q.connect(":memory:")
    Q.init_db(c)
    for r in "ABCD":
        bomlib.register(c, j(BOM_FIX / f"rev_{r}.json"))
    for f in sorted((HERE / "fixtures/prices").glob("*.json")):
        Q.register_price_version(c, j(f))
    yield c
    c.close()


def req(no="T1", rev="A", qty=1, day="2026-03-10"):
    return {"quote_no": no, "product_no": "RX-500", "revision": rev, "qty": qty, "quote_date": day, "customer": "고객"}


# ---------------------------------------------------------------- 전체 검증
def test_full_verification_passes():
    checks, _ = verify.run(None)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


@pytest.mark.parametrize("rev, qty, day", [("A", 7, "2026-05-31"), ("B", 4, "2026-06-01"), ("C", 2, "2026-12-31"),
                                           ("D", 9, "2026-03-01"), ("D", 1, "2026-06-30")])
def test_engine_matches_independent_calculator_beyond_answer_set(conn, rev, qty, day):
    r = req("X", rev, qty, day)
    q = Q.create_quote(conn, r)
    e, _ = reference_calc.calc(r, reference_calc.load_prices(), Q.load_specs())
    for k in ("material_total", "process_total", "outsource_total", "supply", "vat", "total", "status"):
        assert q[k] == e[k], k


# ---------------------------------------------------------------- 규칙
@pytest.mark.parametrize("x, out", [("0.5", 1), ("1.5", 2), ("2.4999", 2), ("2.5", 3), ("677082.35", 677082)])
def test_round_won_half_up(x, out):
    assert R.round_won(Decimal(x)) == out      # 은행가 반올림(2.5→2)이 아님


def test_weight_rounding_and_supply_vat():
    assert R.round_weight(Decimal("59.955")) == Decimal("59.96")
    assert R.truncate_supply(5_159_829) == 5_159_000
    assert R.truncate_supply(999) == 0
    assert R.vat(5_159_000) == 515_900 and R.vat(1_009) == 100


def test_hand_calculated_line(conn):
    """README 의 손 계산: 쉘 판 2장 × 59.95kg ÷ 0.85 × 4,800원 = 677,082원"""
    q = Q.create_quote(conn, req())
    line = next(l for l in q["lines"] if l["item"] == "PL-SHELL")
    assert line["amount"] == 677_082 and line["inputs"]["unit_weight_kg"] == "59.95"


# ---------------------------------------------------------------- 검토 필요 (추측 금지)
def test_no_price_version_at_date_means_all_review(conn):
    q = Q.create_quote(conn, req(day="2026-01-01"))
    assert q["status"] == "NEEDS_REVIEW" and q["total"] == 0
    assert all(l["status"] == "REVIEW" and l["amount"] is None for l in q["lines"])


def test_missing_part_spec_is_review(conn):
    specs = Q.load_specs()
    del specs["parts"]["ELB-25A"]
    q = Q.create_quote(conn, req(), specs)
    rv = [l for l in q["lines"] if l["status"] == "REVIEW"]
    assert [l["item"] for l in rv] == ["ELB-25A"] and "기준정보" in rv[0]["review_reason"]


def test_unknown_material_density_is_review(conn):
    doc = j(BOM_FIX / "rev_D.json")
    doc.update(revision="E", effective_date="2026-07-01")
    for it in doc["items"]:
        if it["part_no"] == "PL-JKT":
            it["material"] = "TITANIUM"
    bomlib.register(conn, doc)
    q = Q.create_quote(conn, req(rev="E", day="2026-07-01"))
    assert q["status"] == "NEEDS_REVIEW"
    assert [l["item"] for l in q["lines"] if l["status"] == "REVIEW"] == ["PL-JKT"]


# ---------------------------------------------------------------- 버전·불변
def test_price_version_idempotent_and_conflict(conn):
    doc = j(HERE / "fixtures/prices/material_2026-03.json")
    assert Q.register_price_version(conn, doc)["created"] is False
    doc2 = copy.deepcopy(doc)
    doc2["items"][0]["price"] = 1
    with pytest.raises(Q.QuoteError) as e:
        Q.register_price_version(conn, doc2)
    assert e.value.code == "PRICE_VERSION_EXISTS"


@pytest.mark.parametrize("bad", [{"price": 0}, {"price": "abc"}, {"key": ""}])
def test_price_version_validation(conn, bad):
    doc = j(HERE / "fixtures/prices/material_2026-03.json")
    doc.update(label="X", effective_from="2027-01-01")
    doc["items"][0].update(bad)
    with pytest.raises(Q.BadRequest):
        Q.register_price_version(conn, doc)


def test_quotes_and_prices_are_immutable(conn):
    Q.create_quote(conn, req())
    for sql in ("UPDATE quotes SET total=1", "DELETE FROM quote_lines", "UPDATE price_items SET price='1'"):
        with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE"):
            conn.execute(sql)


def test_quote_no_conflict(conn):
    Q.create_quote(conn, req())
    with pytest.raises(Q.QuoteError) as e:
        Q.create_quote(conn, req(qty=2))
    assert e.value.code == "QUOTE_NO_CONFLICT"


@pytest.mark.parametrize("field, value", [("qty", 0), ("qty", 1.5), ("qty", "x"), ("quote_date", "2026/03/10")])
def test_bad_input(conn, field, value):
    with pytest.raises(Q.BadRequest):
        Q.create_quote(conn, {**req(), field: value})


def test_unknown_revision(conn):
    with pytest.raises(bomlib.NotFound):
        Q.create_quote(conn, req(rev="Z"))


def test_category_filter(conn):
    Q.create_quote(conn, req())
    d = Q.get_quote(conn, "T1", "OUTSOURCE")
    assert {l["category"] for l in d["lines"]} == {"OUTSOURCE"} and len(d["lines"]) == 4
    with pytest.raises(Q.BadRequest):
        Q.get_quote(conn, "T1", "TAX")
