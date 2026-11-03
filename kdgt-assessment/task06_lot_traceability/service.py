"""과제 06 도메인 서비스: 입고·검사·분할·병합·생산·출하 + 양방향 추적 + 수불 대조.

동시성 원칙
  - 쓰기 동작은 모두 `BEGIN IMMEDIATE` 트랜잭션 안에서 '확인 → 변경'을 한 번에 한다.
    SQLite 는 IMMEDIATE 트랜잭션을 동시에 하나만 허용하므로, 두 출하 요청이 같은 재고를
    동시에 보고 둘 다 통과하는 일(lost update)이 생기지 않는다.
  - 그래도 재고 차감은 `WHERE qty_m >= ?` 조건부 UPDATE 로 한 번 더 막고,
    마지막으로 테이블 CHECK (qty_m >= 0) 가 음수를 거부한다. (3중 방어)
  - 중복 출하: request_id UNIQUE. 같은 요청 재전송이면 기존 결과를 돌려주고(replayed),
    같은 request_id 에 다른 내용이면 거부한다.

상태
  PENDING(미검사) / PASS(합격) / FAIL(부적합) / HOLD(상위 LOT 부적합으로 보류)
  출하·생산 투입은 PASS 만 가능.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")
SCALE = 1000  # qty_m = 수량 × 1000

STATUS_LABEL = {"PENDING": "미검사", "PASS": "합격", "FAIL": "부적합", "HOLD": "보류"}


# ---------------------------------------------------------------- 오류
class DomainError(Exception):
    """업무 규칙 위반. code 는 API 응답에 그대로 나간다."""
    http_status = 409

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def to_dict(self):
        return {"error": self.code, "message": self.message, **self.details}


class NotFound(DomainError):
    http_status = 404


class BadRequest(DomainError):
    http_status = 400


# ---------------------------------------------------------------- 연결/수량
def connect(path: str | Path) -> sqlite3.Connection:
    """요청(스레드)마다 별도 연결. 자동 트랜잭션은 끄고 tx() 로 직접 관리한다."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


@contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def to_m(value, field: str = "qty") -> int:
    """입력 수량 → 정수(1/1000). 0 이하, 소수 셋째 자리 초과, 숫자 아님은 거부."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BadRequest("INVALID_QTY", f"{field}: 숫자가 아님 ({value!r})")
    if not d.is_finite() or d <= 0:
        raise BadRequest("INVALID_QTY", f"{field}: 0보다 커야 함 ({value})")
    m = d * SCALE
    if m != m.to_integral_value():
        raise BadRequest("INVALID_QTY", f"{field}: 소수 셋째 자리까지만 허용 ({value})")
    return int(m)


def from_m(m: int) -> float | int:
    v = Decimal(m) / SCALE
    return int(v) if v == v.to_integral_value() else float(v)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _event(conn, event_type: str, payload: dict, actor: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO events (event_type, at, actor, payload) VALUES (?,?,?,?)",
        (event_type, _now(), actor, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    ).lastrowid


def _ledger(conn, lot_id: str, entry_type: str, delta_m: int, event_id: int) -> None:
    conn.execute(
        "INSERT INTO ledger (lot_id, entry_type, qty_delta_m, event_id) VALUES (?,?,?,?)",
        (lot_id, entry_type, delta_m, event_id),
    )


def _lot(conn, lot_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM lots WHERE lot_id = ?", (lot_id,)).fetchone()
    if row is None:
        raise NotFound("LOT_NOT_FOUND", f"LOT {lot_id} 없음", lot_id=lot_id)
    return row


def _require_new_lot(conn, lot_id: str) -> None:
    if not lot_id or not str(lot_id).strip():
        raise BadRequest("INVALID_LOT_ID", "LOT 번호가 비어 있음")
    if conn.execute("SELECT 1 FROM lots WHERE lot_id = ?", (lot_id,)).fetchone():
        raise DomainError("DUPLICATE_LOT", f"LOT {lot_id} 이미 존재", lot_id=lot_id)


def _require_pass(conn, lot: sqlite3.Row, action: str) -> None:
    """상태가 합격이어도, 근거 사슬(상위 자재 검사 포함)에 부적합이 하나라도 있으면 차단."""
    if lot["status"] != "PASS":
        label = STATUS_LABEL[lot["status"]]
        raise DomainError(
            "STATUS_BLOCKED",
            f"LOT {lot['lot_id']} 은(는) {label} 상태라 {action} 불가"
            + (f" ({lot['status_reason']})" if lot["status_reason"] else ""),
            lot_id=lot["lot_id"], status=lot["status"],
        )
    bad = [e for e in inspection_evidence(conn, lot["lot_id"]) if e["result"] == "FAIL"]
    if bad:
        raise DomainError(
            "UPSTREAM_FAIL",
            f"LOT {lot['lot_id']} 의 검사 근거에 부적합이 있어 {action} 불가: "
            + ", ".join(f"{e['lot_id']} 검사#{e['seq']}" for e in bad),
            lot_id=lot["lot_id"], failed=[{"lot_id": e["lot_id"], "seq": e["seq"]} for e in bad],
        )


def _take(conn, lot_id: str, qty_m: int) -> None:
    """조건부 차감. 재고가 모자라면 0행 갱신 → 오류."""
    cur = conn.execute(
        "UPDATE lots SET qty_m = qty_m - ? WHERE lot_id = ? AND qty_m >= ?", (qty_m, lot_id, qty_m))
    if cur.rowcount != 1:
        have = conn.execute("SELECT qty_m FROM lots WHERE lot_id = ?", (lot_id,)).fetchone()[0]
        raise DomainError("INSUFFICIENT_QTY",
                          f"LOT {lot_id} 재고 부족: 요청 {from_m(qty_m)}, 현재 {from_m(have)}",
                          lot_id=lot_id, requested=from_m(qty_m), on_hand=from_m(have))


def _insert_lot(conn, lot_id, kind, item, unit, project_id, origin, status, reason, qty_m, event_id):
    conn.execute(
        """INSERT INTO lots (lot_id, kind, item, unit, project_id, origin, status, status_reason,
                             qty_m, created_event) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (lot_id, kind, item, unit, project_id, origin, status, reason, qty_m, event_id))


# ---------------------------------------------------------------- 쓰기 동작
def receive(conn, lot_id: str, item: str, qty, unit: str, project_id: str | None = None,
            actor: str | None = None) -> dict:
    """자재 입고 → 미검사(PENDING) 상태로 생성."""
    q = to_m(qty)
    with tx(conn):
        _require_new_lot(conn, lot_id)
        ev = _event(conn, "RECEIPT", {"lot_id": lot_id, "item": item, "qty": str(qty),
                                      "unit": unit, "project_id": project_id}, actor)
        _insert_lot(conn, lot_id, "MATERIAL", item, unit, project_id, "RECEIPT", "PENDING", None, q, ev)
        _ledger(conn, lot_id, "RECEIPT", q, ev)
    return lot_view(conn, lot_id)


