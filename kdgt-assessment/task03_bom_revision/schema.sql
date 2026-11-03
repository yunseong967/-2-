-- 과제 03 스키마 (SQLite)
-- 개정(revision)은 한 번 저장되면 바뀌지 않는다. 과거 BOM 을 언제든 그대로 재현하기 위해서다.
-- 수량·길이는 Decimal 문자열(TEXT)로 저장한다 (부동소수 오차 방지). 길이는 mm 로 정규화.

CREATE TABLE IF NOT EXISTS revisions (
    rev_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_no     TEXT NOT NULL,
    revision       TEXT NOT NULL,
    drawing_no     TEXT NOT NULL,
    effective_date TEXT NOT NULL,          -- 이 날짜부터 유효 (as_of 조회 기준)
    note           TEXT,
    source_hash    TEXT NOT NULL,          -- 원본 추출 JSON 해시 (같은 내용 재등록 판별)
    source_json    TEXT NOT NULL,          -- 원본 추출 JSON 그대로
    created_at     TEXT NOT NULL,
    UNIQUE (product_no, revision),
    UNIQUE (product_no, effective_date)
);

CREATE TABLE IF NOT EXISTS rev_items (     -- 개정별 품목 규격
    rev_id       INTEGER NOT NULL REFERENCES revisions(rev_id),
    part_no      TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('ASSEMBLY', 'PART')),
    material     TEXT,
    thickness_mm TEXT,
    PRIMARY KEY (rev_id, part_no)
);

CREATE TABLE IF NOT EXISTS rev_lines (     -- 개정별 BOM 구성 (부모 → 자식)
    rev_id     INTEGER NOT NULL REFERENCES revisions(rev_id),
    seq        INTEGER NOT NULL,           -- 원본 lines[] 순서
    parent     TEXT NOT NULL,
    child      TEXT NOT NULL,
    qty        TEXT NOT NULL,              -- 부모 1개당 수량
    length_mm  TEXT,                       -- 1개당 절단 길이 (배관·봉재 등), mm
    PRIMARY KEY (rev_id, parent, child),
    FOREIGN KEY (rev_id, parent) REFERENCES rev_items(rev_id, part_no),
    FOREIGN KEY (rev_id, child)  REFERENCES rev_items(rev_id, part_no)
);

-- 불변성: 저장된 개정은 수정·삭제 불가
CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON revisions
BEGIN SELECT RAISE(ABORT, 'REVISION_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON revisions
BEGIN SELECT RAISE(ABORT, 'REVISION_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS rev_items_no_update BEFORE UPDATE ON rev_items
BEGIN SELECT RAISE(ABORT, 'REVISION_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS rev_items_no_delete BEFORE DELETE ON rev_items
BEGIN SELECT RAISE(ABORT, 'REVISION_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS rev_lines_no_update BEFORE UPDATE ON rev_lines
BEGIN SELECT RAISE(ABORT, 'REVISION_IMMUTABLE'); END;
CREATE TRIGGER IF NOT EXISTS rev_lines_no_delete BEFORE DELETE ON rev_lines
BEGIN SELECT RAISE(ABORT, 'REVISION_IMMUTABLE'); END;
