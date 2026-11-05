"""도면 PDF → 구조화 JSON (품번·재질·두께·치수·홀 개수·홀 지름).

입력 단계 (형식마다 다름)
  벡터 PDF : pdfplumber 로 글자(좌표)·사각형·곡선을 그대로 읽는다
  스캔 PDF : 300dpi 렌더링 → 기울기 보정 → tesseract OCR(kor+eng) 글자 좌표 + OpenCV 윤곽선(사각형·원)
판단 단계 (공통)
  표제란 라벨(품번·재질·두께·단위) 옆 값 / 외형 아래 = 가로 치수, 왼쪽 = 세로 치수 / 'n-Ød THRU' 홀 지시
교차 검증
  홀: 지시 개수 ≠ 도형 원 개수 → 검토 필요 / 치수: 가로÷세로 비율이 도형 비율과 15% 넘게 다르면 → 검토 필요(단위 오독 의심)
원칙: 읽지 못했거나 도면에 없는 값은 추측하지 않고 status=REVIEW 와 사유를 남긴다.
"""
from __future__ import annotations

import difflib
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0}
MATERIALS = {"SUS304", "SUS316L", "SS400", "SUS316", "SM490"}
LABELS = {"part_no": "품번", "name": "품명", "material": "재질", "thickness": "두께", "unit": "단위"}
FIELDS = ["part_no", "material", "thickness_mm", "length_mm", "width_mm", "hole_count", "hole_dia_mm"]
OCR_DPI = 300


@dataclass
class Tok:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.top + self.bottom) / 2


@dataclass
class Page:
    """형식과 무관한 공통 표현 (단위: pt, 원점 왼쪽 위)"""
    width: float
    height: float
    tokens: list[Tok]
    rects: list[tuple[float, float, float, float]]          # x0, top, x1, bottom
    circles: list[tuple[float, float, float]]               # cx, cy, diameter
    source: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 입력: 벡터
def load_vector(pdf: Path) -> Page | None:
    import pdfplumber
    with pdfplumber.open(pdf) as doc:
        p = doc.pages[0]
        words = p.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
        if not words:
            return None                                       # 텍스트 레이어 없음 → 스캔으로 처리
        toks = [Tok(w["text"], w["x0"], w["top"], w["x1"], w["bottom"]) for w in words]
        rects = [(r["x0"], r["top"], r["x1"], r["bottom"]) for r in p.rects]
        circles = []
        for c in p.curves:
            w, h = c["x1"] - c["x0"], c["bottom"] - c["top"]
            if w > 1 and abs(w - h) < 0.15 * max(w, h):
                circles.append(((c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2, (w + h) / 2))
        return Page(p.width, p.height, toks, rects, circles, "vector")


# ---------------------------------------------------------------- 입력: 스캔
def _deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """도곽(가장 큰 외곽선)의 기울기를 재서 바로 세운다."""
    import cv2
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    big = max(cnts, key=cv2.contourArea)
    (cx, cy), (w, h), ang = cv2.minAreaRect(big)
    # OpenCV 버전마다 각도 범위가 다르다(4.x: (0, 90], 5.x: [-90, 0)) → 항상 (-45, 45] 로 정규화
    # 처음엔 한쪽만 처리해 도면을 90° 돌려 버렸다 — AI_LOG 오류 #3
    while ang > 45:
        ang -= 90
    while ang <= -45:
        ang += 90
    if abs(ang) < 0.05:
        return gray, 0.0
    M = cv2.getRotationMatrix2D((gray.shape[1] / 2, gray.shape[0] / 2), ang, 1.0)
    return cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]), flags=cv2.INTER_CUBIC, borderValue=255), ang


