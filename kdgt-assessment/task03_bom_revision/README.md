# 과제 03 — BOM 생성과 개정 관리

> 도면 추출 결과(JSON)로 **수량·단위가 있는 다단계 BOM**을 만들고, 도면 개정 간 **규격 변경·부품 추가·수량 변경**을 비교한다.
> 순환 참조는 차단하고, 등록된 개정은 수정할 수 없게 해서 과거 BOM 을 언제든 그대로 재현한다.

## 실행 (kdgt-assessment 폴더에서)

```bash
python -m task03_bom_revision.cli verify     # 자동 검증 (수작업 정답 대조)
python -m task03_bom_revision.cli demo       # out/task03.db 에 개정 A~D 등록
python -m task03_bom_revision.cli serve      # http://127.0.0.1:8003  개정 비교 화면 + API
python -m task03_bom_revision.cli import task03_bom_revision/fixtures/invalid/cycle_nested.json   # 오류 입력 시연
python -m pytest                             # 전체 테스트 (과제 03: 27개)
```

## 완료 기준 대응

| 슬라이드 요구 | 결과 | 증빙 |
|---|---|---|
| 부품 10개 이상, 하위 조립품 2개 | 부품 12~14개, 조립품 3개(쉘·자켓 + 쉘 안의 노즐 조립 = 2단 중첩) | `fixtures/rev_*.json` |
| 도면 개정 3건 | B(규격 변경), C(부품 추가), D(수량 변경·삭제) | 화면 개정 이력 |
| 개정 전후 수량 재현 | 개정 4개 × 전개 수량·총 길이 **71개 값**이 수작업 정답과 일치 | `reports/quantity_verification.md` |
| mm·m 변환 검증 | 입력 7종 환산 일치. 1250 mm ↔ 1.25 m 처럼 **표기만 다른 것은 변경으로 잡지 않음** | 같은 보고서 "mm·m 변환" |
| 과거 BOM 조회 | 개정 지정(`rev=B`) 또는 날짜 지정(`as_of=2026-04-15` → B). 개정은 DB 트리거로 수정·삭제 불가 | `verification.md` |
| 순환 오류 처리 | 중첩 순환(쉘→노즐→쉘)·자기참조를 **경로와 함께** 거부(422). DB 변화 없음 | 같은 보고서 "오류 입력" |

## 제출물 위치

| 제출물 | 파일 |
|---|---|
| BOM API | `api.py` (엔드포인트 목록은 파일 상단 주석), 공통 서버 `common/jsonapi.py` |
| 변경 비교 화면 | `static/compare.html` (`cli serve`), 캡처 `reports/screen_*.png` |
| 수량 검증 결과 | `reports/quantity_verification.md`, `reports/verification.md` |

## 입력 형식 = 과제 02 출력 형식

```json
{"product_no": "RX-500", "drawing_no": "DWG-RX500", "revision": "B", "effective_date": "2026-04-01",
 "note": "쉘 판 두께 6→8t",
 "items": [{"part_no": "PL-SHELL", "name": "쉘 판", "kind": "PART", "material": "SUS304",
            "thickness": {"value": 8, "unit": "mm"}}],
 "lines": [{"parent": "SA-JACKET", "child": "PIPE-25A", "qty": 4, "length": {"value": 1.25, "unit": "m"}}]}
```
- `kind`: `ASSEMBLY`(조립품) / `PART`(부품). 최상위 품번 = `product_no`
- `qty`: 부모 1개당 수량. 소수 허용(예: 도료 0.25 L)
- `length`: 1개당 절단 길이(배관·봉재). 단위는 `mm`·`cm`·`m` 만 허용. inch 등은 **추측하지 않고 거부**

## 개정 비교가 보여주는 것 3가지

1. **구성 변경**: 부모→자식 줄 단위의 추가, 삭제, 수량 변경, 길이 변경
2. **규격 변경**: 같은 품번의 재질·두께·품명 변경
3. **전개 총수량 변화**: 하위 조립 수량까지 곱한 결과. 예를 들어 노즐 조립이 3개에서 4개가 되면 볼트는 12개에서 16개가 된다. 구매 수량에 실제로 영향을 주는 것은 이 값이다.

## 설계 판단 (시연 설명 포인트)

1. **개정은 불변**: 수정이 필요하면 새 개정으로 등록한다. 같은 개정 번호에 다른 내용을 넣으면 `REVISION_EXISTS`로 거부한다. DB 트리거가 직접 UPDATE·DELETE 까지 막는다.
2. **표기가 달라도 같은 BOM 이면 같은 개정**: 단위 표기(1.25 m 와 1250 mm)나 줄 순서만 다른 재등록은 정규화한 뒤 비교해서 "변경 없음"으로 처리한다. 추출기를 다시 돌려 표기가 바뀌어도 가짜 개정이 생기지 않는다.
3. **오류는 한 번에 전부 돌려준다**: 도면 담당자가 한 번에 다 고칠 수 있도록 하기 위해서다. 또 오류 하나가 다른 가짜 오류를 낳지 않게 했다(AI_LOG 오류 #1).
4. **Decimal 계산**: 0.1 m × 3 = 300 mm 가 정확히 나온다. 이진 부동소수 오차가 없다.
5. **판 두께는 항상 mm**: 단위 전환(mm/m)은 길이에만 적용한다. 현장 관례를 따른 것이다(AI_LOG 오류 #2).
6. 유효일은 개정마다 늘어나야 한다. 과거 날짜로 끼워 넣는 개정은 거부한다(`REVISION_ORDER`).

## 오류 코드

`422 BOM_INVALID` 의 `errors[]`: CYCLE(경로 포함), UNKNOWN_PART, DUPLICATE_LINE, DUPLICATE_ITEM, PART_HAS_CHILDREN,
ORPHAN_ITEM, EMPTY_ASSEMBLY, ROOT_MISSING, ROOT_HAS_PARENT, QTY_NOT_POSITIVE, NUMBER_INVALID, UNIT_UNSUPPORTED,
MEASURE_INVALID, MEASURE_NOT_POSITIVE, KIND_INVALID, MISSING_FIELD, DATE_INVALID
/ `409` REVISION_EXISTS, REVISION_ORDER / `404` PRODUCT_NOT_FOUND, REVISION_NOT_FOUND

## 알려진 한계

- 과제 02(도면 추출)가 아직 없어 입력 JSON 은 직접 만든 합성 데이터다
- 중량(kg) 계산은 하지 않는다. 재질 밀도와 치수가 필요하며, 과제 04(견적)에서 다룬다
- 같은 부품이 두 조립품에 다른 길이로 들어가면 총 길이는 맞게 합산되지만, 화면의 "1개당 길이"는 줄별로만 표시된다
