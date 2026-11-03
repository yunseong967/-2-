"""과제 05 보고서 작성: 지표 비교, 분할 목록, 누수 검사, 큰 오차 분석, 모델 카드."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import model as M

HERE = Path(__file__).parent


def bootstrap(y, preds: dict, groups, n: int = 2000, seed: int = 0) -> dict:
    """MAPE 95% 구간과 '기준선 대비 개선'의 95% 구간.
    같은 프로젝트의 재견적들은 오차가 함께 움직이므로(서로 독립이 아님) 행이 아니라 **프로젝트 단위로** 다시 뽑는다.
    (처음엔 행 단위로 뽑아 구간이 실제보다 좁게 나왔다 — AI_LOG 오류 #1)"""
    y = np.asarray(y, float)
    groups = np.asarray(groups)
    uniq = np.unique(groups)
    members = [np.flatnonzero(groups == g) for g in uniq]
    rng = np.random.default_rng(seed)
    ape = {k: np.abs(np.asarray(p) - y) / y * 100 for k, p in preds.items()}
    base = list(preds)[0]
    sums = {k: [] for k in preds}
    gains = {k: [] for k in preds}
    for _ in range(n):
        idx = np.concatenate([members[i] for i in rng.integers(0, len(uniq), len(uniq))])
        for k, a in ape.items():
            sums[k].append(a[idx].mean())
            gains[k].append(ape[base][idx].mean() - a[idx].mean())
    pct = lambda v: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return {k: {"ci": pct(sums[k]), "gain_ci": pct(gains[k])} for k in preds} | {"_projects": len(uniq)}


def error_table(res: dict, k: int = 10) -> pd.DataFrame:
    te = res["test"].copy()
    trva = pd.concat([res["train"], res["val"]])
    p = res["test_preds"][res["chosen"]]
    te["pred"] = p
    te["baseline"] = M.baseline(te)
    te["ape"] = np.abs(p - te[M.TARGET]) / te[M.TARGET] * 100
    vmin, vmax = trva.volume_l.min(), trva.volume_l.max()
    seen = set(trva.product_type)

    def tags(r):
        t = []
        if r.product_type not in seen:
            t.append("학습에 없던 유형")
        if r.rush:
            t.append("급행")
        if r.qty > 1:
            t.append(f"수량 {r.qty}")
        if not (vmin <= r.volume_l <= vmax):
            t.append("용량 범위 밖")
        if r.quote_date >= date(2026, 6, 1):
            t.append("6월 단가·요율 인상 이후")
        return ", ".join(t) or "-"
    te["tags"] = te.apply(tags, axis=1)
    return te.sort_values("ape", ascending=False).head(k)


def write_all(out: Path, res: dict, leaks, leaky, repro) -> None:
    out.mkdir(parents=True, exist_ok=True)
    df, zeros = res["df"], res["zeros"]

    # 분할 목록 (제외 행 포함)
    allrows = pd.concat([df.assign(note=""), zeros.assign(note="0원(취소) 제외")])
    allrows.loc[allrows.split == "embargo", "note"] = allrows.loc[allrows.split == "embargo", "note"].replace("", "완충기간 제외")
    allrows.sort_values(["quote_date", "record_id"])[
        ["record_id", "project_id", "quote_date", "product_type", "split", "note"]].to_csv(out / "splits.csv", index=False)

    te = res["test"]
    boot = bootstrap(te[M.TARGET], res["test_preds"], te["project_id"])
    fmt = lambda v: f"{v:,.0f}"
    L = ["# 과제 05 모델 비교 결과", "",
         f"- 데이터: `data/quotes_history.csv` {len(res['raw'])}행, 프로젝트 {res['raw'].project_id.nunique()}개 "
         f"(SHA-256 앞 12자리 `{M.data_hash()[:12]}`)",
         f"- 0원(취소) 제외 {len(zeros)}건, 완충기간 제외 {int((df.split == 'embargo').sum())}건",
         f"- 분할(프로젝트 단위·시간순): train {len(res['train'])}행 / val {len(res['val'])}행 / test {len(te)}행", "",
         "## 1. 검증셋(val)으로 모델 선택 — train 으로 학습", "",
         "| 모델 | n | MAPE | MAE(원) |", "|---|---:|---:|---:|",
         *[f"| {k}{' **← 선택**' if k == res['chosen'] else ''} | {v['n']} | {v['MAPE']:.2f}% | {fmt(v['MAE'])} |"
           for k, v in res["val_scores"].items()],
         "", "## 2. 테스트셋(test) 최종 평가 — train+val 로 재학습, 테스트는 한 번만 사용", "",
         "| 모델 | n | MAE(원) | MAPE | MAPE 95% 구간 | 기준선 대비 MAPE 개선(95% 구간) | MdAPE | ±5% 이내 | ±10% 이내 | 평균 편향 |",
         "|---|---:|---:|---:|---|---|---:|---:|---:|---:|",
         *[f"| {k} | {v['n']} | {fmt(v['MAE'])} | **{v['MAPE']:.2f}%** | {boot[k]['ci'][0]:.1f}~{boot[k]['ci'][1]:.1f}% | "
           + ("-" if i == 0 else f"{boot[k]['gain_ci'][0]:+.1f}~{boot[k]['gain_ci'][1]:+.1f}%p")
           + f" | {v['MdAPE']:.2f}% | {v['within5']:.0f}% | {v['within10']:.0f}% | {v['bias_pct']:+.1f}% |"
           for i, (k, v) in enumerate(res["test_scores"].items())],
         "", f"95% 구간: 테스트 프로젝트 {boot['_projects']}개를 단위로 붓스트랩 2,000회 (재견적끼리 오차가 함께 움직이므로 행 단위가 아닌 프로젝트 단위).",
         "MAPE = 평균(|예측−실제| ÷ 실제) × 100. 실제값이 0인 행은 분모가 0이 되므로 정의상 계산 대상에서 제외했다(0원 처리 규칙).",
         "편향 = 평균((예측−실제) ÷ 실제). 음수면 과소 견적(손해 위험).", "",
         "## 3. 유형별 테스트 MAPE (선택 모델 vs 기준선)", "",
         "| 유형 | n | 기준선 | 선택 모델 |", "|---|---:|---:|---:|"]
    base_name = list(res["test_scores"])[0]
    for t, v in res["by_type"][res["chosen"]].items():
        L.append(f"| {t} | {v['n']} | {res['by_type'][base_name][t]['MAPE']:.1f}% | {v['MAPE']:.1f}% |")
    L += ["", "## 4. 잘못된 분할과의 비교 (누수가 성능을 부풀리는 정도)", "",
          "같은 'GBM 직접 예측' 모델을 프로젝트·시간을 무시한 무작위 행 분할(8:2)로 평가하면:", "",
          f"- 무작위 분할 MAPE **{leaky['metrics']['MAPE']:.2f}%** (n={leaky['n_test']}, train 과 test 에 같은 프로젝트 {leaky['shared_projects']}개 공유)",
          f"- 올바른 분할 MAPE **{res['test_scores']['GBM 직접 예측']['MAPE']:.2f}%** (n={res['test_scores']['GBM 직접 예측']['n']})",
          "", "무작위 분할은 같은 프로젝트의 재견적(거의 같은 사양)이 train·test 에 동시에 들어가고, 미래 단가로 과거를 맞히게 되므로 실제보다 좋아 보인다.", ""]
    (out / "metrics.md").write_text("\n".join(L), encoding="utf-8")

    K = ["# 과제 05 누수 검사", "", "| 결과 | 검사 | 근거 |", "|---|---|---|",
         *[f"| {'PASS' if ok else 'FAIL'} | {name} | {detail} |" for name, ok, detail in leaks], "",
         "## 재학습 재현성", "",
         f"- 같은 seed 로 데이터 재생성 → 파일 해시 동일: **{repro['data_regenerated_identical']}**",
         f"- 처음부터 두 번 학습 → 테스트 예측 해시 동일: **{repro['retrain_identical']}** ({' / '.join(repro['pred_hashes'])})",
         f"- 버전: {repro['versions']}", ""]
    (out / "leakage_check.md").write_text("\n".join(K), encoding="utf-8")

    E = error_table(res)
    tag_counts = pd.Series([t for s in error_table(res, len(te)).tags for t in s.split(", ") if t != "-"]).value_counts()
    big = error_table(res, len(te))
    big = big[big.ape > 10]
    tag_big = pd.Series([t for s in big.tags for t in s.split(", ") if t != "-"]).value_counts()
    R = ["# 과제 05 큰 오차 사례 분석 (테스트셋, 선택 모델)", "",
         "| 견적 | 프로젝트 | 날짜 | 유형 | 용량 | 재질 | 수량 | 실제 원가 | 예측 | 기준선 | 오차율 | 특이 사항 |",
         "|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|",
         *[f"| {r.record_id} | {r.project_id} | {r.quote_date} | {r.product_type} | {r.volume_l} | {r.material} | {r.qty} | "
           f"{fmt(r.actual_cost)} | {fmt(r.pred)} | {fmt(r.baseline)} | {r.ape:.1f}% | {r.tags} |" for r in E.itertuples()],
         "", f"## 오차 10% 초과 {len(big)}건의 특이 사항 빈도 (테스트 전체 {len(te)}건 중 빈도와 비교)", "",
         "| 특이 사항 | 오차 10% 초과 건 중 | 테스트 전체 중 |", "|---|---:|---:|",
         *[f"| {t} | {tag_big.get(t, 0)} / {len(big)} | {tag_counts.get(t, 0)} / {len(te)} |" for t in tag_counts.index], ""]
    (out / "error_analysis.md").write_text("\n".join(R), encoding="utf-8")

    card = {"model": res["chosen"], "target": "log(실제원가 / 기준선)", "algorithm": "sklearn HistGradientBoostingRegressor",
            "params": M.GBM_PARAMS, "features": list(M.features(res["train"].head(2)).columns),
            "train_rows": len(res["train"]) + len(res["val"]), "test_rows": len(te),
            "data_sha256": M.data_hash(), "splits": {k: [str(a), str(b)] for k, (a, b) in M.SPLITS.items()},
            "zero_rule": "actual_cost <= 0 (취소) 제외", "test_metrics": res["test_scores"][res["chosen"]],
            "versions": repro["versions"]}
    (out / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
