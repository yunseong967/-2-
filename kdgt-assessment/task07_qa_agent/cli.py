"""과제 07 명령행.

  python -m task07_qa_agent.cli eval --backend mock     # 하네스 점검 (키 불필요)
  python -m task07_qa_agent.cli eval --backend naive    # 공격을 따르는 가짜 모델로 코드 가드 점검
  python -m task07_qa_agent.cli eval --backend claude   # 실제 평가 (ANTHROPIC_API_KEY 필요, 과금됨)
  python -m task07_qa_agent.cli ask "질문" --backend claude
  python -m task07_qa_agent.cli serve --backend claude  # http://127.0.0.1:8007 채팅 화면
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agent as A, evaluate as E
from .api import build_agent, make_server

HERE = Path(__file__).parent
DB = HERE.parent / "out" / "task07.db"


def backend_of(name: str, effort: str):
    if name == "claude":
        try:
            return A.ClaudeBackend(effort=effort)
        except Exception as e:
            sys.exit(f"Claude 백엔드를 만들 수 없습니다: {e}\nANTHROPIC_API_KEY 를 설정하세요.")
    return A.ScriptedBackend(name)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="task07")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("eval", "ask", "serve"):
        s = sub.add_parser(name)
        s.add_argument("--backend", choices=["claude", "mock", "naive"], default="mock")
        s.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
        if name == "eval":
            s.add_argument("--out", default=str(HERE / "reports"))
            s.add_argument("--only", nargs="*")
        if name == "ask":
            s.add_argument("question")
        if name == "serve":
            s.add_argument("--port", type=int, default=8007)
    a = p.parse_args(argv)
    if a.cmd == "eval":
        if a.backend == "claude":
            print("실제 Claude API 를 20문항 × 여러 턴 호출합니다 (과금).")
        res = E.run(backend_of(a.backend, a.effort), Path(a.out), a.only)
        s = res["summary"]
        print(f"[{a.backend}] 통과 {s['pass']}/{s['n']}  안전 전부 통과 {s['safety_all_ok']}  DB 불변 {s['db_unchanged']}")
        print(f"  응답 평균 {s['latency']['mean']}s p95 {s['latency']['p95']}s  비용 합계 ${s['cost_usd']['total']}  가드 {s['guards']}")
        for x in res["rows"]:
            if not x["g"]["pass"]:
                print(f"  실패 {x['q']['id']}: {x['g']['fails'] + x['g']['safety_fails']}")
        print(f"보고서: {a.out}/eval_{a.backend}.md, tool_log_{a.backend}.json")
        return 0
    if a.cmd == "ask":
        ag = A.Agent(DB, backend_of(a.backend, a.effort))
        from .tools import build_db
        build_db(DB)
        print(json.dumps(ag.ask(a.question).to_dict(), ensure_ascii=False, indent=1))
        return 0
    if a.backend == "claude":
        backend_of("claude", a.effort)
    server = make_server(build_agent(a.backend, DB, a.effort), port=a.port)
    print(f"http://127.0.0.1:{a.port}  (백엔드 {a.backend})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
