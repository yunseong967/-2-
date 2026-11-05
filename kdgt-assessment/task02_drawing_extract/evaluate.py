"""과제 02 평가: 도면 10장 × 7필드를 고정된 정답과 비교하고 처리시간·오류를 보고한다.

판정 (필드마다)
  정답  : 값을 맞게 확정 / 도면에 없는 값을 '검토 필요'로 돌림 / 해당 없음(홀 0개의 지름)을 비워 둠
  오답  : 틀린 값을 확정 (가장 위험) / 도면에 없는데 값을 만들어 냄
  보류  : 값이 도면에 있는데 '검토 필요'로 돌림 (안전하지만 사람 확인 필요)
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from . import extract as X

HERE = Path(__file__).parent
DRAW = HERE / "drawings"


def judge(field: dict, expected, expect_review: bool) -> str:
    st, v = field["status"], field["value"]
    if expect_review:
        return "정답" if st == "REVIEW" else "오답"
    if st == "REVIEW":
        return "보류"
    if expected is None:
        return "정답" if v is None else "오답"
    if isinstance(expected, (int, float)) and isinstance(v, (int, float)):
        return "정답" if abs(float(v) - float(expected)) < 1e-6 else "오답"
    return "정답" if v == expected else "오답"


def run(out: Path | None = None, use_master: bool = True) -> dict:
    master = set(json.loads((HERE / "fixtures/part_master.json").read_text(encoding="utf-8"))["parts"]) if use_master else None
    rows = []
    for pdf in sorted(DRAW.glob("D*.pdf")):
        ans = json.loads((HERE / "fixtures/answers" / f"{pdf.stem.split('_')[0]}.json").read_text(encoding="utf-8"))
        r = X.extract(pdf, master)
        verdicts = {k: judge(r["fields"][k], ans[k], k in ans["expect_review"]) for k in X.FIELDS}
        rows.append({"answer": ans, "result": r, "verdicts": verdicts})
    summary = summarize(rows)
    if out:
        write(Path(out), rows, summary, use_master)
    return {"rows": rows, "summary": summary}


def summarize(rows) -> dict:
    s = {"by_field": {}, "by_format": {}, "time_ms": {}}
    for fmt in ("vector", "scan", "all"):
        sel = [r for r in rows if fmt == "all" or r["answer"]["format"] == fmt]
        cnt = {"정답": 0, "오답": 0, "보류": 0}
        for r in sel:
            for v in r["verdicts"].values():
                cnt[v] += 1
        n = sum(cnt.values())
        s["by_format"][fmt] = {**cnt, "n": n, "accuracy": round(100 * cnt["정답"] / n, 1) if n else None}
        t = [r["result"]["processing_ms"] for r in sel]
        s["time_ms"][fmt] = {"mean": round(statistics.mean(t), 1), "max": max(t), "n": len(t)}
    for k in X.FIELDS:
        cnt = {fmt: {"정답": 0, "오답": 0, "보류": 0} for fmt in ("vector", "scan")}
        for r in rows:
            cnt[r["answer"]["format"]][r["verdicts"][k]] += 1
        s["by_field"][k] = cnt
    return s


LABEL = {"part_no": "품번", "material": "재질", "thickness_mm": "두께", "length_mm": "가로", "width_mm": "세로",
         "hole_count": "홀 개수", "hole_dia_mm": "홀 지름"}


def write(out: Path, rows, s, use_master):
    out.mkdir(parents=True, exist_ok=True)
    ex = out / "extracted"
    ex.mkdir(exist_ok=True)
    for r in rows:
        (ex / f"{r['result']['drawing']}.json").write_text(json.dumps(r["result"], ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    tag = "with_master" if use_master else "raw"
    L = [f"# 과제 02 필드별 정확도 ({'품번 마스터 대조 포함' if use_master else '원 추출 (마스터 대조 없음)'})", "",
         "정답은 추출기 작성 전에 커밋한 `fixtures/answers/*.json` (커밋 99a4d29).", "",
         "## 형식별", "", "| 형식 | 도면 | 필드 | 정답 | 오답 | 보류 | 정확도 | 평균 처리시간 | 최대 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
         *[f"| {f} | {s['time_ms'][f]['n']} | {d['n']} | {d['정답']} | **{d['오답']}** | {d['보류']} | {d['accuracy']}% | "
           f"{s['time_ms'][f]['mean']:,} ms | {s['time_ms'][f]['max']:,} ms |" for f, d in s["by_format"].items()], "",
         "## 필드별 (정답/오답/보류)", "", "| 필드 | 벡터 5장 | 스캔 5장 |", "|---|---|---|",
         *[f"| {LABEL[k]} | {c['vector']['정답']}/{c['vector']['오답']}/{c['vector']['보류']} | "
           f"{c['scan']['정답']}/{c['scan']['오답']}/{c['scan']['보류']} |" for k, c in s["by_field"].items()], "",
         "## 도면별", "", "| 도면 | 형식 | 시험 내용 | 처리(ms) | " + " | ".join(LABEL.values()) + " |",
         "|---|---|---|---:|" + "---|" * len(LABEL)]
    mark = {"정답": "✔", "오답": "✘", "보류": "?"}
    for r in rows:
        a, res = r["answer"], r["result"]
        cells = []
        for k in X.FIELDS:
            f = res["fields"][k]
            v = "검토" if f["status"] == "REVIEW" else ("-" if f["value"] is None else f"{f['value']:g}" if isinstance(f["value"], float) else str(f["value"]))
            cells.append(f"{mark[r['verdicts'][k]]} {v}")
        L.append(f"| {a['drawing']} | {a['format']} | {a['note']} | {res['processing_ms']:,} | " + " | ".join(cells) + " |")
    L += ["", "✔ 정답 · ✘ 오답 · ? 보류(값이 있는데 검토로 돌림)", ""]
    (out / f"accuracy_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    items = [X.to_bom_item(r["result"]) for r in rows]
    (out / "bom_items_for_task03.json").write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    for m in (False, True):
        res = run(HERE / "reports", use_master=m)
        s = res["summary"]["by_format"]
        print(f"[{'마스터 대조' if m else '원 추출'}] 전체 {s['all']['정답']}/{s['all']['n']} ({s['all']['accuracy']}%) "
              f"오답 {s['all']['오답']} 보류 {s['all']['보류']} | 벡터 {s['vector']['accuracy']}% 스캔 {s['scan']['accuracy']}%")


if __name__ == "__main__":
    main()
