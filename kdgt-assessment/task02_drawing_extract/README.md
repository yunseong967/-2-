# 과제 02 — CAD 도면 객체 추출

> 부품 도면에서 **품번·재질·두께·치수·홀 개수·홀 지름**을 구조화한다.
> 읽지 못했거나 도면에 없는 값은 **추측하지 않고 '검토 필요'**로 돌려준다. 결과는 과제 03 BOM 입력 형식으로 이어진다.

## 실행 (kdgt-assessment 폴더에서)

```bash
# 시스템 도구: tesseract-ocr (+ kor), poppler-utils, 나눔 폰트(도면 생성용)
#   Ubuntu: sudo apt-get install tesseract-ocr tesseract-ocr-kor poppler-utils fonts-nanum
pip install -r requirements.txt                       # pdfplumber, opencv-python-headless, pytesseract, ezdxf, reportlab, pypdf
python -m task02_drawing_extract.render               # 도면 10장 + DXF 2장 생성 (이미 커밋됨)
python -m task02_drawing_extract.evaluate             # 개발용 10장 평가 → reports/accuracy_*.md, extracted/*.json
python -m task02_drawing_extract.holdout              # 처음 보는 도면 10장 평가 → reports/holdout_accuracy.md
python -m pytest task02_drawing_extract
```

## 완료 기준 대응

| 슬라이드 요구 | 결과 | 증빙 |
|---|---|---|
| 합성 도면 10장, **정답 JSON 먼저 고정** | 정답을 추출기보다 먼저 커밋 (`git log` 의 "정답 JSON 먼저 고정", 커밋 99a4d29) | `fixtures/answers/`, `drawings/` |
| 벡터 PDF 5 + 스캔 PDF 5 | 스캔은 텍스트 레이어 없는 이미지 PDF (기울기 −0.6~1.5°, 잡음, 흐림, JPEG 압축) | `drawings/D0x_*.pdf` |
| 단위 혼용·치수 누락 사례 | 단위 혼용 3장(D03 `1 cm`, D04 `0.96 m`/`960`, D08 `1.2 m`/`80 cm`), 누락 3장(D05 두께, D09 세로, D02 홀 지름) | 정답 `note` |
| **필드별 정확도와 처리시간 공개** | 개발용 98.6%(오답 0, 마스터 대조) / **처음 보는 도면 90.0%(오답 0)**, 벡터 ~30ms·스캔 ~3s | `reports/accuracy_*.md`, `reports/holdout_accuracy.md` |
| 누락은 추측 대신 검토 필요 | 누락 3건 모두 '검토 필요' + 사유. 홀 없는 도면은 0개 (없는 홀을 만들지 않음) | `test_missing_values_are_not_guessed` |
| 오류 분석 | 유형 5가지, 교차 검증이 잡은 것, 의도적으로 하지 않은 것 | `reports/error_analysis.md` |
| 지원 형식과 DWG 대응 방안 | 아래 표 + DXF 추출 구현 | `dxf.py` |

## 처리 방식

```
PDF ─┬ 텍스트 레이어 있음 (벡터) → pdfplumber: 글자·사각형·곡선 좌표
     └ 없음 (스캔) → 내장 이미지 추출 → 기울기 보정 → 흰 영역으로 외형·표제란 칸 → 필요한 칸만 tesseract OCR, 원은 내부 흰 영역
DXF ─── ezdxf: 외형 폴리라인·CIRCLE·TEXT·DIMENSION 실좌표 (OCR 없음)
           ↓
공통 판단: 표제란 라벨 옆 값 / 외형 아래 = 가로, 왼쪽 = 세로 / 'n-Ød THRU' 홀 지시
           ↓
교차 검증: 홀 지시 개수 ↔ 도형 원 개수, 치수 비율 ↔ 도형 비율, 재질 목록, 품번 마스터
           ↓
필드마다 {value, status: OK|REVIEW, evidence(근거), reason(검토 사유)}  →  과제 03 BOM items 형식
```

## 지원 형식과 DWG 대응 방안

| 형식 | 지원 | 방법 | 정확도 특성 |
|---|---|---|---|
| 벡터 PDF (CAD 에서 PDF 출력) | ✔ | 글자·도형 좌표 직접 읽기 | 가장 정확 (합성 100%) |
| 스캔 PDF / 이미지 PDF | ✔ | OCR(kor+eng) + OpenCV | 처음 보는 도면 80%, 놓치면 검토로 |
| DXF | ✔ | ezdxf 실좌표·실단위($INSUNITS) | OCR 오류 없음. 치수 엔터티와 도형 대조 |
| **DWG** | 변환 후 ✔ | **DWG → DXF 변환 후 DXF 경로 사용** | DXF 와 같음 |
| 이미지(PNG/JPG) | 일부 | 스캔 경로에서 PDF 변환 단계만 생략하면 됨 (미구현) | 스캔과 같음 |

**DWG 대응 방안** (DWG 는 Autodesk 독점 형식이라 직접 파싱하지 않는다)
1. **ODA File Converter** (Open Design Alliance, 무료 배포·상용 라이선스 별도 확인): DWG → DXF 일괄 변환을 서버 배치로 실행 → `dxf.py` 로 추출. 사내 설치형이라 도면이 외부로 나가지 않는다 → **1순위 권장**
2. **Autodesk Platform Services (Design Automation API)**: 사업계획서의 'Autodesk API' 경로. 클라우드에서 DWG 를 열어 객체(치수·블록 속성)를 직접 추출. 정확도는 가장 높지만 **도면이 외부 클라우드로 전송**되므로 승인 범위 확인 필요(과제 공통 규칙)
3. LibreDWG(GPL): 오픈소스지만 최신 DWG 버전 호환성과 GPL 라이선스 조건 확인 필요 → 보조 수단
4. DWG 의 **블록 속성(ATTRIB)**으로 표제란을 만든 경우 TEXT 위치 추정 대신 속성 태그(PART_NO, MATERIAL…)로 바로 읽도록 `dxf.py` 확장

## 알려진 한계

- 단순 판재 부품(사각 외형 + 원형 홀) 전용. 다중 뷰(정면·측면), 단면도, 곡선 외형, 공차·기하공차 표기는 다루지 않는다
- 표제란 위치는 오른쪽 아래, 라벨은 품번·품명·재질·두께·단위 로 가정 (회사 템플릿마다 설정 필요)
- 라벨을 비슷한 글자로 찾는 방식은 다른 행을 잡을 위험이 있다 (오류 분석 E3) → 템플릿 행 순서 방식으로 바꿀 것
- 정확도는 합성 도면 기준이며 실제 도면 성능과 다르다. 사업계획서 목표(객체 인식 80%)와 직접 비교하지 않는다
