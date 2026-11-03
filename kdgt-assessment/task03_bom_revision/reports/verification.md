# 과제 03 자동 검증 결과

- 방법: 빈 DB + 실제 HTTP 서버. 정답은 `fixtures/expected.json` (수작업 계산)
- 재실행: `python -m task03_bom_revision.cli verify`

## 판정

| 결과 | 항목 | 근거 |
|---|---|---|
| PASS | 개정 A~D 등록 | A:201 부품12 조립4, B:201 부품12 조립4, C:201 부품14 조립4, D:201 부품13 조립4 |
| PASS | 개정별 전개 수량·총길이 71개 값 정답 일치 | 전부 일치 |
| PASS | 개정 비교 3건 (A→B 규격, B→C 추가, C→D 수량·삭제) 정답 일치 | 전부 일치 |
| PASS | mm·m 변환 (7개 입력, 1250mm↔1.25m 표기 차이 무시, D 배관 총 7.5m) | 변환 일치, A→B 배관 변경 없음=True, D 배관 7.5m |
| PASS | 과거 BOM 조회 (as_of 5개 날짜, D 등록 후 A 그대로) | as_of 일치, A 재조회 일치=True |
| PASS | 오류 입력 7종 거부 (순환 2종 포함), DB 변화 없음 | 전부 기대 코드로 거부 |
| PASS | 같은 개정 재등록=200, 다른 내용=409, 직접 UPDATE 차단 | 재등록 200, 변경본 409 REVISION_EXISTS, 트리거 차단 True |