def _page_image(pdf: Path):
    """스캔 PDF 안의 이미지를 그대로 꺼내 OCR_DPI 로 맞춘다.
    (pdftoppm 으로 다시 그리면 장당 13초 — 내장 JPEG 를 꺼내면 0.1초. AI_LOG 오류 #2)"""
    import cv2
    from pypdf import PdfReader
    page_w_pt = float(PdfReader(str(pdf)).pages[0].mediabox.width)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-f", "1", "-l", "1", "-all", str(pdf), f"{tmp}/i"], check=True)
        imgs = sorted(Path(tmp).glob("i-*"), key=lambda f: f.stat().st_size, reverse=True)
        if imgs:
            gray = cv2.imread(str(imgs[0]), cv2.IMREAD_GRAYSCALE)
        else:                                                  # 이미지가 없으면 렌더링
            subprocess.run(["pdftoppm", "-r", str(OCR_DPI), "-gray", "-png", str(pdf), f"{tmp}/p"], check=True)
            return cv2.imread(str(next(Path(tmp).glob("p*.png"))), cv2.IMREAD_GRAYSCALE)
    target_w = int(page_w_pt / 72 * OCR_DPI)
    if gray.shape[1] != target_w:
        gray = cv2.resize(gray, (target_w, int(gray.shape[0] * target_w / gray.shape[1])), interpolation=cv2.INTER_CUBIC)
    return gray


def _ocr(img, lang="kor+eng", psm=7, whitelist=None) -> str:
    import pytesseract
    cfg = f"--psm {psm}" + (f" -c tessedit_char_whitelist={whitelist}" if whitelist else "")
    return pytesseract.image_to_string(img, lang=lang, config=cfg).strip()


def _white_regions(bw):
    """선으로 둘러싸인 흰 영역의 경계 상자 (지시선이 테두리를 가로질러도 내부 영역은 하나로 남는다)."""
    import cv2
    inv = 255 - bw
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w > 60 and h > 30 and area / (w * h) > 0.85:
            out.append((x, y, x + w, y + h))
    return out


def load_scan(pdf: Path) -> Page:
    """스캔: 기울기 보정 → 흰 영역으로 외형·표제란 칸 찾기 → 필요한 자리만 잘라 OCR → 원은 윤곽선 원형도로 검출.
    (처음엔 페이지 전체 OCR + 사각형 윤곽 근사로 했다가 외형·표제란을 못 읽었다 — AI_LOG 오류 #1)"""
    import cv2
    gray = _page_image(pdf)
    gray, ang = _deskew(gray)
    s = 72.0 / OCR_DPI
    den = cv2.medianBlur(gray, 3)
    bw = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))   # 잡음으로 끊긴 선 잇기
    H_px, W_px = gray.shape
    regions = [r for r in _white_regions(bw) if (r[2] - r[0]) < W_px * 0.85]
    rects = [(r[0] * s, r[1] * s, r[2] * s, r[3] * s) for r in regions]
    toks: list[Tok] = []

    def add(text, box_px):
        if text:
            x0, y0, x1, y1 = box_px
            toks.append(Tok(text, x0 * s, y0 * s, x1 * s, y1 * s))

    def crop(x0, y0, x1, y1, pad=6):
        x0, y0, x1, y1 = (int(max(0, x0 + pad)), int(max(0, y0 + pad)), int(min(W_px, x1 - pad)), int(min(H_px, y1 - pad)))
        return den[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else None

    # 표제란: 오른쪽 아래, 높이가 낮고 가로로 긴 칸들. 같은 행의 왼쪽 칸 = 라벨, 오른쪽 칸 = 값
    cells = [r for r in regions if r[0] > W_px * 0.5 and r[1] > H_px * 0.6 and (r[3] - r[1]) < 40 / s]
    for c in cells:
        img = crop(*c)
        if img is not None:
            add(_ocr(img), c)

    # 외형 = 표제란 밖에서 가장 큰 흰 영역
    body = [r for r in regions if r not in cells]
    plate = max(body, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), default=None)
    if plate:
        px0, py0, px1, py1 = plate
        dim_wl = "0123456789.,cmCM "
        below = (px0 - 20, py1 + 25 / s * 0.6, px1 + 20, py1 + 70 / s)
        img = crop(*below, pad=2)
        if img is not None:
            add(_ocr(img, "eng", 7, dim_wl), below)
        left = (max(0, px0 - 150 / s), py0 + (py1 - py0) * 0.3, px0 - 45 / s * 0.9, py1 - (py1 - py0) * 0.3)
        img = crop(*left, pad=2)
        if img is not None:
            add(_ocr(img, "eng", 7, dim_wl), left)
        call = (px1, max(0, py0 - 80 / s), min(W_px, px1 + 230 / s), py0 - 5 / s)
        img = crop(*call, pad=2)
        if img is not None:
            add(_ocr(img, "eng", 6), call)

    # 원 = 원 테두리 안쪽의 흰 영역 (정사각 경계상자, 채움 비율 ≈ π/4).
    # 처음엔 테두리 윤곽의 원형도로 찾았는데, 지시선이 붙은 원은 윤곽이 합쳐져 빠졌다 — AI_LOG 오류 #4
    inv = 255 - bw
    n, _, stats, cents = cv2.connectedComponentsWithStats(inv, connectivity=4)
    merged = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 8 < w < 160 and abs(int(w) - int(h)) < 0.25 * max(w, h) and 0.55 < area / (w * h) < 0.9:
            merged.append((float(cents[i][0]) * s, float(cents[i][1]) * s, (w + h) / 2 * s))
    return Page(W_px * s, H_px * s, toks, rects, merged, "scan",
                [f"기울기 보정 {ang:+.2f}°", f"OCR 영역 {len(toks)}개"])


