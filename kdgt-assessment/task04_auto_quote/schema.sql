-- 과제 04 스키마. BOM 테이블(과제 03 schema.sql)과 같은 DB 를 쓴다.

CREATE TABLE IF NOT EXISTS price_versions (     -- 단가표 버전 (등록 후 수정 불가)
    version_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    table_type     TEXT NOT NULL CHECK (table_type IN ('MATERIAL', 'LENGTH', 'PURCHASE', 'PROCESS', 'OUTSOURCE')),
    label          TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    source         TEXT NOT NULL,               -- 단가 출처
    source_hash    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (table_type, label),
    UNIQUE (table_type, effective_from)
);

CREATE TABLE IF NOT EXISTS price_items (
    version_id INTEGER NOT NULL REFERENCES price_versions(version_id),
    key        TEXT NOT NULL,
    unit       TEXT NOT NULL,
    price      TEXT NOT NULL,                   -- Decimal 문자열 (원)
    PRIMARY KEY (version_id, key)
);

CREATE TABLE IF NOT EXISTS quotes (
    quote_no        TEXT PRIMARY KEY,
    request_hash    TEXT NOT NULL,
    product_no      TEXT NOT NULL,
    revision        TEXT NOT NULL,
    qty             TEXT NOT NULL,
    quote_date      TEXT NOT NULL,
    customer        TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ISSUED', 'NEEDS_REVIEW')),
    rules_version   TEXT NOT NULL,
    versions_json   TEXT NOT NULL,              -- 사용한 단가표 버전 {MATERIAL: '2026-03', ...}
    material_total  INTEGER NOT NULL,
    process_total   INTEGER NOT NULL,
    outsource_total INTEGER NOT NULL,
    subtotal        INTEGER NOT NULL,
    rounding_adj    INTEGER NOT NULL,           -- 천원 미만 절사액 (0 또는 음수)
    supply          INTEGER NOT NULL,
    vat             INTEGER NOT NULL,
    total           INTEGER NOT NULL,
    review_count    INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_lines (        -- 비용별 근거 (견적 시점 값 그대로 저장)
    quote_no      TEXT NOT NULL REFERENCES quotes(quote_no),
    seq           INTEGER NOT NULL,
    category      TEXT NOT NULL CHECK (category IN ('MATERIAL', 'PROCESS', 'OUTSOURCE')),
    item          TEXT NOT NULL,                -- 품번 (조립품 포함)
    item_name     TEXT NOT NULL,
    cost_key      TEXT,                         -- 단가표 키 (SUS304, WELD, 25A/SUS304 …)
    basis_qty     TEXT,                         -- 수량·중량·길이·시간 등 계산 기준량
    basis_unit    TEXT,
    unit_price    TEXT,
    price_table   TEXT,
    price_version TEXT,
    formula       TEXT NOT NULL,                -- 사람이 읽는 계산식
    inputs_json   TEXT NOT NULL,
    amount        INTEGER,                      -- 검토 필요면 NULL
    status        TEXT NOT NULL CHECK (status IN ('OK', 'REVIEW')),
    review_reason TEXT,
    PRIMARY KEY (quote_no, seq)
);

CREATE TRIGGER IF NOT EXISTS pv_immutable_u BEFORE UPDATE ON price_versions BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS pv_immutable_d BEFORE DELETE ON price_versions BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS pi_immutable_u BEFORE UPDATE ON price_items BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS pi_immutable_d BEFORE DELETE ON price_items BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS q_immutable_u  BEFORE UPDATE ON quotes BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS q_immutable_d  BEFORE DELETE ON quotes BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS ql_immutable_u BEFORE UPDATE ON quote_lines BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS ql_immutable_d BEFORE DELETE ON quote_lines BEGIN SELECT RAISE(ABORT, 'IMMUTABLE'); END;
