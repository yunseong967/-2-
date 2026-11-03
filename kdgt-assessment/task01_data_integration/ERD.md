# 과제 01 ERD

```mermaid
erDiagram
    projects ||--o{ material_lots : "프로젝트별 자재 LOT"
    projects ||--o{ shipments     : "출하"
    material_lots ||--o{ receipts    : "입고"
    material_lots ||--o{ inspections : "검사"
    material_lots ||--o{ shipments   : "투입 LOT 추적"
    load_batches ||--o{ load_files   : "파일별 적재 결과"
    load_batches ||--o{ quarantine   : "격리(최초 배치)"

    projects {
        TEXT project_id PK "수주번호 P2026-001"
        TEXT customer
        TEXT product
        TEXT order_date "YYYY-MM-DD"
        TEXT due_date "order_date 이후"
        INT  order_qty "> 0"
    }
    material_lots {
        TEXT lot_no PK "L2026-0001"
        TEXT project_id FK
        TEXT material_code
        TEXT material
        REAL thickness_mm "> 0"
        TEXT unit "KG | EA"
    }
    receipts {
        TEXT receipt_id PK
        TEXT lot_no FK
        TEXT supplier
        TEXT received_date
        REAL qty "> 0"
    }
    inspections {
        TEXT inspection_id PK
        TEXT lot_no FK
        TEXT inspected_date "최초 입고일 이후"
        TEXT result "PASS | FAIL"
        REAL inspected_qty "> 0"
    }
    shipments {
        TEXT shipment_id PK
        TEXT project_id FK
        TEXT lot_no FK "같은 프로젝트 소속 LOT"
        TEXT shipped_date "수주일 이후"
        INT  qty "> 0"
    }
    quarantine {
        INT  quarantine_id PK
        TEXT entity
        TEXT source_file "UNIQUE(source_file, source_row, row_hash)"
        INT  source_row "원본 줄 번호(헤더=1)"
        TEXT record_key
        TEXT reason_codes
        TEXT reason_detail
        TEXT raw_json "원본 행 그대로"
        INT  resolved_batch_id "수정본 적재 시 기록"
    }
    load_batches {
        INT  batch_id PK
        TEXT started_at
        TEXT data_dir
    }
    load_files {
        INT  batch_id PK
        TEXT file_name PK
        TEXT file_sha256
        TEXT encoding
        INT  rows_read
        INT  rows_inserted
        INT  rows_unchanged
        INT  rows_quarantined
    }
```

모든 업무 테이블(projects ~ shipments)은 위 컬럼 외에 **출처 컬럼**을 함께 가집니다:
`source_file`, `source_row`, `row_hash`, `batch_id`.

조회용 뷰
- `v_broken_relations`: 참조 대상(LOT/프로젝트)이 없어 격리됐고 아직 해결되지 않은 행
- `v_project_lot_summary`: 프로젝트 → LOT → 입고/검사/출하 건수 요약