# ---------------------------------------------------------------- 판단
def parse_number(text: str) -> float | None:
    t = text.replace(",", ".").replace("O", "0").replace("o", "0").strip()
    return float(t) if re.fullmatch(r"\d+(\.\d+)?", t) else None


def parse_measure(text: str, default_unit: str | None) -> tuple[float | None, str]:
    """'1260' / '0.96 m' / '1 cm' / '80cm' → (mm, 설명)"""
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?\s*", text.replace("O", "0"), re.I)
    if not m:
        return None, f"숫자·단위로 해석 불가: {text!r}"
    v = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").lower() or default_unit
    if unit not in UNITS:
        return None, f"단위 없음·기본 단위도 없음: {text!r}"
    return round(v * UNITS[unit], 4), f"{text.strip()} → {v} {unit}"


def same_row(a: Tok, b: Tok, tol: float) -> bool:
    return abs(a.cy - b.cy) < tol


def find_label(page: Page, label: str, region) -> Tok | None:
    x_min, y_min = region
    cands = [t for t in page.tokens if t.x0 >= x_min and t.top >= y_min]
    exact = [t for t in cands if t.text == label]
    if exact:
        return exact[0]
    best = max(cands, key=lambda t: difflib.SequenceMatcher(None, t.text, label).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, best.text, label).ratio() >= 0.5 and len(best.text) <= len(label) + 1:
        return best
    return None


def title_value(page: Page, label: str, region) -> tuple[str | None, str]:
    lab = find_label(page, label, region)
    if lab is None:
        return None, f"표제란에서 '{label}' 라벨을 찾지 못함"
    row_h = max(6.0, lab.bottom - lab.top)
    vals = sorted([t for t in page.tokens if same_row(t, lab, row_h * 0.8) and lab.x1 - 2 <= t.x0 < lab.x1 + 260 and t is not lab],
                  key=lambda t: t.x0)
    return " ".join(t.text for t in vals).strip(), f"라벨 '{lab.text}' 오른쪽"


def plate_rect(page: Page, title_region):
    tx, ty = title_region
    cands = [r for r in page.rects
             if (r[2] - r[0]) < page.width * 0.85 and not (r[0] >= tx - 5 and r[1] >= ty - 5)
             and (r[2] - r[0]) > 40 and (r[3] - r[1]) > 40]
    return max(cands, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), default=None)


def join_line(toks: list[Tok]) -> str:
    return " ".join(t.text for t in sorted(toks, key=lambda t: t.x0))


