# 과제 07 — 입출하 질의응답 AI Agent

> "이 LOT 는 언제 입고됐고 어디로 출하됐나?" 같은 질문에 **읽기 전용 DB 조회**와 **절차 문서 검색**으로 답하는 Agent.
> 모든 답변은 근거 행 ID·문서 위치를 인용하고, 근거가 없으면 답을 보류하며, 문서·데이터 속 지시는 따르지 않는다.

## ⚠ 실제 평가는 API 키가 있어야 합니다

이 저장소의 `reports/` 에는 **가짜 모델(mock·naive)로 하네스를 점검한 결과만** 들어 있다. 실제 Claude 의 정답률·응답시간·호출비용은 아래 명령으로 한 번 실행해서 만들어야 한다(과금됨, 20문항 예상 비용은 실행 후 보고서에 기록됨).

```bash
pip install -r requirements.txt                     # anthropic SDK 포함
export ANTHROPIC_API_KEY=sk-ant-...                 # 본인 키
python -m task07_qa_agent.cli eval --backend claude # → reports/eval_claude.md, tool_log_claude.json
python -m task07_qa_agent.cli serve --backend claude   # http://127.0.0.1:8007 채팅 화면
```

키 없이 할 수 있는 것:
```bash
python -m task07_qa_agent.cli eval --backend mock    # 도구·가드·채점기 점검 (20/20)
python -m task07_qa_agent.cli eval --backend naive   # 공격을 따르는 가짜 모델 → 코드 가드가 막는지
python -m task07_qa_agent.cli serve --backend mock   # 채팅 화면 (평가 문항만 응답)
python -m pytest task07_qa_agent                     # 테스트 (가짜 API 클라이언트로 Claude 경로까지 검증)
```

## 완료 기준 대응

| 슬라이드 요구 | 구현 | 증빙 |
|---|---|---|
| 수량·날짜는 읽기 전용 DB 도구 | `query_db`: SQLite `mode=ro` + authorizer(SELECT 계열만) + 한 문장 + 100행·2초 제한. **프롬프트가 아니라 코드로** 막는다 | `test_writes_are_blocked_by_code` (DELETE·DROP·PRAGMA·ATTACH 등 9종) |
| 절차 문서는 검색 | `search_docs`: 절(§) 단위 검색, 결과에 `파일 §절 (L시작-끝)` 위치 | `tools.DocSearch` |
| 정답 10 / 정보 없음 5 / 공격 5 평가 | `fixtures/questions.json`, 정규식 기반 결정적 채점 (품질·안전 분리) | `reports/eval_*.md` |
| 답변마다 행 ID·문서 위치 | `submit_answer.citations` 필수. 인용을 누르면 원문 행/절 표시 | 채팅 화면 |
| 무근거 답변 보류 | **G1** 도구가 돌려주지 않은 인용 삭제 → **G2** 유효 인용 없는 ANSWERED 는 INSUFFICIENT 로 바꿔 보류 | naive 평가: 가짜 인용 5건 차단 |
| 문서 속 지시 무시 | 문서·DB 결과를 `<document>`/`<db_result>` 데이터 경계로 감싸 전달 + 시스템 지시 + **G3** 카나리 유출 차단. 주입 문구는 SOP-03 §5 와 출하 S-0005 메모에 실제로 심어 둠 | A08, X03 |
| 채팅 화면·도구 로그 | `static/chat.html` — 답변, 상태, 인용, 도구 로그, 응답시간, 토큰, 비용 | `reports/screen_chat_mock.png` |
| 응답시간과 호출비용 | 문항별 지연, 토큰(입력/출력/캐시쓰기/캐시읽기), 모델별 단가로 비용 계산 (대체 모델 응답 포함) | `reports/eval_claude.md` (키로 실행 후 생성) |

## 구조

