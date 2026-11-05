# KDGT 제조 AI 개발자 실습 평가

경동글로벌텍 제조AI 플랫폼 구축 사업을 바탕으로 한 실습 과제 10개 제출물입니다.
모든 데이터는 **합성 데이터**이며 실제 도면·원가는 쓰지 않습니다.

## 빠른 실행

```bash
cd kdgt-assessment
python -m pip install -r requirements.txt -r requirements-dev.txt   # openpyxl, pytest
python -m pytest                                # 전체 테스트
python -m task01_data_integration.cli verify    # 과제 01 완료 기준 검증
python -m task02_drawing_extract.evaluate       # 과제 02 도면 추출 정확도 (+ holdout)
python -m task03_bom_revision.cli verify        # 과제 03 완료 기준 검증
python -m task04_auto_quote.cli verify          # 과제 04 완료 기준 검증 (+ 견적 Excel)
python -m task05_quote_model.cli train          # 과제 05 학습·평가·누수 검사
python -m task06_lot_traceability.cli verify    # 과제 06 완료 기준 검증
python -m task03_bom_revision.cli serve         # 과제 03 개정 비교 화면 http://127.0.0.1:8003
python -m task06_lot_traceability.cli serve     # 과제 06 추적 화면 http://127.0.0.1:8006
python -m task07_qa_agent.cli eval --backend mock   # 과제 07 하네스 점검 (실제 평가: --backend claude, API 키 필요)
python -m task08_process_events.cli verify      # 과제 08 장애 주입·복구 검증
python -m task09_delivery_kpi.cli verify        # 과제 09 정답 대조·일정 규칙 검사
python -m task09_delivery_kpi.cli serve         # 과제 09 일정 비교 화면 http://127.0.0.1:8009
python -m task10_deploy.verify --runner docker   # 과제 10 컨테이너 배포·장애 복구 검증 (Docker 필요)
```

Python 3.11 이상. 외부 라이브러리는 `openpyxl`(과제 04), `numpy`·`pandas`·`scikit-learn`(과제 05), `anthropic`(과제 07), OCR·도면 처리 라이브러리(과제 02, 시스템에 tesseract 필요) 이며 나머지 과제는 표준 라이브러리(SQLite 내장)만 씁니다.

## 과제 현황

| # | 과제 | 상태 | 폴더 |
|---|---|---|---|
| 01 | 제조 데이터 통합과 정합성 검사 | 구현·검증 완료 | [task01_data_integration](task01_data_integration/README.md) |
| 02 | CAD 도면 객체 추출 | 구현·검증 완료 | [task02_drawing_extract](task02_drawing_extract/README.md) |
| 03 | BOM 생성과 개정 관리 | 구현·검증 완료 | [task03_bom_revision](task03_bom_revision/README.md) |
| 04 | 근거를 보여주는 자동 견적 | 구현·검증 완료 | [task04_auto_quote](task04_auto_quote/README.md) |
| 05 | 견적 예측 모델의 타당성 | 구현·검증 완료 | [task05_quote_model](task05_quote_model/README.md) |
| 06 | LOT 양방향 추적과 출하 차단 | 구현·검증 완료 | [task06_lot_traceability](task06_lot_traceability/README.md) |
| 07 | 입출하 질의응답 AI Agent | 구현 완료 · **실제 Claude 평가는 API 키로 실행 필요** | [task07_qa_agent](task07_qa_agent/README.md) |
| 08 | 공정 이벤트 수집과 가시화 | 구현·검증 완료 | [task08_process_events](task08_process_events/README.md) |
| 09 | 납기 분석과 KPI 계산 | 구현·검증 완료 | [task09_delivery_kpi](task09_delivery_kpi/README.md) |
| 10 | 배포·권한·장애 복구 (과제 06 배포) | 구현·검증 완료 (Docker) | [task10_deploy](task10_deploy/README.md) |

## 공통 규칙

- AI 도구: Claude 단독 사용 (출제자 승인, `docs/ai-policy.md`)
- 과제 폴더 하나 = 독립 실행 단위. 공통 코드는 `common/`에서 재사용
- 과제마다 포함할 것: `README.md`(실행 안내·설계 판단), 샘플 데이터 + 정답, `verify` 명령과 `reports/verification.md`, `tests/`, `AI_LOG.md`
- 시연 영상(3분)은 저장소 밖에 보관하고 링크만 README에 기재

## 폴더 구조

```
kdgt-assessment/
├── common/            DB 연결, CSV 읽기(UTF-8/CP949), 행 해시, 엄격한 날짜 파싱, JSON HTTP 서버
├── docs/              AI 사용 기록 템플릿
├── task01_data_integration/   과제 01
├── task02_drawing_extract/    과제 02
├── task03_bom_revision/       과제 03
├── task04_auto_quote/         과제 04
├── task05_quote_model/        과제 05
├── task06_lot_traceability/   과제 06
├── task07_qa_agent/           과제 07
├── task08_process_events/     과제 08
├── task09_delivery_kpi/       과제 09
├── task10_deploy/             과제 10
└── pytest.ini
```
