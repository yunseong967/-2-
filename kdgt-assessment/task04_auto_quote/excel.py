"""견적 Excel 출력 (openpyxl). 시트 4개: 견적서 / 산출근거 / 단가출처 / 계산규칙"""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import rules as R
from .quote import CATEGORY_LABEL

THIN = Side(style="thin", color="C9CED6")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD = PatternFill("solid", fgColor="E8EDF7")
WARN = PatternFill("solid", fgColor="FDECEA")
BOLD = Font(bold=True)
MONEY = '#,##0"원"'


def _table(ws, row, headers, rows, widths=None, money_cols=()):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row, c, h)
        cell.font, cell.fill, cell.border = BOLD, HEAD, BOX
    for r, values in enumerate(rows, row + 1):
        for c, v in enumerate(values, 1):
            cell = ws.cell(r, c, v)
            cell.border = BOX
            if c in money_cols and isinstance(v, int):
                cell.number_format = MONEY
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
    return row + len(rows) + 1


def build(q: dict) -> bytes:
    wb = Workbook()

    # 1) 견적서 ------------------------------------------------------------
    ws = wb.active
    ws.title = "견적서"
    ws["A1"] = "견 적 서"
    ws["A1"].font = Font(bold=True, size=18)
    status = "발행" if q["status"] == "ISSUED" else f"검토 필요 — 미확정 항목 {q['review_count']}건 (발행 불가)"
    info = [("견적번호", q["quote_no"]), ("견적일", q["quote_date"]), ("고객", q["customer"]),
            ("제품", f"{q['product_no']} (도면 개정 {q['revision']})"), ("수량", f"{q['qty']} 대"),
            ("상태", status), ("계산 규칙", q["rules_version"]),
            ("단가표 버전", ", ".join(f"{k} {v or '없음'}" for k, v in q["price_versions"].items()))]
    for i, (k, v) in enumerate(info, 3):
        ws.cell(i, 1, k).font = BOLD
        ws.cell(i, 2, v)
        if k == "상태" and q["status"] != "ISSUED":
            ws.cell(i, 2).fill = WARN
    row = 3 + len(info) + 1
    summary = [("재료비", q["material_total"]), ("가공비", q["process_total"]), ("외주비", q["outsource_total"]),
               ("소계", q["subtotal"]), ("단수 조정 (천원 미만 절사)", q["rounding_adj"]),
               ("공급가액", q["supply"]), ("부가세 (10%, 원 미만 절사)", q["vat"]), ("합계", q["total"])]
    row = _table(ws, row, ["구분", "금액"], summary, money_cols=(2,))
    for r in range(row - 3, row):
        ws.cell(r - 0, 1).font = BOLD
    ws.cell(row - 1, 2).font = Font(bold=True, size=13)
    if q["status"] != "ISSUED":
        ws.cell(row + 1, 1, "※ 검토 필요 항목은 금액에 포함되지 않았습니다. '산출근거' 시트의 붉은 줄을 확인하세요.").font = Font(color="B42318")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 52

    # 2) 산출근거 ----------------------------------------------------------
    ws = wb.create_sheet("산출근거")
    headers = ["#", "구분", "품번", "품명", "단가 키", "기준량", "단위", "단가(원)", "단가표", "버전", "계산식", "금액", "상태", "검토 사유"]
    rows = [[i, CATEGORY_LABEL[l["category"]], l["item"], l["item_name"], l["cost_key"],
             float(l["basis_qty"]) if l["basis_qty"] else None, l["basis_unit"],
             float(l["unit_price"]) if l["unit_price"] else None, l["price_table"], l["price_version"],
             l["formula"], l["amount"], "확정" if l["status"] == "OK" else "검토 필요", l["review_reason"]]
            for i, l in enumerate(q["lines"], 1)]
    end = _table(ws, 1, headers, rows, [5, 8, 12, 16, 14, 10, 6, 11, 11, 9, 80, 14, 10, 50], money_cols=(12,))
    for r, l in enumerate(q["lines"], 2):
        if l["status"] != "OK":
            for c in range(1, len(headers) + 1):
                ws.cell(r, c).fill = WARN
        ws.cell(r, 8).number_format = "#,##0"
    ws.cell(end + 1, 11, "합계 (확정 금액)").font = BOLD
    ws.cell(end + 1, 12, f"=SUM(L2:L{end - 1})").number_format = MONEY
    ws.freeze_panes = "A2"

    # 3) 단가출처 ----------------------------------------------------------
    ws = wb.create_sheet("단가출처")
    _table(ws, 1, ["단가표", "버전", "적용 시작일", "출처"],
           [[s["table_type"], s["label"], s["effective_from"], s["source"]] for s in q["price_sources"]],
           [12, 10, 12, 60])

    # 4) 계산규칙 ----------------------------------------------------------
    ws = wb.create_sheet("계산규칙")
    ws["A1"] = f"계산 규칙 {R.RULES_VERSION}"
    ws["A1"].font = BOLD
    for i, line in enumerate(R.__doc__.strip().splitlines()[1:], 3):
        ws.cell(i, 1, line)
    ws.cell(i + 2, 1, "비중(g/cm³): " + ", ".join(f"{k} {v}" for k, v in R.DENSITY.items()))
    ws.cell(i + 3, 1, "수율: " + ", ".join(f"{k} {v}" for k, v in R.YIELD.items()))
    ws.column_dimensions["A"].width = 110

    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="center", wrap_text=True)
        # 인쇄: 가로 폭을 한 페이지에 맞춤 (처음엔 견적서가 두 장으로 쪼개져 인쇄됨 — AI_LOG 오류 #1)
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.print_options.horizontalCentered = True
    wb["산출근거"].page_setup.orientation = "landscape"
    wb["산출근거"].print_title_rows = "1:1"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
