-- 과제 01 스키마 (SQLite)
-- 정상 데이터는 업무 테이블에, 오류 데이터는 quarantine 에 원본 그대로 격리한다.
-- 모든 업무 행은 원본 파일명/행 번호/행 해시/적재 배치를 함께 보존한다.

CREATE TABLE IF NOT EXISTS load_batches (
    batch_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    data_dir     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS load_files (
    batch_id          INTEGER NOT NULL REFERENCES load_batches(batch_id),
    file_name         TEXT    NOT NULL,
    file_sha256       TEXT    NOT NULL,
    encoding          TEXT    NOT NULL,
    rows_read         INTEGER NOT NULL,
    rows_inserted     INTEGER NOT NULL,
    rows_unchanged    INTEGER NOT NULL,
    rows_quarantined  INTEGER NOT NULL,
    PRIMARY KEY (batch_id, file_name)
);

-- 수주 (orders.csv) : 프로젝트 = 수주 1건
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    customer     TEXT NOT NULL,
    product      TEXT NOT NULL,
    order_date   TEXT NOT NULL,
    due_date     TEXT NOT NULL,
    order_qty    INTEGER NOT NULL CHECK (order_qty > 0),
    source_file  TEXT NOT NULL,
    source_row   INTEGER NOT NULL,
    row_hash     TEXT NOT NULL,
    batch_id     INTEGER NOT NULL REFERENCES load_batches(batch_id),
    CHECK (due_date >= order_date)
);

-- 자재 (materials.csv) : 자재 LOT 등록부. LOT 는 프로젝트에 귀속된다(ETO 구조).
CREATE TABLE IF NOT EXISTS material_lots (
    lot_no        TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(project_id),
    material_code TEXT NOT NULL,
    material      TEXT NOT NULL,
    thickness_mm  REAL NOT NULL CHECK (thickness_mm > 0),
    unit          TEXT NOT NULL CHECK (unit IN ('KG', 'EA')),
    source_file   TEXT NOT NULL,
    source_row    INTEGER NOT NULL,
    row_hash      TEXT NOT NULL,
    batch_id      INTEGER NOT NULL REFERENCES load_batches(batch_id)
);

-- 입고 (receipts.csv)
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id    TEXT PRIMARY KEY,
    lot_no        TEXT NOT NULL REFERENCES material_lots(lot_no),
    supplier      TEXT NOT NULL,
    received_date TEXT NOT NULL,
    qty           REAL NOT NULL CHECK (qty > 0),
    source_file   TEXT NOT NULL,
    source_row    INTEGER NOT NULL,
    row_hash      TEXT NOT NULL,
    batch_id      INTEGER NOT NULL REFERENCES load_batches(batch_id)
);

-- 검사 (inspections.csv)
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id  TEXT PRIMARY KEY,
    lot_no         TEXT NOT NULL REFERENCES material_lots(lot_no),
    inspected_date TEXT NOT NULL,
    result         TEXT NOT NULL CHECK (result IN ('PASS', 'FAIL')),
    inspected_qty  REAL NOT NULL CHECK (inspected_qty > 0),
    source_file    TEXT NOT NULL,
    source_row     INTEGER NOT NULL,
    row_hash       TEXT NOT NULL,
    batch_id       INTEGER NOT NULL REFERENCES load_batches(batch_id)
);

-- 출하 (shipments.csv) : lot_no 는 해당 출하품에 투입된 주 자재 LOT (추적용)
CREATE TABLE IF NOT EXISTS shipments (
    shipment_id   TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(project_id),
    lot_no        TEXT NOT NULL REFERENCES material_lots(lot_no),
    shipped_date  TEXT NOT NULL,
    qty           INTEGER NOT NULL CHECK (qty > 0),
    source_file   TEXT NOT NULL,
    source_row    INTEGER NOT NULL,
    row_hash      TEXT NOT NULL,
    batch_id      INTEGER NOT NULL REFERENCES load_batches(batch_id)
);

-- 오류 격리. 같은 파일·같은 행·같은 내용은 한 번만 저장된다(재적재 시 건수 불변).
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    first_batch_id INTEGER NOT NULL REFERENCES load_batches(batch_id),
    entity         TEXT NOT NULL,
    source_file    TEXT NOT NULL,
    source_row     INTEGER NOT NULL,
    record_key     TEXT,
    reason_codes   TEXT NOT NULL,   -- 예: "UNKNOWN_LOT" / "QTY_NEGATIVE,DATE_INVALID"
    reason_detail  TEXT NOT NULL,   -- 사람이 읽는 설명
    raw_json       TEXT NOT NULL,   -- 원본 행 그대로
    row_hash       TEXT NOT NULL,
    -- 나중에 수정본이 정상 적재되면 그 배치 번호를 기록한다(행은 지우지 않고 이력으로 남김)
    resolved_batch_id INTEGER REFERENCES load_batches(batch_id),
    UNIQUE (source_file, source_row, row_hash)
);

-- 관계가 끊긴 행: 참조 대상(LOT/프로젝트)이 없거나, 검사 대상 LOT 의 입고가 없어 격리된 행
-- (뷰는 정의가 바뀔 수 있으므로 매번 다시 만든다. 기존 DB 에도 새 정의가 반영되도록)
DROP VIEW IF EXISTS v_broken_relations;
CREATE VIEW v_broken_relations AS
SELECT q.source_file,
       q.source_row,
       q.entity,
       q.record_key,
       q.reason_codes,
       json_extract(q.raw_json, '$.lot_no')     AS ref_lot_no,
       json_extract(q.raw_json, '$.project_id') AS ref_project_id,
       q.reason_detail
FROM quarantine q
WHERE q.resolved_batch_id IS NULL
  AND (q.reason_codes LIKE '%UNKNOWN_LOT%' OR q.reason_codes LIKE '%UNKNOWN_PROJECT%'
       OR q.reason_codes LIKE '%NO_RECEIPT%')
ORDER BY q.source_file, q.source_row;

-- 프로젝트-LOT 연결 요약 (통합 결과 확인용)
DROP VIEW IF EXISTS v_project_lot_summary;
CREATE VIEW v_project_lot_summary AS
SELECT p.project_id,
       m.lot_no,
       m.material,
       (SELECT COUNT(*) FROM receipts r    WHERE r.lot_no = m.lot_no)   AS receipt_cnt,
       (SELECT COALESCE(SUM(qty),0) FROM receipts r WHERE r.lot_no = m.lot_no) AS received_qty,
       (SELECT COUNT(*) FROM inspections i WHERE i.lot_no = m.lot_no)   AS inspection_cnt,
       (SELECT COUNT(*) FROM shipments s   WHERE s.lot_no = m.lot_no)   AS shipment_cnt
FROM projects p
JOIN material_lots m ON m.project_id = p.project_id
ORDER BY p.project_id, m.lot_no;