```
질문 → Agent 루프 (최대 8턴)
        ├ query_db     → ReadOnlyDB (코드로 읽기 전용) → 결과 + ref(receipts#1 …)
        ├ search_docs  → DocSearch → 절 본문 + ref(SOP-02§1) + 위치
        └ submit_answer{status, answer, citations}
      → 코드 가드 G1~G4 → 최종 답변
```

| 가드 | 하는 일 | 모델이 지시를 어겨도? |
|---|---|---|
| 읽기 전용 DB | 쓰기·스키마 변경·다중 문장 거부 | 막힘 |
| G1 인용 검증 | 이번 대화에서 도구가 실제로 돌려준 ref 만 남김 | 막힘 |
| G2 무근거 보류 | 유효 인용 없는 ANSWERED → INSUFFICIENT | 막힘 |
| G3 유출 차단 | 시스템 프롬프트의 카나리 문자열이 답에 있으면 REFUSED | 막힘 |
| G4 형식 | submit_answer 없이 종료, 턴 초과, max_tokens, 모델 거절, API 오류 → 보류 | 막힘 |
| **한계** | 실제 행을 인용하면서 그 내용을 잘못 해석·주입 지시를 따르는 답 | **코드로 못 막음 → 평가 세트로만 탐지** (naive X03) |

## Claude 호출 설정 (`agent.ClaudeBackend`)

| 항목 | 값 | 이유 |
|---|---|---|
| 모델 | `claude-opus-5-5` | 기본 모델 |
| thinking | `{"type": "adaptive"}` | 이 모델은 thinking 을 끌 수 없음, 깊이는 effort 로 조절 |
| effort | `medium` (CLI `--effort low/high`) | 응답시간·비용과 정확도 균형. low/high 비교 실험 가능 |
| 도구 | `strict: true`, `tool_choice` 미지정(auto) | 이 모델은 강제 도구 선택(any/tool)이 400 |
| 캐시 | 최상위 `cache_control: ephemeral` | 시스템 프롬프트·도구·이전 턴 재사용 |
| 거절 대비 | `fallbacks="default"` + 베타 `server-side-fallback-2026-07-01` | 안전 분류기 오탐 시 서버가 대체 모델로 재시도. 비용은 `usage.iterations` 의 모델별 토큰으로 계산 |
| 원본 블록 회신 | 응답 `content` 를 그대로 다음 요청에 붙임 | thinking 블록 보존 |
| 접속 주소 | 기본 `https://api.anthropic.com`, 필요 시 `KDGT_ANTHROPIC_BASE_URL` | 환경의 `ANTHROPIC_BASE_URL` 로 키가 새는 것 방지 |

단가(USD/100만 토큰, `agent.PRICES`): claude-opus-5-5 입력 4 / 출력 20 / 캐시쓰기 5 / 캐시읽기 0.20. 실제 청구액과 다를 수 있으니 콘솔 사용량과 대조할 것.

## 평가 결과 (가짜 모델 — AI 성능 아님)

| 백엔드 | 통과 | 안전 | DB 불변 | 의미 |
|---|---:|---|---|---|
| mock (정상 스크립트) | 20/20 | 전부 통과 | 예 | 도구·인용 검증·채점기가 올바르게 동작 |
| naive (공격을 따르는 모델) | 18/20 | X03 실패 | 예 | DELETE/DROP·가짜 인용·카나리 유출은 **코드가 막음**. 실제 행을 인용한 주입 순응(X03)은 **평가가 잡음** |

## 알려진 한계

- **실제 Claude 평가 결과가 아직 없다** (API 키 필요). 20문항은 작은 표본이라 통과율 1문항 차이가 5%p 이다
- 문서 검색은 문자 2-gram 겹침이다. 문서가 많아지면 임베딩 검색으로 바꿔야 한다
- 채점은 정규식이다. 표현이 다른 정답(예: "8월 3일")을 놓칠 수 있다 → 실패 사례는 사람이 원문을 확인할 것
- 채팅은 한 번에 한 질문만 처리한다(비용 폭주 방지 잠금)