def field(value=None, status="OK", evidence="", reason=None):
    return {"value": value, "status": status, "evidence": evidence, "reason": reason}


def interpret(page: Page) -> dict:
    out: dict = {}
    # 표제란 영역 = 페이지 오른쪽 아래 (라벨 '도면번호' 위치로 잡고, 못 찾으면 고정 비율)
    anchor = find_label(page, "도면번호", (page.width * 0.5, page.height * 0.5))
    region = (anchor.x0 - 5, anchor.top - 5) if anchor else (page.width * 0.6, page.height * 0.65)

    unit_txt, _ = title_value(page, "단위", region)
    default_unit = (unit_txt or "").lower().strip() if (unit_txt or "").lower().strip() in UNITS else None

    pn, ev = title_value(page, "품번", region)
    if pn and re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{1,20}", pn.replace(" ", "")):
        out["part_no"] = field(pn.replace(" ", ""), evidence=ev)
    else:
        out["part_no"] = field(None, "REVIEW", ev, f"품번 판독 불가: {pn!r}")
    nm, ev = title_value(page, "품명", region)
    if nm and re.search(r"[가-힣]", nm):
        out["name"] = field(nm, evidence=ev)
    else:
        out["name"] = field(None, "REVIEW", ev, f"품명 판독 불가: {nm!r}")

    mat, ev = title_value(page, "재질", region)
    m = (mat or "").upper().replace(" ", "")
    if m:   # OCR 혼동 보정은 영문 접두부의 5↔S, 숫자부의 O↔0 만. 결과는 등록 재질 목록에 있을 때만 인정
        m = re.sub(r"^[5S]U[5S]", "SUS", m)
        m = re.sub(r"^[5S]{2}(?=\d)", "SS", m)
        m = re.sub(r"(?<=\d)O|O(?=\d)", "0", m)
    if m in MATERIALS:
        out["material"] = field(m, evidence=ev)
    else:
        out["material"] = field(None, "REVIEW", ev, f"등록되지 않았거나 판독 불가한 재질: {mat!r}")

    thk, ev = title_value(page, "두께", region)
    if not thk:
        out["thickness_mm"] = field(None, "REVIEW", ev, "두께 칸이 비어 있음 — 추측하지 않음")
    else:
        v, how = parse_measure(thk.lstrip("tT"), default_unit)
        out["thickness_mm"] = field(v, "OK" if v is not None else "REVIEW", f"{ev}: {how}", None if v is not None else how)

    plate = plate_rect(page, region)
    if plate is None:
        for k in ("length_mm", "width_mm", "hole_count", "hole_dia_mm"):
            out[k] = field(None, "REVIEW", "", "부품 외형(사각형)을 찾지 못함")
        return out
    px0, ptop, px1, pbot = plate
    out["_plate_pt"] = [round(v, 1) for v in plate]

    # 가로 치수: 외형 아래 60pt 안, 외형 가로 범위 안
    below = [t for t in page.tokens if pbot < t.top < pbot + 60 and px0 - 10 < t.cx < px1 + 10]
    left = [t for t in page.tokens if px0 - 140 < t.x1 < px0 - 5 and ptop - 5 < t.cy < pbot + 5]
    for key, toks, label in (("length_mm", below, "가로(외형 아래)"), ("width_mm", left, "세로(외형 왼쪽)")):
        txt = join_line([t for t in toks if re.search(r"\d", t.text) or t.text.lower() in UNITS])
        if not txt:
            out[key] = field(None, "REVIEW", label, f"{label} 치수가 도면에 없음 — 추측하지 않음")
            continue
        v, how = parse_measure(txt, default_unit)
        out[key] = field(v, "OK" if v is not None else "REVIEW", f"{label}: {how}", None if v is not None else how)

    # 치수 비율 ↔ 도형 비율 (단위 오독 탐지)
    L, Wd = out["length_mm"]["value"], out["width_mm"]["value"]
    if L and Wd:
        drawn = (px1 - px0) / (pbot - ptop)
        if abs((L / Wd) / drawn - 1) > 0.15:
            for k in ("length_mm", "width_mm"):
                out[k].update(status="REVIEW", reason=f"치수 비율 {L / Wd:.2f} ≠ 도형 비율 {drawn:.2f} — 단위 오독 의심")

    # 홀: 지시선 문자 + 외형 안 원 개수
    inside = [c for c in page.circles if px0 + 2 < c[0] < px1 - 2 and ptop + 2 < c[1] < pbot - 2]
    callout = None
    for t in page.tokens:
        if "THRU" in t.text.upper() or "관통" in t.text:
            line = join_line([u for u in page.tokens if same_row(u, t, 6) and t.x0 - 140 < u.x0 <= t.x0])
            mm = re.search(r"(\d+)\s*[-–~]\s*[ØøΦφ⌀@©®0OQ]?\s*(\d+(?:\.\d+)?)", line.replace(" ", ""))
            if mm:
                callout = (int(mm.group(1)), float(mm.group(2)), line)
                break
    n_geo = len(inside)
    if callout:
        n, d, line = callout
        if n == n_geo:
            out["hole_count"] = field(n, evidence=f"지시 '{line}' = 도형 원 {n_geo}개")
        else:
            out["hole_count"] = field(None, "REVIEW", f"지시 '{line}'", f"지시 {n}개 ≠ 도형 원 {n_geo}개")
        out["hole_dia_mm"] = field(d, evidence=f"지시 '{line}'")
    else:
        out["hole_count"] = field(n_geo, evidence=f"지시 없음, 도형 원 {n_geo}개")
        out["hole_dia_mm"] = (field(None, "OK", "홀 없음 (해당 없음)") if n_geo == 0 else
                              field(None, "REVIEW", f"도형 원 {n_geo}개", "홀 지름 지시가 없음 — 도형 크기로 추측하지 않음(실척 아님)"))
    return out


