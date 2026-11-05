"""합성 부품 도면 10장 생성 (벡터 PDF 5 + 스캔 PDF 5).

정답(fixtures/answers)은 이 파일보다 먼저 커밋했다. 여기서는 '도면에 어떻게 적혀 있는가'(표기 문자열·배치)만 정한다.
스캔 PDF = 벡터 도면을 200dpi 회색조로 렌더링 → 기울기·잡음·흐림·JPEG 압축 → 이미지 PDF (텍스트 레이어 없음)
"""
from __future__ import annotations

import io
import math
import random
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
OUT = HERE / "drawings"
FONT = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
pdfmetrics.registerFont(TTFont("Nanum", FONT))
W, H = landscape(A4)

# 표기 방식. dims 는 도면에 '적힌 문자열', None 이면 그 치수를 그리지 않음
SPECS = {
    "D01": dict(fmt="vector", part="PL-SHELL", name="쉘 판", mat="SUS304", thk="6", unit="mm", L=1260, Wd=1000,
                h_text="1260", v_text="1000", holes=(4, 12, "corners"), callout="4-Ø12 THRU"),
    "D02": dict(fmt="vector", part="PL-JKT", name="자켓 판", mat="SS400", thk="4.5", unit="mm", L=1300, Wd=800,
                h_text="1300", v_text="800", holes=(6, 10, "grid3x2"), callout=None),
    "D03": dict(fmt="vector", part="LUG", name="러그", mat="SS400", thk="1 cm", unit="mm", L=150, Wd=100,
                h_text="150", v_text="100", holes=(2, 18, "row"), callout="2-Ø18 THRU"),
    "D04": dict(fmt="vector", part="HD-BLANK", name="경판 블랭크", mat="SUS304", thk="8", unit="mm", L=960, Wd=960,
                h_text="0.96 m", v_text="960", holes=(1, 50, "center"), callout="1-Ø50 THRU"),
    "D05": dict(fmt="vector", part="BRK-01", name="브래킷", mat="SUS316L", thk="", unit="mm", L=300, Wd=200,
                h_text="300", v_text="200", holes=(4, 14, "corners"), callout="4-Ø14 THRU"),
    "D06": dict(fmt="scan", part="PL-SHELL2", name="쉘 판", mat="SUS316L", thk="8", unit="mm", L=1500, Wd=1200,
                h_text="1500", v_text="1200", holes=(8, 16, "grid4x2"), callout="8-Ø16 THRU", angle=0.4, noise=8),
    "D07": dict(fmt="scan", part="FL-PLATE", name="플랜지 판", mat="SUS304", thk="12", unit="mm", L=400, Wd=400,
                h_text="400", v_text="400", holes=(8, 18, "bolt"), callout="8-Ø18 THRU", angle=-0.6, noise=10),
    "D08": dict(fmt="scan", part="BASE-01", name="베이스 판", mat="SS400", thk="16", unit="mm", L=1200, Wd=800,
                h_text="1.2 m", v_text="80 cm", holes=(4, 22, "corners"), callout="4-Ø22 THRU", angle=0.8, noise=10),
    "D09": dict(fmt="scan", part="COVER-01", name="커버 판", mat="SUS304", thk="3", unit="mm", L=600, Wd=400,
                h_text="600", v_text=None, holes=(6, 8, "grid3x2"), callout="6-Ø8 THRU", angle=-0.3, noise=8),
    "D10": dict(fmt="scan", part="GUSSET", name="거셋", mat="SS400", thk="9", unit="mm", L=250, Wd=150,
                h_text="250", v_text="150", holes=(0, 0, None), callout=None, angle=1.5, noise=18),
}