def inspect(conn, lot_id: str, result: str, inspector: str, note: str | None = None) -> dict:
    """검사/재검사. FAIL 이면 하위 LOT 전체를 HOLD 로 묶고 영향 출하 목록을 돌려준다."""
    if result not in ("PASS", "FAIL"):
        raise BadRequest("INVALID_RESULT", f"result 는 PASS/FAIL ({result!r})")
    if not inspector:
        raise BadRequest("INVALID_INSPECTOR", "inspector 필수")
    with tx(conn):
        _lot(conn, lot_id)
        seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM inspections WHERE lot_id=?",
                           (lot_id,)).fetchone()[0]
        ev = _event(conn, "INSPECT", {"lot_id": lot_id, "result": result, "note": note}, inspector)
        insp_id = conn.execute(
            "INSERT INTO inspections (lot_id, seq, result, inspector, note, event_id) VALUES (?,?,?,?,?,?)",
            (lot_id, seq, result, inspector, note, ev)).lastrowid
        reason = None if result == "PASS" else f"검사 #{insp_id} 부적합" + (f": {note}" if note else "")
        conn.execute("UPDATE lots SET status=?, status_reason=?, held_from=NULL, held_by=NULL "
                     "WHERE lot_id=?", (result, reason, lot_id))

        held, released = [], []
        for d in _descendants(conn, lot_id):
            row = conn.execute("SELECT status, held_by, held_from FROM lots WHERE lot_id=?", (d,)).fetchone()
            if result == "FAIL" and row["status"] in ("PASS", "PENDING"):
                conn.execute(
                    "UPDATE lots SET status='HOLD', status_reason=?, held_from=?, held_by=? WHERE lot_id=?",
                    (f"상위 LOT {lot_id} 부적합(검사 #{insp_id})", row["status"], lot_id, d))
                held.append(d)
            elif result == "PASS" and row["status"] == "HOLD" and row["held_by"] == lot_id:
                # 다른 상위 LOT 이 여전히 부적합이면 보류 유지
                other = [e for e in inspection_evidence(conn, d) if e["result"] == "FAIL"]
                if other:
                    e = other[0]
                    conn.execute("UPDATE lots SET status_reason=?, held_by=? WHERE lot_id=?",
                                 (f"상위 LOT {e['lot_id']} 부적합(검사 #{e['inspection_id']})",
                                  e["lot_id"], d))
                    continue
                conn.execute("UPDATE lots SET status=?, status_reason=NULL, held_from=NULL, held_by=NULL "
                             "WHERE lot_id=?", (row["held_from"], d))
                released.append(d)
    out = {"inspection_id": insp_id, "lot_id": lot_id, "seq": seq, "reinspection": seq > 1,
           "result": result, "status": result, "held_lots": held, "released_lots": released}
    if result == "FAIL":
        out["affected_shipments"] = trace_forward(conn, lot_id)["shipments"]
    return out


def split(conn, lot_id: str, quantities: list, new_lot_ids: list[str] | None = None,
          actor: str | None = None) -> dict:
    """분할. 자식 LOT 은 부모의 상태/사유를 그대로 물려받는다. 남은 양은 부모에 남는다."""
    if not quantities:
        raise BadRequest("INVALID_SPLIT", "분할 수량 목록이 비어 있음")
    qs = [to_m(q, "quantities") for q in quantities]
    if new_lot_ids is not None and len(new_lot_ids) != len(qs):
        raise BadRequest("INVALID_SPLIT", "new_lot_ids 개수가 quantities 와 다름")
    with tx(conn):
        parent = _lot(conn, lot_id)
        if sum(qs) > parent["qty_m"]:
            raise DomainError("INSUFFICIENT_QTY",
                              f"LOT {lot_id} 분할 합계 {from_m(sum(qs))} > 현재 {from_m(parent['qty_m'])}",
                              lot_id=lot_id, requested=from_m(sum(qs)), on_hand=from_m(parent["qty_m"]))
        n0 = conn.execute("SELECT COUNT(*) FROM lot_links WHERE parent_lot=? AND link_type='SPLIT'",
                          (lot_id,)).fetchone()[0]
        ids = new_lot_ids or [f"{lot_id}-S{n0 + i + 1}" for i in range(len(qs))]
        if len(set(ids)) != len(ids):
            raise BadRequest("INVALID_SPLIT", "new_lot_ids 중복")
        for cid in ids:
            _require_new_lot(conn, cid)
        ev = _event(conn, "SPLIT", {"lot_id": lot_id, "quantities": [str(q) for q in quantities],
                                    "children": ids}, actor)
        _take(conn, lot_id, sum(qs))
        for cid, q in zip(ids, qs):
            _insert_lot(conn, cid, parent["kind"], parent["item"], parent["unit"], parent["project_id"],
                        "SPLIT", parent["status"], parent["status_reason"], q, ev)
            conn.execute("INSERT INTO lot_links (parent_lot, child_lot, link_type, qty_m, event_id) "
                         "VALUES (?,?,?,?,?)", (lot_id, cid, "SPLIT", q, ev))
            _ledger(conn, lot_id, "SPLIT_OUT", -q, ev)
            _ledger(conn, cid, "SPLIT_IN", q, ev)
    return {"parent": lot_view(conn, lot_id), "children": [lot_view(conn, c) for c in ids]}


