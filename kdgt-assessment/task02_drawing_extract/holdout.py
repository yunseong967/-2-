"""홀드아웃 평가: 개발 중 한 번도 보지 않은 무작위 도면 10장(벡터 5 + 스캔 5).

개발용 10장(D01~D10)은 추출기를 고치면서 계속 봤으므로 그 정확도는 낙관적이다.
여기서는 seed 고정 무작위 사양으로 새 도면을 만들고, 결과를 본 뒤 추출기를 고치지 않고 그대로 보고한다.
정답은 사양(도면에 적은 내용)에서 바로 만든다: 적힌 표기 → mm 환산, 적지 않은 값 → 검토 필요.
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from . import evaluate as E, extract as X, render as R

HERE = Path(__file__).parent
OUT = HERE / "drawings" / "holdout"
SEED = 20261105


def fmt_len(mm: float, unit: str) -> str:
    v = mm / {"mm": 1, "cm": 10, "m": 1000}[unit]
    return (f"{v:g} {unit}" if unit != "mm" else f"{v:g}") if unit != "mm" else f"{mm:g}"


def make_specs(seed: int = SEED) -> list[tuple[str, dict, dict]]:
    rng = random.Random(seed)
    specs = []
    pats = [(2, "row"), (4, "corners"), (6, "grid3x2"), (8, "bolt"), (1, "center"), (0, None), (8, "grid4x2")]
    for i in range(10):
        did = f"H{i + 1:02d}"
        fmt = "vector" if i < 5 else "scan"
        L = rng.choice([200, 250, 400, 600, 800, 1000, 1200, 1500])
        Wd = rng.choice([v for v in (150, 200, 300, 400, 600, 800, 1000) if v <= L])
        thk = rng.choice([3, 4.5, 6, 8, 10, 12, 16])
        n, pat = rng.choice(pats)
        dia = rng.choice([8, 10, 12, 14, 16, 18, 22]) if n else 0
        part = f"{rng.choice(['PL', 'BRK', 'SP', 'CV', 'RB'])}-{rng.randint(100, 999)}"
        mat = rng.choice(["SUS304", "SUS316L", "SS400"])
        name = rng.choice(["보강 판", "덮개 판", "받침 판", "브래킷", "칸막이 판"])
        # 단위 혼용·누락을 무작위로
        h_unit = rng.choice(["mm", "mm", "m", "cm"])
        v_unit = rng.choice(["mm", "mm", "cm"])
        thk_text = rng.choice([f"{thk:g}", f"{thk:g}", f"{thk / 10:g} cm" if thk >= 10 else f"{thk:g}"])
        missing = rng.choice([None, None, None, "thk", "v"])
        callout = f"{n}-Ø{dia} THRU" if n and rng.random() > 0.2 else None
        spec = dict(fmt=fmt, part=part, name=name, mat=mat, thk="" if missing == "thk" else thk_text, unit="mm",
                    L=L, Wd=Wd, h_text=fmt_len(L, h_unit), v_text=None if missing == "v" else fmt_len(Wd, v_unit),
                    holes=(n, dia, pat), callout=callout,
                    angle=round(rng.uniform(-2.0, 2.0), 2), noise=rng.choice([8, 12, 16, 20]))
        ans = dict(drawing=did, format=fmt, part_no=part, name=name, material=mat,
                   thickness_mm=None if missing == "thk" else thk, length_mm=L, width_mm=None if missing == "v" else Wd,
                   hole_count=n, hole_dia_mm=dia if (n and callout) else None,
                   expect_review=(["thickness_mm"] if missing == "thk" else []) + (["width_mm"] if missing == "v" else [])
                   + (["hole_dia_mm"] if n and not callout else []),
                   note=f"{fmt}, 가로 '{spec['h_text']}', 세로 '{spec['v_text']}', 두께 '{spec['thk']}', 홀 {n}개 {pat or ''}"
                        + (" 지시 없음" if n and not callout else "") + (f", 기울기 {spec['angle']}° 잡음 {spec['noise']}" if fmt == "scan" else ""))
        specs.append((did, spec, ans))
    return specs


def run(out_dir: Path | None = HERE / "reports") -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    master = set(json.loads((HERE / "fixtures/part_master.json").read_text(encoding="utf-8"))["parts"])
    for i, (did, spec, ans) in enumerate(make_specs()):
        target = OUT / f"{did}_{spec['fmt']}.pdf"
        if spec["fmt"] == "vector":
            R.draw_vector(did, spec, target)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                v = Path(tmp) / "v.pdf"
                R.draw_vector(did, spec, v)
                R.to_scan(v, target, spec["angle"], spec["noise"], seed=500 + i)
        (OUT / f"{did}_answer.json").write_text(json.dumps(ans, ensure_ascii=False, indent=1), encoding="utf-8")
        r = X.extract(target, master | {ans["part_no"]})        # 마스터에는 실제 품번이 등록돼 있다고 가정
        verdicts = {k: E.judge(r["fields"][k], ans[k], k in ans["expect_review"]) for k in X.FIELDS}
        rows.append({"answer": ans, "result": r, "verdicts": verdicts})
    s = E.summarize(rows)
    if out_dir:
        mark = {"정답": "✔", "오답": "✘", "보류": "?"}
        L = ["# 과제 02 홀드아웃 평가 (개발 중 보지 않은 도면 10장)", "",
             f"seed {SEED} 무작위 사양. **결과를 본 뒤 추출기를 고치지 않았다.** 정답은 사양에서 생성 (`drawings/holdout/*_answer.json`).", "",
             "| 형식 | 필드 | 정답 | 오답 | 보류 | 정확도 | 평균 처리시간 |", "|---|---:|---:|---:|---:|---:|---:|",
             *[f"| {f} | {d['n']} | {d['정답']} | **{d['오답']}** | {d['보류']} | {d['accuracy']}% | {s['time_ms'][f]['mean']:,} ms |"
               for f, d in s["by_format"].items()], "",
             "| 도면 | 내용 | " + " | ".join(E.LABEL.values()) + " |", "|---|---|" + "---|" * len(E.LABEL)]
        for r in rows:
            cells = []
            for k in X.FIELDS:
                f = r["result"]["fields"][k]
                v = "검토" if f["status"] == "REVIEW" else ("-" if f["value"] is None else f"{f['value']:g}" if isinstance(f["value"], float) else str(f["value"]))
                cells.append(f"{mark[r['verdicts'][k]]} {v}")
            L.append(f"| {r['answer']['drawing']} | {r['answer']['note']} | " + " | ".join(cells) + " |")
        L += ["", "## 오답·보류 상세", ""]
        for r in rows:
            for k, v in r["verdicts"].items():
                if v != "정답":
                    f = r["result"]["fields"][k]
                    L.append(f"- {r['answer']['drawing']} {E.LABEL[k]}: {v} — 정답 {r['answer'][k]!r}, 추출 {f['value']!r} "
                             f"({f['status']}) 근거 `{f.get('evidence')}` 사유 `{f.get('reason')}`")
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "holdout_accuracy.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return {"rows": rows, "summary": s}


if __name__ == "__main__":
    res = run()
    for f, d in res["summary"]["by_format"].items():
        print(f, d)
