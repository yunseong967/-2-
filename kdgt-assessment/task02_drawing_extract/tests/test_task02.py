"""과제 02 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
import json
from pathlib import Path

import numpy as np
import pytest

from task02_drawing_extract import evaluate as E, extract as X, holdout as H

HERE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text, unit, mm", [("1260", "mm", 1260), ("0.96 m", "mm", 960), ("1 cm", "mm", 10),
                                             ("80cm", None, 800), ("4,5", "mm", 4.5), ("1.2 M", None, 1200)])
def test_parse_measure(text, unit, mm):
    assert X.parse_measure(text, unit)[0] == mm


@pytest.mark.parametrize("text, unit", [("1260", None), ("12 inch", "mm"), ("150.", "mm"), ("abc", "mm")])
def test_parse_measure_refuses_to_guess(text, unit):
    assert X.parse_measure(text, unit)[0] is None


def test_judge_rules():
    ok = lambda v: {"value": v, "status": "OK"}
    rv = {"value": None, "status": "REVIEW"}
    assert E.judge(ok(6.0), 6, False) == "정답"
    assert E.judge(ok(7.0), 6, False) == "오답"
    assert E.judge(rv, 6, False) == "보류"
    assert E.judge(rv, None, True) == "정답"
    assert E.judge(ok(3.0), None, True) == "오답"        # 도면에 없는데 만들어 냄


@pytest.mark.parametrize("ang", [1.3, -1.3, 0.6, -0.6])
def test_deskew_handles_both_signs(ang):
    import cv2
    img = np.full((800, 1100), 255, np.uint8)
    cv2.rectangle(img, (40, 40), (1060, 760), 0, 3)
    M = cv2.getRotationMatrix2D((550, 400), ang, 1.0)
    rot = cv2.warpAffine(img, M, (1100, 800), borderValue=255)
    _, measured = X._deskew(rot)
    assert abs(abs(measured) - abs(ang)) < 0.2 and abs(measured) < 45     # 90° 돌리지 않음


def test_development_set_has_no_wrong_values_with_master():
    res = E.run(None, use_master=True)
    s = res["summary"]["by_format"]
    assert s["all"]["오답"] == 0 and s["vector"]["accuracy"] == 100.0


def test_missing_values_are_not_guessed():
    res = E.run(None, use_master=True)
    rows = {r["answer"]["drawing"]: r["result"]["fields"] for r in res["rows"]}
    assert rows["D05"]["thickness_mm"]["status"] == "REVIEW"            # 두께 칸 비어 있음
    assert rows["D09"]["width_mm"]["status"] == "REVIEW"                # 세로 치수 없음
    assert rows["D02"]["hole_dia_mm"]["status"] == "REVIEW"             # 지름 지시 없음
    assert rows["D10"]["hole_count"]["value"] == 0                      # 없는 홀을 만들지 않음


def test_part_master_turns_ocr_slip_into_review_with_candidate():
    res = X.extract(HERE / "drawings" / "D09_scan.pdf",
                    set(json.loads((HERE / "fixtures/part_master.json").read_text())["parts"]))
    f = res["fields"]["part_no"]
    assert f["status"] == "REVIEW" and f.get("candidate") == "COVER-01"


def test_holdout_never_confirms_a_wrong_value():
    """개발 중 보지 않은 도면 10장 — 정확도보다 '틀린 값을 확정하지 않는 것'을 지킨다."""
    s = H.run(None)["summary"]["by_format"]
    assert s["all"]["오답"] == 0 and s["vector"]["accuracy"] == 100.0


def test_dxf_path_exact_geometry():
    r = X.extract(HERE / "drawings" / "D01_dxf.dxf")
    f = r["fields"]
    assert (f["length_mm"]["value"], f["width_mm"]["value"], f["hole_count"]["value"], f["hole_dia_mm"]["value"]) == (1260, 1000, 4, 12)
    assert X.extract(HERE / "drawings" / "D05_dxf.dxf")["fields"]["thickness_mm"]["status"] == "REVIEW"


def test_bom_item_matches_task03_format():
    from task03_bom_revision import bom
    r = X.extract(HERE / "drawings" / "D01_vector.pdf")
    item = X.to_bom_item(r)
    doc = {"product_no": "ASM", "drawing_no": "D", "revision": "A", "effective_date": "2026-01-01",
           "items": [{"part_no": "ASM", "name": "조립", "kind": "ASSEMBLY"}, item],
           "lines": [{"parent": "ASM", "child": item["part_no"], "qty": 2}]}
    assert bom.validate(doc)["items"]["PL-SHELL"]["thickness_mm"] == "6"