def check_part_master(res: dict, master: set[str]) -> None:
    """품번 마스터(ERP) 대조: 없으면 검토 필요 + 가장 비슷한 후보 제안 (자동 치환하지 않음)."""
    f = res["part_no"]
    if f["status"] != "OK" or f["value"] in master:
        return
    cand = difflib.get_close_matches(f["value"], sorted(master), n=1, cutoff=0.8)
    f.update(status="REVIEW", reason=f"품번 마스터에 없음: {f['value']!r}" + (f" — 후보 {cand[0]!r} (확인 필요)" if cand else ""),
             candidate=cand[0] if cand else None)


def extract(pdf: Path, part_master: set[str] | None = None) -> dict:
    t0 = time.perf_counter()
    if pdf.suffix.lower() == ".dxf":
        from .dxf import interpret_dxf
        res, notes = interpret_dxf(pdf)
        page = Page(0, 0, [], [], [], "dxf", notes)
    else:
        page = load_vector(pdf)
        if page is None:
            page = load_scan(pdf)
        res = interpret(page)
    if part_master is not None:
        check_part_master(res, part_master)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    review = [k for k in FIELDS if res.get(k, {}).get("status") == "REVIEW"]
    return {"drawing": pdf.stem.split("_")[0], "file": pdf.name, "source": page.source, "processing_ms": ms,
            "notes": page.notes, "fields": {k: res[k] for k in ["part_no", "name"] + FIELDS[1:] if k in res},
            "review_fields": review, "status": "NEEDS_REVIEW" if review else "OK"}


def to_bom_item(r: dict) -> dict:
    """과제 03 입력 형식(items 한 줄)으로 변환. 검토 필요 필드는 비워 둔다."""
    f = r["fields"]
    item = {"part_no": f["part_no"]["value"], "name": f["name"]["value"], "kind": "PART", "material": f["material"]["value"]}
    if f["thickness_mm"]["status"] == "OK" and f["thickness_mm"]["value"]:
        item["thickness"] = {"value": f["thickness_mm"]["value"], "unit": "mm"}
    return item
