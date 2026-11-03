"""시연·검증용 시나리오. 각 단계에 '정답'(기대 HTTP 상태와 오류 코드)을 함께 적어 둔다.

흐름 (반응기 쉘 프로젝트 P2026-001)
  자재 입고 4건 → 검사(합격 2, 부적합 1, 미검사 1)
  → 분할 → 병합 → 생산 2건 (+ 미검사/부적합 자재 투입 차단)
  → 제품 검사 → 부분 출하 / 중복 요청 / 재고 초과 / 미검사 제품 출하 차단
  → 원자재 재검사 부적합 → 하위 LOT 보류 + 영향 출하 목록
  → 제품만 재검사 합격해도 상위 자재 부적합이면 출하 차단
  → 원자재 재검사 합격 → 하위 LOT 보류 해제(원래 상태로 복원) → 출하
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field


def http_client(base_url: str):
    def call(method: str, path: str, body: dict | None = None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    return call


@dataclass
class Step:
    title: str
    method: str
    path: str
    body: dict | None = None
    expect_status: int = 201
    expect_error: str | None = None
    check: callable = None          # (응답 json) -> (ok: bool, 설명: str)
    result: dict = field(default_factory=dict)


def _ok(cond, msg):
    return bool(cond), msg


R = "/api/receipts"
I = "/api/inspections"
SHIP = "/api/shipments"

STEPS: list[Step] = [
    # ---- 입고
    Step("자재 입고 M-A SUS304 6t 1000KG", "POST", R,
         {"lot_id": "M-A", "item": "SUS304 6t", "qty": 1000, "unit": "KG", "project_id": "P2026-001"}),
    Step("자재 입고 M-B SUS304 6t 400KG", "POST", R,
         {"lot_id": "M-B", "item": "SUS304 6t", "qty": 400, "unit": "KG", "project_id": "P2026-001"}),
    Step("자재 입고 M-C SUS316L 8t 500KG (검사 안 함)", "POST", R,
         {"lot_id": "M-C", "item": "SUS316L 8t", "qty": 500, "unit": "KG", "project_id": "P2026-001"}),
    Step("자재 입고 M-D SS400 10t 300KG", "POST", R,
         {"lot_id": "M-D", "item": "SS400 10t", "qty": 300, "unit": "KG", "project_id": "P2026-001"}),
    Step("같은 LOT 번호로 다시 입고 → 거부", "POST", R,
         {"lot_id": "M-A", "item": "SUS304 6t", "qty": 10, "unit": "KG"}, 409, "DUPLICATE_LOT"),
    # ---- 검사
    Step("M-A 수입검사 합격", "POST", I, {"lot_id": "M-A", "result": "PASS", "inspector": "QC김"}),
    Step("M-B 수입검사 합격", "POST", I, {"lot_id": "M-B", "result": "PASS", "inspector": "QC김"}),
    Step("M-D 수입검사 부적합(두께 미달)", "POST", I,
         {"lot_id": "M-D", "result": "FAIL", "inspector": "QC김", "note": "두께 9.6mm 실측"}),
    # ---- 분할 / 병합
    Step("M-A 분할 300 + 200 (남은 500은 M-A)", "POST", "/api/splits",
         {"lot_id": "M-A", "quantities": [300, 200]},
         check=lambda r: _ok([c["lot_id"] for c in r["children"]] == ["M-A-S1", "M-A-S2"]
                             and r["parent"]["qty"] == 500 and r["children"][0]["status"] == "PASS",
                             "자식 M-A-S1(300)/M-A-S2(200) 합격 상태 상속, 부모 500")),
    Step("분할 합계가 재고 초과 → 거부", "POST", "/api/splits",
         {"lot_id": "M-A", "quantities": [400, 200]}, 409, "INSUFFICIENT_QTY"),
    Step("M-B + M-A-S2 병합 → MRG-1 (600KG)", "POST", "/api/merges",
         {"lot_ids": ["M-B", "M-A-S2"], "new_lot_id": "MRG-1"},
         check=lambda r: _ok(r["qty"] == 600 and r["status"] == "PASS", "600KG, 합격")),
    Step("다른 품목(M-C) 병합 → 거부", "POST", "/api/merges",
         {"lot_ids": ["MRG-1", "M-C"], "new_lot_id": "MRG-X"}, 409, "ITEM_MISMATCH"),
    # ---- 생산
    Step("제품 P-001 반응기 쉘 2EA 생산 (M-A-S1 250 + MRG-1 100 투입)", "POST", "/api/productions",
         {"lot_id": "P-001", "item": "반응기 쉘 500L", "qty": 2, "unit": "EA", "project_id": "P2026-001",
          "consumes": [{"lot_id": "M-A-S1", "qty": 250}, {"lot_id": "MRG-1", "qty": 100}]}),
    Step("제품 P-002 3EA 생산 (M-A 200 투입)", "POST", "/api/productions",
         {"lot_id": "P-002", "item": "반응기 쉘 500L", "qty": 3, "unit": "EA", "project_id": "P2026-001",
          "consumes": [{"lot_id": "M-A", "qty": 200}]}),
    Step("미검사 자재 M-C 투입 → 차단", "POST", "/api/productions",
         {"lot_id": "P-X1", "item": "교반기 축", "qty": 1, "unit": "EA",
          "consumes": [{"lot_id": "M-C", "qty": 10}]}, 409, "STATUS_BLOCKED"),
    Step("부적합 자재 M-D 투입 → 차단", "POST", "/api/productions",
         {"lot_id": "P-X2", "item": "교반기 축", "qty": 1, "unit": "EA",
          "consumes": [{"lot_id": "M-D", "qty": 10}]}, 409, "STATUS_BLOCKED"),
    Step("제품 P-003 1EA 생산 (MRG-1 50 투입, 제품검사 안 함)", "POST", "/api/productions",
         {"lot_id": "P-003", "item": "반응기 쉘 500L", "qty": 1, "unit": "EA",
          "consumes": [{"lot_id": "MRG-1", "qty": 50}]}),
    # ---- 제품 검사 / 출하
    Step("P-001 제품검사 합격", "POST", I, {"lot_id": "P-001", "result": "PASS", "inspector": "QC이"}),
    Step("P-002 제품검사 합격", "POST", I, {"lot_id": "P-002", "result": "PASS", "inspector": "QC이"}),
    Step("자재 LOT 직접 출하 → 차단", "POST", SHIP,
         {"request_id": "REQ-000", "lot_id": "M-A", "qty": 1, "customer": "한빛제약"}, 409, "KIND_INVALID"),
    Step("P-001 부분 출하 1EA (REQ-001)", "POST", SHIP,
         {"request_id": "REQ-001", "lot_id": "P-001", "qty": 1, "customer": "한빛제약"},
         check=lambda r: _ok(len(r["evidence"]) >= 3,
                             f"검사 근거 {len(r['evidence'])}건 저장: "
                             + ", ".join(f"{e['lot_id']}#{e['seq']}" for e in r["evidence"]))),
    Step("같은 요청 REQ-001 재전송 → 출하 안 늘어남(200, replayed)", "POST", SHIP,
         {"request_id": "REQ-001", "lot_id": "P-001", "qty": 1, "customer": "한빛제약"}, 200,
         check=lambda r: _ok(r["replayed"] is True, "replayed=true, 같은 출하번호 반환")),
    Step("REQ-001 을 다른 수량으로 재사용 → 거부", "POST", SHIP,
         {"request_id": "REQ-001", "lot_id": "P-001", "qty": 2, "customer": "한빛제약"}, 409,
         "REQUEST_ID_CONFLICT"),
    Step("P-001 나머지 1EA 출하 (REQ-002)", "POST", SHIP,
         {"request_id": "REQ-002", "lot_id": "P-001", "qty": 1, "customer": "한빛제약"}),
    Step("P-001 재고 0 상태에서 추가 출하 → 차단", "POST", SHIP,
         {"request_id": "REQ-003", "lot_id": "P-001", "qty": 1, "customer": "한빛제약"}, 409,
         "INSUFFICIENT_QTY"),
    Step("P-002 2EA 출하 (REQ-004)", "POST", SHIP,
         {"request_id": "REQ-004", "lot_id": "P-002", "qty": 2, "customer": "대성바이오"}),
    Step("미검사 제품 P-003 출하 → 차단", "POST", SHIP,
         {"request_id": "REQ-005", "lot_id": "P-003", "qty": 1, "customer": "대성바이오"}, 409,
         "STATUS_BLOCKED"),
    # ---- 재검사
    Step("M-A 재검사 부적합 (MTC 성분 불일치) → 하위 보류 + 영향 출하", "POST", I,
         {"lot_id": "M-A", "result": "FAIL", "inspector": "QC박", "note": "MTC Cr 함량 불일치"},
         check=lambda r: _ok(
             r["reinspection"] and set(r["held_lots"]) == {"M-A-S1", "M-A-S2", "MRG-1", "P-001", "P-002", "P-003"}
             and [s["request_id"] for s in r["affected_shipments"]] == ["REQ-001", "REQ-002", "REQ-004"],
             "보류: " + ", ".join(r["held_lots"]) + " / 영향 출하: "
             + ", ".join(s["request_id"] for s in r["affected_shipments"]))),
    Step("보류된 P-002 출하 → 차단", "POST", SHIP,
         {"request_id": "REQ-006", "lot_id": "P-002", "qty": 1, "customer": "대성바이오"}, 409,
         "STATUS_BLOCKED"),
    Step("P-002 개별 재검사 합격 (제품 성분분석 적합)", "POST", I,
         {"lot_id": "P-002", "result": "PASS", "inspector": "QC박", "note": "제품 성분분석 적합"}),
    Step("P-002 는 합격이지만 투입 자재 M-A 가 부적합 → 출하 차단", "POST", SHIP,
         {"request_id": "REQ-006", "lot_id": "P-002", "qty": 1, "customer": "대성바이오"}, 409,
         "UPSTREAM_FAIL"),
    Step("부적합 근거가 있는 MRG-1 투입 생산 → 차단", "POST", "/api/productions",
         {"lot_id": "P-X3", "item": "반응기 쉘 500L", "qty": 1, "unit": "EA",
          "consumes": [{"lot_id": "MRG-1", "qty": 10}]}, 409, "STATUS_BLOCKED"),
    Step("M-A 재검사 #3 합격 (MTC 재발행·성분 재분석 적합) → 보류 해제", "POST", I,
         {"lot_id": "M-A", "result": "PASS", "inspector": "QC박", "note": "MTC 재발행, 재분석 적합"},
         check=lambda r: _ok(set(r["released_lots"]) == {"M-A-S1", "M-A-S2", "MRG-1", "P-001", "P-003"},
                             "해제: " + ", ".join(r["released_lots"]) + " (P-003 은 원래 미검사로 복원)")),
    Step("P-003 은 미검사로 복원되어 여전히 출하 차단", "POST", SHIP,
         {"request_id": "REQ-007", "lot_id": "P-003", "qty": 1, "customer": "대성바이오"}, 409,
         "STATUS_BLOCKED"),
    Step("P-002 나머지 1EA 출하 (REQ-006 재시도)", "POST", SHIP,
         {"request_id": "REQ-006", "lot_id": "P-002", "qty": 1, "customer": "대성바이오"},
         check=lambda r: _ok(all(e["result"] == "PASS" for e in r["evidence"]),
                             "근거: " + ", ".join(f"{e['lot_id']}#{e['seq']} {e['result']}" for e in r["evidence"]))),
    # ---- 조회
    Step("정방향 추적 M-A → 영향 출하 4건", "GET", "/api/trace/forward/M-A", expect_status=200,
         check=lambda r: _ok([s["request_id"] for s in r["shipments"]] == ["REQ-001", "REQ-002", "REQ-004", "REQ-006"],
                             "출하: " + ", ".join(s["request_id"] for s in r["shipments"]))),
    Step("역방향 추적 출하#1 → 원자재 M-A, M-B", "GET", "/api/shipments/1/trace", expect_status=200,
         check=lambda r: _ok(sorted(r["raw_materials"]) == ["M-A", "M-B"],
                             "원자재: " + ", ".join(sorted(r["raw_materials"])))),
    Step("재고 수불 대조 일치", "GET", "/api/reconcile", expect_status=200,
         check=lambda r: _ok(r["ok"] and r["negative_lots"] == 0,
                             f"LOT {len(r['lots'])}개 / 품목 {len(r['items'])}개 모두 일치, 음수 0")),
]


def run(call) -> list[Step]:
    """모든 단계를 실행하고 결과를 채운다. (단계별로 실패해도 끝까지 진행)"""
    import copy
    steps = copy.deepcopy(STEPS)
    for s in steps:
        status, body = call(s.method, s.path, s.body)
        ok = status == s.expect_status
        note = ""
        if s.expect_error:
            ok = ok and body.get("error") == s.expect_error
            note = f"{body.get('error')}: {body.get('message')}"
        if ok and s.check:
            ok, note = s.check(body)
        elif not ok and not note:
            note = json.dumps(body, ensure_ascii=False)[:200]
        s.result = {"status": status, "ok": ok, "note": note, "body": body}
    return steps
