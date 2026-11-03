"""과제 05 모델 파이프라인: 정제 → 분할 → 기준선 → 모델 → 평가 → 누수 검사 → 재현성.

모델 선택은 검증셋(val)으로만 한다. 테스트셋(test)은 마지막에 한 번만 본다.
"""
from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

HERE = Path(__file__).parent
DATA = HERE / "data" / "quotes_history.csv"

# ---------------------------------------------------------------- 분할 기준 (프로젝트 첫 견적일 기준)
# 재견적은 첫 견적 후 최대 30일 → 구간 사이 1개월 '완충기간' 에 시작한 프로젝트는 버린다(시간 누수 방지)
SPLITS = {
    "train": (date(2024, 1, 1), date(2025, 10, 1)),
    "val": (date(2025, 11, 1), date(2026, 4, 1)),
    "test": (date(2026, 5, 1), date(2027, 1, 1)),
}
TARGET = "actual_cost"
FORBIDDEN = {TARGET, "status", "record_id", "project_id", "quote_date"}   # 특징으로 쓰면 안 되는 열
TYPES = ["REACTOR", "AGITATOR", "DRYER", "PSG"]
NUMERIC = ["volume_l", "shell_t_mm", "plate_weight_kg", "nozzle_count", "jacket", "weld_m", "qty", "rush",
           "sus304_price", "labor_rate"]
GBM_PARAMS = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=8,
                  l2_regularization=1.0, early_stopping=False, random_state=0)


# ---------------------------------------------------------------- 데이터
def load(path: Path = DATA) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"record_id": str, "project_id": str})
    df["quote_date"] = pd.to_datetime(df["quote_date"]).dt.date
    return df


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """0원 처리 규칙: actual_cost <= 0 (취소 건) 은 학습·평가에서 제외하고 따로 보고.
    이유: MAPE 의 분모가 0 이 되고, 취소 건은 '원가'가 아니라 '수주 실패'라서 예측 대상이 아니다."""
    bad = df[TARGET].isna() | (df[TARGET] <= 0)
    return df[~bad].copy(), df[bad].copy()


def assign_splits(df: pd.DataFrame) -> pd.DataFrame:
    first = df.groupby("project_id")["quote_date"].min()
    def which(d):
        for name, (a, b) in SPLITS.items():
            if a <= d < b:
                return name
        return "embargo"
    proj_split = first.map(which)
    out = df.copy()
    out["split"] = out["project_id"].map(proj_split)
    return out


# ---------------------------------------------------------------- 기준선 (현행 규칙 견적)
def baseline(df: pd.DataFrame) -> np.ndarray:
    """견적 담당자가 쓰던 규칙. 재료비 식은 정확하지만 노무시간은 유형 구분 없는 경험식이고,
    수량 할인·급행 할증·DRYER 대형 가중을 모른다."""
    kg = np.where(df["material"] == "SUS316L", df["sus304_price"] * 1.35, df["sus304_price"])
    material = df["plate_weight_kg"] * kg / 0.85
    hours = 30 + df["weld_m"] * 1.0 + df["nozzle_count"] * 1.5 + df["jacket"] * 10
    labor = hours * df["labor_rate"]
    outsource = 560_000 + np.where(df["material"] == "SUS316L", 25_000 * df["volume_l"] ** 0.67, 0)
    return ((material + labor + outsource) * df["qty"]).to_numpy(dtype=float)


# ---------------------------------------------------------------- 특징
def features(df: pd.DataFrame) -> pd.DataFrame:
    """처음 보는 제품 유형(예: 학습기간에 없던 PSG)은 모든 유형 열이 0 → '알 수 없음'으로 들어간다."""
    X = df[NUMERIC].astype(float).copy()
    X["sus316l"] = (df["material"] == "SUS316L").astype(float)
    for t in TYPES:
        X[f"type_{t}"] = (df["product_type"] == t).astype(float)
    X["log_baseline"] = np.log(baseline(df))
    assert not (set(X.columns) & FORBIDDEN), "금지 열이 특징에 들어감"
    return X


