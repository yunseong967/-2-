-- 과제 06 스키마 (SQLite, WAL)
-- 수량은 소수 오차를 피하기 위해 1/1000 단위 정수(qty_m)로 저장한다. 1.5 KG → 1500.

CREATE TABLE IF NOT EXISTS events (            -- 모든 업무 동작의 감사 기록
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,                 -- RECEIPT/INSPECT/SPLIT/MERGE/PRODUCE/SHIP
    at          TEXT NOT NULL,
    actor       TEXT,
    payload     TEXT NOT NULL                  -- 요청 JSON
);

CREATE TABLE IF NOT EXISTS lots (
    lot_id        TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('MATERIAL', 'PRODUCT')),
    item          TEXT NOT NULL,
    unit          TEXT NOT NULL,
    project_id    TEXT,
    origin        TEXT NOT NULL CHECK (origin IN ('RECEIPT', 'SPLIT', 'MERGE', 'PRODUCTION')),
    status        TEXT NOT NULL CHECK (status IN ('PENDING', 'PASS', 'FAIL', 'HOLD')),
    status_reason TEXT,
    held_from     TEXT,                         -- HOLD 직전 상태 (상위 LOT 재검사 합격 시 복원)
    held_by       TEXT,                         -- HOLD 를 건 상위 LOT
    qty_m         INTEGER NOT NULL CHECK (qty_m >= 0),   -- 재고 음수 금지 (최후 방어선)
    created_event INTEGER NOT NULL REFERENCES events(event_id)
);

-- LOT 계보: 부모 → 자식. SPLIT(분할) / MERGE(병합) / CONSUME(생산 투입)
CREATE TABLE IF NOT EXISTS lot_links (
    link_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_lot  TEXT NOT NULL REFERENCES lots(lot_id),
    child_lot   TEXT NOT NULL REFERENCES lots(lot_id),
    link_type   TEXT NOT NULL CHECK (link_type IN ('SPLIT', 'MERGE', 'CONSUME')),
    qty_m       INTEGER NOT NULL CHECK (qty_m > 0),
    event_id    INTEGER NOT NULL REFERENCES events(event_id),
    CHECK (parent_lot <> child_lot)
);
CREATE INDEX IF NOT EXISTS ix_links_parent ON lot_links(parent_lot);
CREATE INDEX IF NOT EXISTS ix_links_child  ON lot_links(child_lot);

-- 검사 이력. 재검사는 행을 추가하고, LOT 상태는 가장 최근 검사를 따른다.
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id        TEXT NOT NULL REFERENCES lots(lot_id),
    seq           INTEGER NOT NULL,            -- 해당 LOT 의 n번째 검사 (2 이상 = 재검사)
    result        TEXT NOT NULL CHECK (result IN ('PASS', 'FAIL')),
    inspector     TEXT NOT NULL,
    note          TEXT,
    event_id      INTEGER NOT NULL REFERENCES events(event_id),
    UNIQUE (lot_id, seq)
);

-- 출하. request_id 는 클라이언트가 붙이는 요청 번호 → 같은 요청이 두 번 와도 출하는 1건
CREATE TABLE IF NOT EXISTS shipments (
    shipment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    lot_id       TEXT NOT NULL REFERENCES lots(lot_id),
    qty_m        INTEGER NOT NULL CHECK (qty_m > 0),
    customer     TEXT NOT NULL,
    evidence     TEXT NOT NULL,                -- 출하 시점 검사 근거 JSON (나중에 상태가 바뀌어도 보존)
    event_id     INTEGER NOT NULL REFERENCES events(event_id)
);

-- 재고 수불부. LOT 별 합계 = lots.qty_m 이어야 한다.
CREATE TABLE IF NOT EXISTS ledger (
    entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id      TEXT NOT NULL REFERENCES lots(lot_id),
    entry_type  TEXT NOT NULL CHECK (entry_type IN
                ('RECEIPT', 'SPLIT_OUT', 'SPLIT_IN', 'MERGE_OUT', 'MERGE_IN', 'CONSUME', 'PRODUCE', 'SHIP')),
    qty_delta_m INTEGER NOT NULL,
    event_id    INTEGER NOT NULL REFERENCES events(event_id)
);
CREATE INDEX IF NOT EXISTS ix_ledger_lot ON ledger(lot_id);
