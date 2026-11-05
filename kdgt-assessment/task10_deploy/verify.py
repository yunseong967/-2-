"""과제 10 시나리오 검증 — 같은 시나리오를 로컬 프로세스(LocalRunner) 또는 Docker 컨테이너(DockerRunner)로 실행.

  1. 새 환경에서 기동 (빈 데이터 볼륨) → /healthz, /readyz
  2. 키 발급(관리자·조회자·폐기 시험용) → 권한 테스트 표
  3. 관리자로 과제 06 시나리오 데이터 적재 → 백업 → 백업 후 추가 쓰기
  4. 장애 A  DB 잠금(쓰기 중단): 조회는 계속, 쓰기는 503 + Retry-After, 잠금 해제 후 재시작 없이 회복
  5. 장애 B  DB 파일 손상: 모든 조회 503, 프로세스는 생존 → 백업 복원 → 건수·합계 = 백업 manifest
  6. 장애 C  LLM 시간초과: 504 를 제한 시간 안에 반환, 그동안 다른 API 정상, 3회 실패 후 차단기 즉시 503
  7. 재시작 후 데이터·키 유지, 폐기 키 401
  8. 비밀값 검사: 감사 로그·서버 로그·키 파일·응답 어디에도 API 키 원문 / Anthropic 키 없음
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
FAKE_ANTHROPIC_KEY = "sk-ant-api03-FAKEKEY-for-timeout-test-0123456789abcdef"
LLM_TIMEOUT = 3.0


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(base, method, path, key=None, body=None, timeout=30):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null"), dict(r.headers), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), dict(e.headers), time.perf_counter() - t0


# ---------------------------------------------------------------- 실행기
class LocalRunner:
    name = "local"

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t10-"))
        self.data = self.tmp / "data"
        self.port, self.llm_port = free_port(), free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "KDGT_DATA_DIR": str(self.data), "KDGT_LLM_TIMEOUT": str(LLM_TIMEOUT),
                    "KDGT_LLM_BACKEND": "claude", "ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY,
                    "KDGT_ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.llm_port}", "KDGT_PORT": str(self.port),
                    "PYTHONPATH": str(ROOT)}
        self.proc = self.llm = None
        self.log = self.tmp / "server.log"

    def _cmd(self, *args):
        return [sys.executable, "-m", "task10_deploy.cli", *args]

    def start(self):
        if self.llm is None:
            self.llm = subprocess.Popen(self._cmd("slow-llm", "--port", str(self.llm_port), "--delay", "60"),
                                        env=self.env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.proc = subprocess.Popen(self._cmd("serve", "--host", "127.0.0.1", "--port", str(self.port)), env=self.env,
                                     cwd=ROOT, stdout=open(self.log, "a"), stderr=subprocess.STDOUT)
        wait_healthy(self.base)

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)

    def restart(self):
        self.stop()
        self.start()

    def exec(self, *args) -> str:
        r = subprocess.run(self._cmd(*args), env=self.env, cwd=ROOT, capture_output=True, text=True, timeout=120)
        return r.stdout + r.stderr

    def exec_bg(self, *args):
        return subprocess.Popen(self._cmd(*args), env=self.env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def read_file(self, rel) -> str:
        p = self.data / rel
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

    def logs(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""

    def cleanup(self):
        self.stop()
        if self.llm:
            self.llm.terminate()
        shutil.rmtree(self.tmp, ignore_errors=True)


class DockerRunner:
    name = "docker"
    IMAGE, NET, APP, LLM = "kdgt-lot:task10", "kdgt-t10-net", "kdgt-t10-app", "kdgt-t10-slowllm"

    def __init__(self, build=True):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.volume = f"kdgt-t10-data-{int(time.time())}"      # 매번 새 볼륨 = 새 환경
        self.build = build
        self.build_log = ""

    def sh(self, *cmd, check=True, timeout=600) -> str:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
        return r.stdout + r.stderr

    def start(self):
        if self.build:
            extra = []
            if os.environ.get("HTTPS_PROXY"):        # 이 개발 환경처럼 프록시를 거쳐야 할 때만
                extra = ["--network", "host", "--build-arg", f"HTTPS_PROXY={os.environ['HTTPS_PROXY']}"]
                ca = os.environ.get("KDGT_BUILD_CA", "/root/.ccr/ca-bundle.crt")
                if Path(ca).exists():
                    extra += ["--secret", f"id=ca,src={ca}"]
            self.build_log = self.sh("docker", "build", *extra, "-f", str(HERE / "deploy" / "Dockerfile"), "-t", self.IMAGE, str(ROOT))
            self.build = False
            self.sh("docker", "rm", "-f", self.APP, self.LLM, check=False)
            self.sh("docker", "network", "rm", self.NET, check=False)
            self.sh("docker", "network", "create", self.NET)
            self.sh("docker", "volume", "create", self.volume)
            self.sh("docker", "run", "-d", "--name", self.LLM, "--network", self.NET, self.IMAGE,
                    "python", "-m", "task10_deploy.cli", "slow-llm", "--port", "9999", "--delay", "60")
            self.sh("docker", "run", "-d", "--name", self.APP, "--network", self.NET, "-p", f"127.0.0.1:{self.port}:8010",
                    "-v", f"{self.volume}:/data", "-e", f"KDGT_LLM_TIMEOUT={LLM_TIMEOUT}",
                    "-e", f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC_KEY}", "-e", f"KDGT_ANTHROPIC_BASE_URL=http://{self.LLM}:9999",
                    "--restart", "unless-stopped", self.IMAGE)
        else:
            self.sh("docker", "start", self.APP)
        wait_healthy(self.base)

    def restart(self):
        self.sh("docker", "restart", self.APP)
        wait_healthy(self.base)

    def exec(self, *args) -> str:
        return self.sh("docker", "exec", self.APP, "python", "-m", "task10_deploy.cli", *args, check=False)

    def exec_bg(self, *args):
        return subprocess.Popen(["docker", "exec", self.APP, "python", "-m", "task10_deploy.cli", *args],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def read_file(self, rel) -> str:
        return self.sh("docker", "exec", self.APP, "sh", "-c", f"cat /data/{rel} 2>/dev/null || true", check=False)

    def logs(self) -> str:
        return self.sh("docker", "logs", self.APP, check=False)

    def info(self) -> dict:
        insp = json.loads(self.sh("docker", "inspect", self.APP))[0]
        img = json.loads(self.sh("docker", "image", "inspect", self.IMAGE))[0]
        return {"user": insp["Config"].get("User"), "health": insp["State"].get("Health", {}).get("Status"),
                "image_size_mb": round(img["Size"] / 1e6, 1), "restart_policy": insp["HostConfig"]["RestartPolicy"]["Name"]}

    def cleanup(self):
        self.sh("docker", "rm", "-f", self.APP, self.LLM, check=False)
        self.sh("docker", "network", "rm", self.NET, check=False)
        self.sh("docker", "volume", "rm", self.volume, check=False)


def wait_healthy(base, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if http(base, "GET", "/healthz", timeout=2)[0] == 200:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("서버가 뜨지 않음")


# ---------------------------------------------------------------- 시나리오
@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Timeline:
    t0: float = field(default_factory=time.time)
    rows: list = field(default_factory=list)

    def add(self, step, what, result):
        self.rows.append((round(time.time() - self.t0, 2), step, what, result))


def jl(out: str) -> dict:
    for line in reversed(out.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    raise ValueError(out)


def run(runner, out: Path | None = None) -> tuple[list[Check], dict]:
    from task06_lot_traceability.scenario import STEPS
    checks, tl, art = [], Timeline(), {"perm": []}
    try:
        runner.start()
        b = runner.base
        tl.add("기동", "새 데이터 볼륨으로 서비스 시작", f"healthz {http(b, 'GET', '/healthz')[0]}, readyz {http(b, 'GET', '/readyz')[0]}")
        ready = http(b, "GET", "/readyz")[0] == 200

        admin = jl(runner.exec("keys", "add", "--name", "품질팀장", "--role", "admin"))
        viewer = jl(runner.exec("keys", "add", "--name", "현장조회", "--role", "viewer"))
        spare = jl(runner.exec("keys", "add", "--name", "퇴사자", "--role", "admin"))
        A, V = admin["api_key"], viewer["api_key"]
        checks.append(Check("새 환경에서 기동 (빈 볼륨 → 스키마 생성 → 준비 완료)", ready, "healthz 200, readyz 200"))

        # 1) 권한
        recv = {"lot_id": "PERM-1", "item": "SUS304 6t", "qty": 10, "unit": "KG"}
        matrix = [
            ("키 없이 조회", "GET", "/api/lots", None, None, 401),
            ("잘못된 키로 조회", "GET", "/api/lots", "kdgt_wrongwrongwrongwrongwrong", None, 401),
            ("조회자 조회", "GET", "/api/lots", V, None, 200),
            ("조회자 쓰기(입고)", "POST", "/api/receipts", V, recv, 403),
            ("조회자 쓰기(출하)", "POST", "/api/shipments", V, {"request_id": "X", "lot_id": "P", "qty": 1, "customer": "c"}, 403),
            ("조회자 백업 실행", "POST", "/admin/backup", V, {}, 403),
            ("조회자 감사 로그 조회", "GET", "/admin/audit", V, None, 403),
            ("관리자 쓰기(입고)", "POST", "/api/receipts", A, recv, 201),
            ("관리자 감사 로그 조회", "GET", "/admin/audit?n=5", A, None, 200),
            ("인증 없는 헬스체크", "GET", "/healthz", None, None, 200),
        ]
        bad = []
        for desc, m, path, key, body, exp in matrix:
            s = http(b, m, path, key, body)[0]
            art["perm"].append((desc, m, path, exp, s, s == exp))
            if s != exp:
                bad.append(f"{desc}: {s}≠{exp}")
        lots = http(b, "GET", "/api/lots", A)[1]
        checks.append(Check(f"권한 테스트 {len(matrix)}건 (조회자 쓰기 거부 포함)", not bad and sum(l["lot_id"] == "PERM-1" for l in lots) == 1,
                            "; ".join(bad) or "전부 기대대로 (조회자 쓰기 403, 키 없음 401, 관리자 쓰기 201)"))
        tl.add("권한", "권한 테스트 표 실행", f"{len(matrix) - len(bad)}/{len(matrix)} 기대대로")

        # 2) 데이터 적재 → 백업 → 추가 쓰기
        bad = []
        for st in STEPS:
            s = http(b, st.method, st.path, A, st.body)[0]
            if s != st.expect_status:
                bad.append(f"{st.title}: {s}")
        tl.add("데이터", f"과제 06 시나리오 {len(STEPS)}단계를 관리자 키로 적재", "정답 일치" if not bad else str(bad[:3]))
        s, man, _, _ = http(b, "POST", "/admin/backup", A, {})
        tl.add("백업", "관리자 POST /admin/backup", f"{s} {man.get('file')} 건수·합계 {man.get('stats')}")
        for i in range(3):
            http(b, "POST", "/api/receipts", A, {"lot_id": f"AFTER-{i}", "item": "SS400", "qty": 50, "unit": "KG"})
        live_before_disaster = jl(runner.exec("stats"))["stats"]
        tl.add("데이터", "백업 이후 입고 3건 추가", f"lots {man['stats']['lots.count']} → {live_before_disaster['lots.count']}")
        art["manifest"] = man

        # 3) 장애 A: DB 잠금
        locker = runner.exec_bg("db-lock", "--seconds", "8")
        locker.stdout.readline()                                 # {"locked": true}
        tl.add("장애 A", "DB 배타 잠금 8초 시작 (쓰기 중단)", "")
        rs = http(b, "GET", "/api/lots", V)
        ws = http(b, "POST", "/api/receipts", A, {"lot_id": "LOCKED-1", "item": "SS400", "qty": 1, "unit": "KG"})
        rz = http(b, "GET", "/readyz")
        tl.add("장애 A", "잠금 중 조회 / 쓰기", f"조회 {rs[0]} ({rs[3]:.2f}s), 쓰기 {ws[0]} {ws[1].get('error')} ({ws[3]:.2f}s, Retry-After {ws[2].get('Retry-After')})")
        locker.wait(30)
        ws2 = http(b, "POST", "/api/receipts", A, {"lot_id": "LOCKED-1", "item": "SS400", "qty": 1, "unit": "KG"})
        tl.add("장애 A", "잠금 해제 후 같은 쓰기 재시도 (재시작 없음)", f"{ws2[0]}")
        ok = rs[0] == 200 and ws[0] == 503 and ws[1].get("error") == "DB_LOCKED" and ws[2].get("Retry-After") and ws[3] < 6 and ws2[0] == 201
        checks.append(Check("장애 A: DB 잠금 → 쓰기 503(빠른 실패), 조회 유지, 해제 후 자동 회복", ok,
                            f"조회 {rs[0]}, 쓰기 {ws[0]} {ws[1].get('error')} {ws[3]:.1f}s 만에 응답, 해제 후 {ws2[0]}"))

        # 4) 장애 B: DB 손상 → 복원
        expect = jl(runner.exec("stats"))["stats"]
        runner.exec("corrupt")
        tl.add("장애 B", "DB 파일 앞 4KB 훼손 (디스크 손상 재현)", "")
        g = http(b, "GET", "/api/lots", V)
        rz = http(b, "GET", "/readyz")
        hz = http(b, "GET", "/healthz")
        tl.add("장애 B", "손상 상태 조회 / readyz / healthz", f"{g[0]} {g[1].get('error')}, readyz {rz[0]}, healthz {hz[0]} (프로세스 생존)")
        res = jl(runner.exec("restore"))
        tl.add("복원", "최신 백업 검증(해시·integrity·manifest) 후 원자적 교체", f"match={res.get('match')} {res.get('stats')}")
        g2 = http(b, "GET", "/api/lots", V)
        rz2 = http(b, "GET", "/readyz")
        trace = http(b, "GET", "/api/shipments/1/trace", V)
        ok = (g[0] == 503 and rz[0] == 503 and hz[0] == 200 and res.get("match") is True
              and res["stats"] == man["stats"] and g2[0] == 200 and rz2[0] == 200 and trace[0] == 200)
        lost = expect["lots.count"] - res.get("stats", {}).get("lots.count", 0)
        checks.append(Check("장애 B: DB 손상 → 503, 백업 복원 후 건수·합계 일치", ok,
                            f"손상 시 조회 {g[0]}·readyz {rz[0]}·healthz {hz[0]} / 복원 후 {res.get('stats')} = 백업 {man['stats']} / "
                            f"복원 후 조회 {g2[0]}, 추적 {trace[0]} / 백업 이후 쓰기 {lost}건은 손실(RPO = 마지막 백업)"))
        art["restore"] = res

        # 5) 장애 C: LLM 시간초과
        results = []
        def slow_call():
            results.append(http(b, "POST", "/api/assist", V, {"question": "LOT M-A는 언제 입고됐나?"}, timeout=60))
        th = threading.Thread(target=slow_call)
        th.start()
        time.sleep(0.3)
        meanwhile = http(b, "GET", "/api/lots", V)
        th.join()
        first = results[0]
        art["llm_times"] = [round(first[3], 2)]
        tl.add("장애 C", f"LLM 무응답 서버 상대로 질의 (제한 {LLM_TIMEOUT}s)",
               f"{first[0]} {first[1].get('error')} {first[3]:.2f}s / 그 사이 조회 {meanwhile[0]} {meanwhile[3]:.2f}s")
        more = [http(b, "POST", "/api/assist", V, {"question": "M-B 입고 수량은?"}, timeout=60) for _ in range(2)]
        fast = http(b, "POST", "/api/assist", V, {"question": "M-C는 출하됐나?"}, timeout=60)
        tl.add("장애 C", "연속 실패 3회 후 4번째 질의", f"{fast[0]} {fast[1].get('error')} {fast[3]:.2f}s (차단기 열림, Retry-After {fast[2].get('Retry-After')})")
        ok = (first[0] == 504 and first[1].get("error") == "LLM_TIMEOUT" and first[3] < LLM_TIMEOUT + 1.0
              and meanwhile[0] == 200 and meanwhile[3] < 1.5 and all(m[0] == 504 for m in more)
              and fast[0] == 503 and fast[1].get("error") == "LLM_UNAVAILABLE" and fast[3] < 1.0)
        checks.append(Check("장애 C: LLM 시간초과 → 504, 다른 API 영향 없음, 3회 후 차단기", ok,
                            f"1회 {first[0]} {first[3]:.1f}s (제한 {LLM_TIMEOUT}s), 그 사이 조회 {meanwhile[0]} {meanwhile[3]:.2f}s, "
                            f"2·3회 {[m[0] for m in more]}, 4회 {fast[0]} {fast[1].get('error')} {fast[3]:.2f}s"))

        # 6) 재시작·폐기
        runner.exec("keys", "revoke", spare["key_id"])
        runner.restart()
        after = http(b, "GET", "/api/lots", V)
        rev = http(b, "GET", "/api/lots", spare["api_key"])
        ok = after[0] == 200 and len(after[1]) == res["stats"]["lots.count"] and rev[0] == 401
        checks.append(Check("재시작 후 데이터·키 유지, 폐기 키 거부", ok,
                            f"재시작 후 조회 {after[0]} LOT {len(after[1])}개, 폐기 키 {rev[0]}"))
        tl.add("재시작", "컨테이너/프로세스 재시작", f"조회 {after[0]}, 폐기 키 {rev[0]}")

        # 7) 비밀값 검사
        audit = runner.read_file("audit.log")
        keysfile = runner.read_file("keys.json")
        logs = runner.logs()
        audit_api = json.dumps(http(b, "GET", "/admin/audit?n=500", A)[1], ensure_ascii=False)
        secrets_ = {"관리자 키": A, "조회자 키": V, "폐기 키": spare["api_key"], "Anthropic 키": FAKE_ANTHROPIC_KEY}
        leaks = [f"{n} in {where}" for n, s in secrets_.items()
                 for where, text in (("audit.log", audit), ("keys.json", keysfile), ("서버 로그", logs), ("감사 API 응답", audit_api))
                 if s in text]
        n_audit = len(audit.strip().splitlines())
        roles = {json.loads(l).get("role") for l in audit.strip().splitlines()}
        statuses = sorted({json.loads(l).get("status") for l in audit.strip().splitlines()})
        n500 = sum(1 for l in audit.strip().splitlines() if json.loads(l).get("status") == 500)
        dup_conflicts = sum(1 for l in audit.strip().splitlines() if json.loads(l).get("status") == 409)
        checks.append(Check("감사 로그 상태코드가 실제 응답과 일치 (예상치 못한 500 없음)", n500 == 0 and dup_conflicts > 0,
                            f"500 {n500}건, 업무 거부 409 {dup_conflicts}건이 409 로 기록됨"))
        checks.append(Check("로그·키 파일에 비밀값 없음", not leaks and n_audit > 0 and '"sha256"' in keysfile,
                            f"검사 4곳 × 비밀값 4종 → 노출 {leaks or '0건'}; 감사 로그 {n_audit}줄 (역할 {sorted(r for r in roles if r)}, 상태코드 {statuses}); keys.json 은 해시만"))
        art.update(timeline=tl.rows, audit_sample=[json.loads(l) for l in audit.strip().splitlines()[-6:]])
        if isinstance(runner, DockerRunner):
            art["docker"] = runner.info()
            art["build_tail"] = runner.build_log.strip().splitlines()[-3:]
    finally:
        if out:
            _report(Path(out), runner, checks, art)
        runner.cleanup()
    return checks, art


def _report(out: Path, runner, checks, art):
    out.mkdir(parents=True, exist_ok=True)
    L = [f"# 과제 10 검증 결과 — 실행기 `{runner.name}`", "",
         f"- 재실행: `python -m task10_deploy.verify --runner {runner.name}`"]
    if art.get("docker"):
        L += [f"- 컨테이너: 사용자 `{art['docker']['user']}`(root 아님), 재시작 정책 `{art['docker']['restart_policy']}`, "
              f"이미지 {art['docker']['image_size_mb']}MB, 매 실행 새 볼륨(새 환경)"]
    L += ["", "## 판정", "", "| 결과 | 항목 | 근거 |", "|---|---|---|",
          *[f"| {'PASS' if c.ok else 'FAIL'} | {c.name} | {c.detail} |" for c in checks], "",
          "## 권한 테스트", "", "| 시험 | 요청 | 기대 | 실제 | 판정 |", "|---|---|---:|---:|---|",
          *[f"| {d} | {m} {p} | {e} | {s} | {'일치' if ok else '불일치'} |" for d, m, p, e, s, ok in art.get("perm", [])], "",
          "## 장애·복구 타임라인 (경과 초)", "", "| 경과(s) | 단계 | 한 일 | 결과 |", "|---:|---|---|---|",
          *[f"| {t} | {s} | {w} | {str(r).replace('|', '/')} |" for t, s, w, r in art.get("timeline", [])], "",
          "## 감사 로그 예시 (마지막 6줄, 키 원문 없음)", "", "```",
          *[json.dumps(x, ensure_ascii=False) for x in art.get("audit_sample", [])], "```", ""]
    (out / f"verification_{runner.name}.md").write_text("\n".join(L), encoding="utf-8")


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--runner", choices=["local", "docker"], default="local")
    p.add_argument("--out", default=str(HERE / "reports"))
    a = p.parse_args()
    runner = DockerRunner() if a.runner == "docker" else LocalRunner()
    checks, _ = run(runner, Path(a.out))
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
    sys.exit(0 if all(c.ok for c in checks) else 1)


if __name__ == "__main__":
    main()
