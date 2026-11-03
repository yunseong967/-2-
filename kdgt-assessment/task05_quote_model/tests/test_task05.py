"""과제 05 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from task05_quote_model import generate, model as M, report


@pytest.fixture(scope="module")
def res():
    return M.run()


def test_generator_rules():
    rows = generate.generate()
    df = pd.DataFrame(rows)
    assert len(df) >= 200
    assert (df.actual_cost == 0).sum() == 10 and (df[df.actual_cost == 0].status == "CANCELLED").all()
    psg = df[df.product_type == "PSG"]
    assert len(psg) > 0 and (pd.to_datetime(psg.quote_date).dt.date >= date(2026, 5, 1)).all()
    assert generate.generate() == rows          # 같은 seed → 같은 데이터


def test_committed_data_matches_generator(tmp_path):
    p = tmp_path / "x.csv"
    generate.write(generate.generate(), p)
    assert M.data_hash(p) == M.data_hash()


def test_zero_rows_excluded_and_metric_guard():
    df, zeros = M.clean(M.load())
    assert len(zeros) == 10 and (df.actual_cost > 0).all()
    with pytest.raises(ValueError):
        M.metrics(np.array([100.0, 0.0]), np.array([90.0, 5.0]))


def test_metrics_hand_example():
    m = M.metrics(np.array([100.0, 200.0]), np.array([110.0, 180.0]))
    assert m["n"] == 2 and m["MAE"] == 15 and m["MAPE"] == pytest.approx(10.0) and m["within10"] == 100


def test_all_leakage_checks_pass(res):
    bad = [c for c in M.leakage_checks(res) if not c[1]]
    assert not bad, bad


def test_baseline_and_features_never_read_target(res):
    te = res["test"].drop(columns=[M.TARGET, "status"])
    assert np.array_equal(M.baseline(te), M.baseline(res["test"]))
    X = M.features(te)
    assert not set(X.columns) & M.FORBIDDEN


def test_unseen_type_encoded_as_unknown():
    row = M.load().head(1).copy()
    row["product_type"] = "NEW_TYPE"
    X = M.features(row)
    assert X[[c for c in X.columns if c.startswith("type_")]].to_numpy().sum() == 0


def test_model_selection_ignores_test_targets(res, monkeypatch):
    """테스트 정답을 마구 바꿔도 선택되는 모델은 같아야 한다 (선택은 val 로만)."""
    orig_clean = M.clean

    def tampered(df):
        d, z = orig_clean(df)
        firsts = d.groupby("project_id")["quote_date"].transform("min")
        mask = firsts >= date(2026, 5, 1)
        d.loc[mask, M.TARGET] = d.loc[mask, M.TARGET] * 3
        return d, z
    monkeypatch.setattr(M, "clean", tampered)
    assert M.run()["chosen"] == res["chosen"]


def test_leaky_split_looks_better_than_honest(res):
    leaky = M.leaky_experiment(res)
    assert leaky["shared_projects"] > 0
    assert leaky["metrics"]["MAPE"] < res["test_scores"]["GBM 직접 예측"]["MAPE"]


def test_retrain_reproducible():
    r = M.reproducibility()
    assert r["retrain_identical"] and r["data_regenerated_identical"]


def test_cluster_bootstrap_wider_than_row_bootstrap(res):
    """재견적이 묶여 있으므로 프로젝트 단위 구간이 행 단위(각 행을 독립 프로젝트로 가정)보다 넓어야 한다."""
    te = res["test"]
    by_proj = report.bootstrap(te[M.TARGET], res["test_preds"], te["project_id"])
    by_row = report.bootstrap(te[M.TARGET], res["test_preds"], np.arange(len(te)))
    k = res["chosen"]
    assert (by_proj[k]["ci"][1] - by_proj[k]["ci"][0]) > (by_row[k]["ci"][1] - by_row[k]["ci"][0])


def test_chosen_model_beats_baseline_on_test(res):
    s = res["test_scores"]
    assert s[res["chosen"]]["MAPE"] < s["기준선(현행 규칙)"]["MAPE"]
