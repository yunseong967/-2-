"""DXF 도면 추출 (DWG 대응 경로: DWG → DXF 변환 후 이 모듈로 읽는다).

DXF 는 실제 좌표(도면 단위)를 그대로 담으므로 OCR 없이 정확하다.
  외형 = 가장 큰 닫힌 LWPOLYLINE, 홀 = 외형 안 CIRCLE (반지름이 실제 값), 단위 = $INSUNITS 헤더
  표제란 = TEXT 엔터티 (라벨과 같은 높이의 오른쪽 값)
  치수(DIMENSION) 측정값과 도형 크기가 다르면 → 검토 필요
"""
from __future__ import annotations

from pathlib import Path

INSUNITS_MM = {4: 1.0, 5: 10.0, 6: 1000.0, 1: 25.4}     # mm, cm, m, inch


def make_dxf(path: Path, spec: dict):
    """render.SPECS 한 항목으로 DXF 생성 (실척, mm)."""
    import ezdxf
    from .render import hole_centers
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    L, W = spec["L"], spec["Wd"]
    msp.add_lwpolyline([(0, 0), (L, 0), (L, W), (0, W)], close=True)
    n, dia, pat = spec["holes"]
    for (x, y) in hole_centers(n, pat, 0, 0, L, W):
        msp.add_circle((x, y), dia / 2)
    msp.add_linear_dim(base=(0, -0.1 * W), p1=(0, 0), p2=(L, 0), override={"dimtxt": W * 0.03}).render()
    if spec.get("v_text"):
        msp.add_linear_dim(base=(-0.1 * L, 0), p1=(0, 0), p2=(0, W), angle=90, override={"dimtxt": W * 0.03}).render()
    tx, ty, rh = L * 1.3, 0, max(L, W) * 0.04
    rows = [("도면번호", spec.get("dwg", "")), ("품번", spec["part"]), ("품명", spec["name"]), ("재질", spec["mat"]),
            ("두께", spec["thk"]), ("단위", spec["unit"])]
    for i, (k, v) in enumerate(reversed(rows)):
        msp.add_text(k, height=rh * 0.5).set_placement((tx, ty + i * rh))
        if v:
            msp.add_text(v, height=rh * 0.5).set_placement((tx + rh * 3, ty + i * rh))
    doc.saveas(path)


def interpret_dxf(path: Path) -> tuple[dict, list[str]]:
    import ezdxf
    from .extract import MATERIALS, field, parse_measure
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    f = INSUNITS_MM.get(doc.header.get("$INSUNITS", 0))
    notes = [f"$INSUNITS={doc.header.get('$INSUNITS', 0)}"]
    out: dict = {}

    texts = [(e.dxf.text.strip(), e.dxf.insert.x, e.dxf.insert.y, e.dxf.height) for e in msp.query("TEXT")]

    def title(label):
        lab = next((t for t in texts if t[0] == label), None)
        if lab is None:
            return None
        row = sorted([t for t in texts if abs(t[2] - lab[2]) < lab[3] * 0.5 and t[1] > lab[1]], key=lambda t: t[1])
        return row[0][0] if row else ""

    unit_txt = (title("단위") or "").lower()
    if f is None and unit_txt in ("mm", "cm", "m"):
        f = {"mm": 1.0, "cm": 10.0, "m": 1000.0}[unit_txt]
        notes.append("헤더 단위 없음 → 표제란 단위 사용")
    pn = title("품번")
    out["part_no"] = field(pn, evidence="TEXT 품번") if pn else field(None, "REVIEW", "", "품번 없음")
    nm = title("품명")
    out["name"] = field(nm, evidence="TEXT 품명") if nm else field(None, "REVIEW", "", "품명 없음")
    mat = (title("재질") or "").upper()
    out["material"] = field(mat, evidence="TEXT 재질") if mat in MATERIALS else field(None, "REVIEW", "", f"재질 {mat!r}")
    thk = title("두께")
    if thk:
        v, how = parse_measure(thk, unit_txt or None)
        out["thickness_mm"] = field(v, "OK" if v is not None else "REVIEW", how, None if v is not None else how)
    else:
        out["thickness_mm"] = field(None, "REVIEW", "", "두께 칸이 비어 있음 — 추측하지 않음")

    polys = [e for e in msp.query("LWPOLYLINE") if e.closed]
    if not polys or f is None:
        for k in ("length_mm", "width_mm", "hole_count", "hole_dia_mm"):
            out[k] = field(None, "REVIEW", "", "외형 없음" if not polys else "도면 단위를 알 수 없음")
        return out, notes
    def bbox(e):
        pts = [(p[0], p[1]) for p in e.get_points()]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
    x0, y0, x1, y1 = max((bbox(e) for e in polys), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    L, W = float(round((x1 - x0) * f, 4)), float(round((y1 - y0) * f, 4))
    # 치수 엔터티와 도형 대조
    dims = [d.get_measurement() * f for d in msp.query("DIMENSION")]
    has_w = any(abs(m - W) < 1e-6 for m in dims)
    out["length_mm"] = field(L, evidence=f"외형 폴리라인 가로 {x1 - x0:g} × {f:g}")
    out["width_mm"] = (field(W, evidence=f"외형 폴리라인 세로 {y1 - y0:g} × {f:g}") if has_w else
                       field(W, "OK", f"외형 폴리라인 세로 (치수 기입 없음)"))
    holes = [c for c in msp.query("CIRCLE") if x0 < c.dxf.center.x < x1 and y0 < c.dxf.center.y < y1]
    out["hole_count"] = field(len(holes), evidence=f"CIRCLE {len(holes)}개")
    dias = sorted({round(c.dxf.radius * 2 * f, 3) for c in holes})
    if not holes:
        out["hole_dia_mm"] = field(None, "OK", "홀 없음")
    elif len(dias) == 1:
        out["hole_dia_mm"] = field(dias[0], evidence="CIRCLE 반지름 × 2")
    else:
        out["hole_dia_mm"] = field(None, "REVIEW", f"지름 {dias}", "지름이 여러 가지 — 홀 종류별 분리 필요")
    return out, notes