def merge(conn, lot_ids: list[str], new_lot_id: str, actor: str | None = None) -> dict:
    """병합. 같은 품목·단위·종류만 가능. 부적합/보류 LOT 은 섞을 수 없다.
    모두 합격이면 합격, 하나라도 미검사면 결과도 미검사(재검사 필요)."""
    if len(lot_ids) < 2 or len(set(lot_ids)) != len(lot_ids):
        raise BadRequest("INVALID_MERGE", "서로 다른 LOT 2개 이상 필요")
    with tx(conn):
        lots = [_lot(conn, l) for l in lot_ids]
        _require_new_lot(conn, new_lot_id)
        keys = {(l["kind"], l["item"], l["unit"]) for l in lots}
        if len(keys) != 1:
            raise DomainError("ITEM_MISMATCH", "품목·단위·종류가 다른 LOT 은 병합 불가",
                              lots={l["lot_id"]: f"{l['kind']}/{l['item']}/{l['unit']}" for l in lots})
        for l in lots:
            if l["status"] in ("FAIL", "HOLD") or any(
                    e["result"] == "FAIL" for e in inspection_evidence(conn, l["lot_id"])):
                _require_pass(conn, l, "병합")
            if l["qty_m"] == 0:
                raise DomainError("INSUFFICIENT_QTY", f"LOT {l['lot_id']} 재고 0", lot_id=l["lot_id"])
        status = "PASS" if all(l["status"] == "PASS" for l in lots) else "PENDING"
        projects = {l["project_id"] for l in lots}
        ev = _event(conn, "MERGE", {"lot_ids": lot_ids, "new_lot_id": new_lot_id}, actor)
        total = sum(l["qty_m"] for l in lots)
        kind, item, unit = keys.pop()
        _insert_lot(conn, new_lot_id, kind, item, unit,
                    projects.pop() if len(projects) == 1 else None,
                    "MERGE", status, None if status == "PASS" else "병합 LOT 에 미검사분 포함", total, ev)
        for l in lots:
            _take(conn, l["lot_id"], l["qty_m"])
            conn.execute("INSERT INTO lot_links (parent_lot, child_lot, link_type, qty_m, event_id) "
                         "VALUES (?,?,?,?,?)", (l["lot_id"], new_lot_id, "MERGE", l["qty_m"], ev))
            _ledger(conn, l["lot_id"], "MERGE_OUT", -l["qty_m"], ev)
        _ledger(conn, new_lot_id, "MERGE_IN", total, ev)
    return lot_view(conn, new_lot_id)