def hole_centers(n, pattern, x0, y0, w, h):
    if pattern == "corners":
        return [(x0 + w * fx, y0 + h * fy) for fx, fy in ((.12, .15), (.88, .15), (.12, .85), (.88, .85))][:n]
    if pattern == "row":
        return [(x0 + w * (i + 1) / (n + 1), y0 + h / 2) for i in range(n)]
    if pattern == "center":
        return [(x0 + w / 2, y0 + h / 2)]
    if pattern and pattern.startswith("grid"):
        cols, rows = map(int, pattern[4:].split("x"))
        return [(x0 + w * (c + 1) / (cols + 1), y0 + h * (r + 1) / (rows + 1)) for r in range(rows) for c in range(cols)]
    if pattern == "bolt":
        r = min(w, h) * 0.36
        return [(x0 + w / 2 + r * math.cos(2 * math.pi * i / n), y0 + h / 2 + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return []


def arrow(c, x, y, dx, dy):
    L, a = 6, math.atan2(dy, dx)
    for s in (0.35, -0.35):
        c.line(x, y, x - L * math.cos(a + s), y - L * math.sin(a + s))


def draw_vector(did: str, s: dict, path: Path):
    c = canvas.Canvas(str(path), pagesize=(W, H))
    c.setTitle(f"DWG-{did}")
    c.setLineWidth(1.2)
    c.rect(20, 20, W - 40, H - 40)                       # 도곽
    # 표제란
    rows = [("도면번호", f"DWG-{did}"), ("품번", s["part"]), ("품명", s["name"]), ("재질", s["mat"]),
            ("두께", s["thk"]), ("단위", s["unit"]), ("개정", "A")]
    tx, ty, cw, rh = W - 20 - 300, 20, (90, 210), 20
    c.setLineWidth(0.8)
    for i, (k, v) in enumerate(reversed(rows)):
        y = ty + i * rh
        c.rect(tx, y, cw[0], rh)
        c.rect(tx + cw[0], y, cw[1], rh)
        c.setFont("Nanum", 11)
        c.drawString(tx + 8, y + 6, k)
        c.setFont("Nanum", 12)
        c.drawString(tx + cw[0] + 10, y + 5.5, v)
    # 주기 (방해 숫자)
    c.setFont("Nanum", 10)
    for i, line in enumerate(["주기", "1. 지시 없는 모따기 C1", "2. 표면 거칠기 Ra 3.2", "3. 작성일 2026-03-01"]):
        c.drawString(40, H - 50 - i * 14, line)
    # 부품 외형 (배율 맞춤)
    box_x, box_y, box_w, box_h = 160, 150, 330, 300
    k = min(box_w / s["L"], box_h / s["Wd"])
    pw, ph = s["L"] * k, s["Wd"] * k
    px, py = box_x + (box_w - pw) / 2, box_y + (box_h - ph) / 2
    c.setLineWidth(1.6)
    c.rect(px, py, pw, ph)
    c.setLineWidth(1.0)
    n, dia, pat = s["holes"]
    centers = hole_centers(n, pat, px, py, pw, ph)
    for (hx, hy) in centers:
        c.circle(hx, hy, max(2.5, dia * k / 2))
    # 가로 치수
    c.setLineWidth(0.6)
    yd = py - 40
    c.line(px, py - 5, px, yd - 6)
    c.line(px + pw, py - 5, px + pw, yd - 6)
    c.line(px, yd, px + pw, yd)
    arrow(c, px, yd, -1, 0)
    arrow(c, px + pw, yd, 1, 0)
    c.setFont("Nanum", 13)
    c.drawCentredString(px + pw / 2, yd - 17, s["h_text"])
    # 세로 치수 (글자는 가로로 — 한방향 치수 기입)
    if s["v_text"]:
        xd = px - 40
        c.line(px - 5, py, xd - 6, py)
        c.line(px - 5, py + ph, xd - 6, py + ph)
        c.line(xd, py, xd, py + ph)
        arrow(c, xd, py, 0, -1)
        arrow(c, xd, py + ph, 0, 1)
        c.drawRightString(xd - 8, py + ph / 2 - 4, s["v_text"])
    # 홀 지시선
    if s["callout"] and centers:
        hx, hy = max(centers, key=lambda p: (p[1], p[0]))
        lx, ly = px + pw + 25, py + ph + 25
        c.line(hx, hy, lx, ly)
        c.line(lx, ly, lx + 80, ly)
        c.drawString(lx + 4, ly + 4, s["callout"])
    c.showPage()
    c.save()


def to_scan(vector_pdf: Path, out_pdf: Path, angle: float, noise: float, seed: int):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", str(vector_pdf), f"{tmp}/p"], check=True)
        img = Image.open(next(Path(tmp).glob("p*.png"))).convert("L")
    img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    rng = np.random.default_rng(seed)
    a = np.asarray(img, dtype=np.float32) + rng.normal(0, noise, img.size[::-1])
    speck = rng.random(a.shape) < 0.0008
    a[speck] = 0
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=55)                     # 스캐너 압축
    Image.open(buf).save(out_pdf, "PDF", resolution=200)


def render_all(out: Path = OUT):
    out.mkdir(parents=True, exist_ok=True)
    for i, (did, s) in enumerate(SPECS.items()):
        target = out / f"{did}_{s['fmt']}.pdf"
        if s["fmt"] == "vector":
            draw_vector(did, s, target)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                v = Path(tmp) / "v.pdf"
                draw_vector(did, s, v)
                to_scan(v, target, s["angle"], s["noise"], seed=100 + i)
    from .dxf import make_dxf                       # DWG 대응 경로 시연용 DXF 2장 (평가 10장과 별도)
    for did in ("D01", "D05"):
        make_dxf(out / f"{did}_dxf.dxf", {**SPECS[did], "dwg": f"DWG-{did}"})
    return sorted(out.glob("D*.pdf"))


if __name__ == "__main__":
    for p in render_all():
        print(p.name)