# ---------------------------------------------------------------- 모델
@dataclass
class Model:
    name: str
    kind: str                      # baseline | direct | residual
    est: HistGradientBoostingRegressor | None = None
    feature_names: list[str] = field(default_factory=list)

    def fit(self, train: pd.DataFrame) -> "Model":
        """train 행만 받는다. (검증·테스트 데이터는 이 함수에 들어오지 않음)"""
        if self.kind == "baseline":
            return self
        X, y = features(train), train[TARGET].to_numpy(dtype=float)
        target = np.log(y) if self.kind == "direct" else np.log(y / baseline(train))
        self.est = HistGradientBoostingRegressor(**GBM_PARAMS).fit(X, target)
        self.feature_names = list(X.columns)
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.kind == "baseline":
            return baseline(df)
        p = self.est.predict(features(df))
        return np.exp(p) if self.kind == "direct" else baseline(df) * np.exp(p)


def candidates() -> list[Model]:
    return [Model("기준선(현행 규칙)", "baseline"),
            Model("GBM 직접 예측", "direct"),
            Model("GBM 기준선 보정", "residual")]


# ---------------------------------------------------------------- 지표
def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    """MAE(원), MAPE(%) 와 표본 수. y 는 이미 0원이 제외된 값이어야 한다(아니면 오류)."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    if (y <= 0).any():
        raise ValueError("0원 이하 실제값이 평가에 들어옴 — clean() 을 거쳐야 함")
    ape = np.abs(p - y) / y
    return {"n": int(len(y)), "MAE": float(np.mean(np.abs(p - y))), "MAPE": float(100 * ape.mean()),
            "MdAPE": float(100 * np.median(ape)), "within5": float(100 * (ape <= 0.05).mean()),
            "within10": float(100 * (ape <= 0.10).mean()), "bias_pct": float(100 * np.mean((p - y) / y))}


# ---------------------------------------------------------------- 실험 전체
def data_hash(path: Path = DATA) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(path: Path = DATA) -> dict:
    raw = load(path)
    df, zeros = clean(raw)
    df = assign_splits(df)
    zeros = assign_splits(zeros)
    tr, va, te = (df[df.split == s] for s in ("train", "val", "test"))

    # 1) 검증셋으로 모델 선택 (train 으로 학습)
    val_scores = {}
    for m in candidates():
        m.fit(tr)
        val_scores[m.name] = metrics(va[TARGET], m.predict(va))
    chosen = min(val_scores, key=lambda k: val_scores[k]["MAPE"])

    # 2) 최종: train+val 로 다시 학습 → test 한 번 평가 (모든 후보를 같은 방식으로 보고)
    trva = pd.concat([tr, va])
    test_scores, test_preds = {}, {}
    for m in candidates():
        m.fit(trva)
        test_preds[m.name] = m.predict(te)
        test_scores[m.name] = metrics(te[TARGET], test_preds[m.name])

    by_type = {}
    for m_name in test_scores:
        by_type[m_name] = {t: metrics(te[TARGET][te.product_type == t], test_preds[m_name][(te.product_type == t).to_numpy()])
                           for t in TYPES if (te.product_type == t).any()}

    return {"raw": raw, "df": df, "zeros": zeros, "train": tr, "val": va, "test": te,
            "val_scores": val_scores, "chosen": chosen, "test_scores": test_scores, "test_preds": test_preds,
            "by_type": by_type}


# ---------------------------------------------------------------- 누수 검사
def leakage_checks(res: dict) -> list[tuple[str, bool, str]]:
    df, tr, va, te = res["df"], res["train"], res["val"], res["test"]
    out = []
    sets = {"train": set(tr.project_id), "val": set(va.project_id), "test": set(te.project_id)}
    overlap = {f"{a}∩{b}": len(sets[a] & sets[b]) for a, b in (("train", "val"), ("train", "test"), ("val", "test"))}
    out.append(("같은 프로젝트가 두 분할에 걸치지 않음", not any(overlap.values()), str(overlap)))

    order = (max(tr.quote_date) < min(va.quote_date)) and (max(va.quote_date) < min(te.quote_date))
    out.append(("시간 순서: train 마지막 < val 처음, val 마지막 < test 처음", order,
                f"train {min(tr.quote_date)}~{max(tr.quote_date)}, val {min(va.quote_date)}~{max(va.quote_date)}, "
                f"test {min(te.quote_date)}~{max(te.quote_date)}"))

    feats = set(features(tr.head(3)).columns)
    out.append(("정답·식별자 열이 특징에 없음", not (feats & FORBIDDEN), f"특징 {len(feats)}개, 금지 열 겹침 {feats & FORBIDDEN or '없음'}"))

    X = features(df)
    corr = {c: abs(pd.Series(X[c]).corr(np.log(df[TARGET]), method="spearman")) for c in X.columns if X[c].nunique() > 1}
    top = max(corr, key=corr.get)
    out.append(("정답과 거의 같은 특징(|순위상관|>0.995) 없음", bool(corr[top] <= 0.995), f"가장 높은 특징 {top} = {corr[top]:.3f}"))

    key = NUMERIC + ["product_type", "material"]
    dup = len(pd.merge(tr[key].drop_duplicates(), te[key].drop_duplicates(), on=key))
    out.append(("train 과 test 에 특징이 완전히 같은 행 없음", dup == 0, f"{dup}건"))

    # 테스트 정답을 섞어도 예측이 같다 = 학습에 테스트 정답이 쓰이지 않았다
    m1 = Model("x", "residual").fit(pd.concat([tr, va]))
    shuffled = te.copy()
    shuffled[TARGET] = np.random.default_rng(0).permutation(shuffled[TARGET].to_numpy())
    m2 = Model("x", "residual").fit(pd.concat([tr, va]))
    same = np.array_equal(m1.predict(te), m2.predict(shuffled))
    out.append(("테스트 정답을 섞어도 예측 불변 (학습에 미사용)", same, "예측 배열 완전 일치" if same else "불일치"))

    out.append(("0원(취소) 행은 학습·평가에서 제외", not (df[TARGET] <= 0).any(),
                f"제외 {len(res['zeros'])}건 (분할별 {res['zeros'].split.value_counts().to_dict()})"))
    return out


def leaky_experiment(res: dict, seed: int = 0) -> dict:
    """잘못된 분할(프로젝트·시간 무시한 무작위 행 분할)이면 성능이 얼마나 부풀려 보이는지 비교."""
    df = res["df"][res["df"].split != "embargo"]
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(df))
    cut = int(len(df) * 0.8)
    tr, te = df.iloc[idx[:cut]], df.iloc[idx[cut:]]
    shared = len(set(tr.project_id) & set(te.project_id))
    m = Model("x", "direct").fit(tr)
    return {"metrics": metrics(te[TARGET], m.predict(te)), "shared_projects": shared, "n_test": len(te)}


# ---------------------------------------------------------------- 재현성
def reproducibility(path: Path = DATA) -> dict:
    """같은 seed 로 데이터를 다시 만들고, 처음부터 두 번 학습해 예측이 비트 단위로 같은지 확인."""
    from . import generate
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "regen.csv"
        generate.write(generate.generate(), p)
        regen_same = data_hash(p) == data_hash(path)
    preds = []
    for _ in range(2):
        r = run(path)
        preds.append(np.concatenate([r["test_preds"][k] for k in sorted(r["test_preds"])]))
    h = [hashlib.sha256(np.ascontiguousarray(p).tobytes()).hexdigest()[:16] for p in preds]
    return {"data_regenerated_identical": regen_same, "retrain_identical": h[0] == h[1], "pred_hashes": h,
            "versions": {"python": platform.python_version(), "numpy": np.__version__,
                         "pandas": pd.__version__, "scikit-learn": sklearn.__version__}}