def produce(conn, product_lot_id: str, item: str, qty, unit: str, consumes: list[dict],
            project_id: str | None = None, actor: str | None = None) -> dict:
    """생산. 합격(PASS) LOT 만 투입 가능. 제품 LOT 은 미검사 상태로 생성된다."""
    q = to_m(qty)
    if not consumes:
        raise BadRequest("INVALID_PRODUCE", "투입 자재(consumes) 필요")
    uses = [(c["lot_id"], to_m(c["qty"], f"consumes[{c.get('lot_id')}].qty")) for c in consumes]
    if len({u[0] for u in uses}) != len(uses):
        raise BadRequest("INVALID_PRODUCE", "같은 LOT 을 consumes 에 두 번 넣을 수 없음")
    with tx(conn):
        _require_new_lot(conn, product_lot_id)
        for lid, _ in uses:
            _require_pass(conn, _lot(conn, lid), "생산 투입")
        ev = _event(conn, "PRODUCE", {"lot_id": product_lot_id, "item": item, "qty": str(qty),
                                      "consumes": consumes, "project_id": project_id}, actor)
        _insert_lot(conn, product_lot_id, "PRODUCT", item, unit, project_id, "PRODUCTION",
                    "PENDING", None, q, ev)
        for lid, uq in uses:
            _take(conn, lid, uq)
            conn.execute("INSERT INTO lot_links (parent_lot, child_lot, link_type, qty_m, event_id) "
                         "VALUES (?,?,?,?,?)", (lid, product_lot_id, "CONSUME", uq, ev))
            _ledger(conn, lid, "CONSUME", -uq, ev)
        _ledger(conn, product_lot_id, "PRODUCE", q, ev)
    return lot_view(conn, product_lot_id)


def _request_hash(lot_id, qty_m, customer) -> str:
    return hashlib.sha256(json.dumps([lot_id, qty_m, customer], ensure_ascii=False).encode()).hexdigest()


def ship(conn, request_id: str, lot_id: str, qty, customer: str, actor: str | None = None) -> dict:
    """출하. 제품 LOT·합격·재고 충분·request_id 미사용일 때만 성공.
    같은 request_id 로 같은 내용이 다시 오면 새로 출하하지 않고 기존 결과를 돌려준다."""
    if not request_id:
        raise BadRequest("INVALID_REQUEST_ID", "request_id 필수 (중복 출하 방지용)")
    if not customer:
        raise BadRequest("INVALID_CUSTOMER", "customer 필수")
    q = to_m(qty)
    h = _request_hash(lot_id, q, customer)
    with tx(conn):
        prev = conn.execute("SELECT * FROM shipments WHERE request_id=?", (request_id,)).fetchone()
        if prev is not None:
            if prev["request_hash"] != h:
                raise DomainError("REQUEST_ID_CONFLICT",
                                  f"request_id {request_id} 는 다른 내용으로 이미 사용됨",
                                  request_id=request_id, shipment_id=prev["shipment_id"])
            return {**shipment_view(conn, prev["shipment_id"]), "replayed": True}
        lot = _lot(conn, lot_id)
        if lot["kind"] != "PRODUCT":
            raise DomainError("KIND_INVALID", f"LOT {lot_id} 은(는) 자재 LOT — 제품 LOT 만 출하 가능",
                              lot_id=lot_id)
        _require_pass(conn, lot, "출하")
        ev = _event(conn, "SHIP", {"request_id": request_id, "lot_id": lot_id, "qty": str(qty),
                                   "customer": customer}, actor)
        _take(conn, lot_id, q)
        evidence = inspection_evidence(conn, lot_id)
        sid = conn.execute(
            """INSERT INTO shipments (request_id, request_hash, lot_id, qty_m, customer, evidence, event_id)
               VALUES (?,?,?,?,?,?,?)""",
            (request_id, h, lot_id, q, customer, json.dumps(evidence, ensure_ascii=False), ev)).lastrowid
        _ledger(conn, lot_id, "SHIP", -q, ev)
    return {**shipment_view(conn, sid), "replayed": False}


# ---------------------------------------------------------------- 조회
def lot_view(conn, lot_id: str) -> dict:
    l = _lot(conn, lot_id)
    return {"lot_id": l["lot_id"], "kind": l["kind"], "item": l["item"], "unit": l["unit"],
            "project_id": l["project_id"], "origin": l["origin"], "status": l["status"],
            "status_label": STATUS_LABEL[l["status"]], "status_reason": l["status_reason"],
            "qty": from_m(l["qty_m"])}


