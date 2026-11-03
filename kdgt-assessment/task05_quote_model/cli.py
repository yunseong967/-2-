"""과제 05 명령행.

  python -m task05_quote_model.cli generate   # 합성 데이터 재생성 (seed 고정 → 같은 파일)
  python -m task05_quote_model.cli train      # 학습·평가·누수 검사·재현성 → reports/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import generate, model as M, report

HERE = Path(__file__).parent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task05")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate")
    sub.add_parser("train").add_argument("--out", default=str(HERE / "reports"))
    a = p.parse_args(argv)
    if a.cmd == "generate":
        generate.main([])
        return 0
    res = M.run()
    leaks = M.leakage_checks(res)
    leaky = M.leaky_experiment(res)
    repro = M.reproducibility()
    report.write_all(Path(a.out), res, leaks, leaky, repro)
    print(f"선택 모델(검증셋 기준): {res['chosen']}")
    for k, v in res["test_scores"].items():
        print(f"  test {k:<12} n={v['n']}  MAE {v['MAE']:>11,.0f}원  MAPE {v['MAPE']:5.2f}%  ±5% {v['within5']:.0f}%")
    print(f"  (무작위 분할이었다면 GBM 직접 MAPE {leaky['metrics']['MAPE']:.2f}% — 부풀려진 값)")
    for name, ok, detail in leaks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
    print(f"[{'PASS' if repro['retrain_identical'] and repro['data_regenerated_identical'] else 'FAIL'}] "
          f"재현성: 데이터 재생성 동일={repro['data_regenerated_identical']}, 재학습 동일={repro['retrain_identical']}")
    ok = all(x[1] for x in leaks) and repro["retrain_identical"] and repro["data_regenerated_identical"]
    print(f"보고서: {a.out}/metrics.md, leakage_check.md, error_analysis.md, splits.csv, model_card.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