def list_lots(conn) -> list[dict]:
    return [lot_view(conn, r[0]) for r in conn.execute("SELECT lot_id FROM lots ORDER BY lot_id")]


def shipment_view(conn, shipment_id: int) -> dict:
    s = conn.execute(
        """SELECT s.*, e.at FROM shipments s JOIN events e ON e.event_id = s.event_id
           WHERE s.shipment_id = ?""", (shipment_id,)).fetchone()
    if s is None:
        raise NotFound("SHIPMENT_NOT_FOUND", f"출하 {shipment_id} 없음")
    return {"shipment_id": s["shipment_id"], "request_id": s["request_id"], "lot_id": s["lot_id"],
            "qty": from_m(s["qty_m"]), "customer": s["customer"], "shipped_at": s["at"],
            "evidence": json.loads(s["evidence"])}


def _inspection_row(conn, lot_id):
    r = conn.execute(
        """SELECT i.inspection_id, i.lot_id, i.seq, i.result, i.inspector, i.note, e.at
           FROM inspections i JOIN events e ON e.event_id = i.event_id
           WHERE i.lot_id = ? ORDER BY i.seq DESC LIMIT 1""", (lot_id,)).fetchone()
    return dict(r) if r else None


def _parents(conn, lot_id):
    return conn.execute("SELECT parent_lot, link_type, qty_m FROM lot_links WHERE child_lot=? "
                        "ORDER BY link_id", (lot_id,)).fetchall()


def _children(conn, lot_id):
    return conn.execute("SELECT child_lot, link_type, qty_m FROM lot_links WHERE parent_lot=? "
                        "ORDER BY link_id", (lot_id,)).fetchall()


def _descendants(conn, lot_id) -> list[str]:
    seen, order, q = {lot_id}, [], deque([lot_id])
    while q:
        for c in _children(conn, q.popleft()):
            if c["child_lot"] not in seen:
                seen.add(c["child_lot"])
                order.append(c["child_lot"])
                q.append(c["child_lot"])
    return order


def inspection_evidence(conn, lot_id: str) -> list[dict]:
    """이 LOT 이 합격이라고 볼 근거가 되는 검사 목록.
    - 자기 검사가 있으면 가장 최근 것
    - 분할/병합으로 생겨 자기 검사가 없으면 → 부모의 근거를 물려받음
    - 생산 투입(CONSUME) 자재의 근거는 항상 포함 (제품 = 제품검사 + 자재검사)"""
    out, seen = [], set()

    def walk(lid, via):
        if lid in seen:
            return
        seen.add(lid)
        own = _inspection_row(conn, lid)
        if own:
            out.append({**own, "via": via})
        for p in _parents(conn, lid):
            if p["link_type"] == "CONSUME" or own is None:
                walk(p["parent_lot"], f"{lid}←{p['link_type']}")

    walk(lot_id, "self")
    return out


def trace_forward(conn, lot_id: str) -> dict:
    """정방향: 이 LOT 에서 파생된 모든 LOT 과, 그 LOT 들로 나간 출하 목록."""
    _lot(conn, lot_id)
    lots = [lot_id] + _descendants(conn, lot_id)
    edges = [{"from": l, "to": c["child_lot"], "type": c["link_type"], "qty": from_m(c["qty_m"])}
             for l in lots for c in _children(conn, l)]
    marks = ",".join("?" * len(lots))
    sids = [r[0] for r in conn.execute(
        f"SELECT shipment_id FROM shipments WHERE lot_id IN ({marks}) ORDER BY shipment_id", lots)]
    return {"root": lot_id, "direction": "forward",
            "lots": [lot_view(conn, l) for l in lots], "edges": edges,
            "shipments": [shipment_view(conn, s) for s in sids]}


def trace_backward(conn, lot_id: str) -> dict:
    """역방향: 이 LOT 을 만든 모든 상위 LOT(최초 입고 자재까지)과 각 LOT 의 최근 검사."""
    _lot(conn, lot_id)
    seen, order, q, edges = {lot_id}, [lot_id], deque([lot_id]), []
    while q:
        cur = q.popleft()
        for p in _parents(conn, cur):
            edges.append({"from": p["parent_lot"], "to": cur, "type": p["link_type"],
                          "qty": from_m(p["qty_m"])})
            if p["parent_lot"] not in seen:
                seen.add(p["parent_lot"])
                order.append(p["parent_lot"])
                q.append(p["parent_lot"])
    nodes = []
    for l in order:
        v = lot_view(conn, l)
        v["latest_inspection"] = _inspection_row(conn, l)
        nodes.append(v)
    roots = [n["lot_id"] for n in nodes if n["origin"] == "RECEIPT"]
    return {"root": lot_id, "direction": "backward", "lots": nodes, "edges": edges,
            "raw_materials": roots, "evidence": inspection_evidence(conn, lot_id)}


def trace_shipment(conn, shipment_id: int) -> dict:
    s = shipment_view(conn, shipment_id)
    return {"shipment": s, **trace_backward(conn, s["lot_id"])}


def reconcile(conn) -> dict:
    """재고 수불 대조: LOT 별 수불부 합계 = 현재고, 품목별 보존식 성립 여부."""
    types = ["RECEIPT", "SPLIT_IN", "SPLIT_OUT", "MERGE_IN", "MERGE_OUT", "PRODUCE", "CONSUME", "SHIP"]
    rows = []
    for l in conn.execute("SELECT lot_id, item, unit, qty_m FROM lots ORDER BY lot_id"):
        sums = dict.fromkeys(types, 0)
        for e in conn.execute("SELECT entry_type, SUM(qty_delta_m) FROM ledger WHERE lot_id=? "
                              "GROUP BY entry_type", (l["lot_id"],)):
            sums[e[0]] = e[1]
        calc = sum(sums.values())
        rows.append({"lot_id": l["lot_id"], "item": l["item"], "unit": l["unit"],
                     **{k: from_m(v) for k, v in sums.items()},
                     "ledger_balance": from_m(calc), "on_hand": from_m(l["qty_m"]),
                     "diff": from_m(calc - l["qty_m"]), "ok": calc == l["qty_m"]})
    items = []
    for r in conn.execute(
        """SELECT l.item, l.unit,
                  SUM(CASE WHEN g.entry_type IN ('RECEIPT','PRODUCE') THEN g.qty_delta_m ELSE 0 END) AS inflow,
                  SUM(CASE WHEN g.entry_type IN ('CONSUME','SHIP') THEN -g.qty_delta_m ELSE 0 END) AS outflow,
                  SUM(CASE WHEN g.entry_type LIKE 'SPLIT%' OR g.entry_type LIKE 'MERGE%'
                           THEN g.qty_delta_m ELSE 0 END) AS internal_net
           FROM ledger g JOIN lots l ON l.lot_id = g.lot_id GROUP BY l.item, l.unit ORDER BY l.item"""):
        on_hand = conn.execute("SELECT SUM(qty_m) FROM lots WHERE item=? AND unit=?",
                               (r["item"], r["unit"])).fetchone()[0]
        items.append({"item": r["item"], "unit": r["unit"], "inflow": from_m(r["inflow"]),
                      "outflow": from_m(r["outflow"]), "split_merge_net": from_m(r["internal_net"]),
                      "on_hand": from_m(on_hand),
                      "ok": r["inflow"] - r["outflow"] == on_hand and r["internal_net"] == 0})
    negative = conn.execute("SELECT COUNT(*) FROM lots WHERE qty_m < 0").fetchone()[0]
    return {"lots": rows, "items": items, "negative_lots": negative,
            "ok": all(r["ok"] for r in rows) and all(i["ok"] for i in items) and negative == 0}
