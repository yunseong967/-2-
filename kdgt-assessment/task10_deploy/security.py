"""API 키(서버 측 해시 보관) · 역할 · 비밀값 가리기 · 감사 로그.

키 원문은 발급할 때 한 번만 화면에 보여 주고, 파일에는 SHA-256 해시만 저장한다.
감사 로그에는 키 원문이 아니라 key_id(발급 번호)만 남기고, 기록 직전에 비밀값 패턴을 한 번 더 지운다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

ROLES = ("admin", "viewer")
KEY_PREFIX = "kdgt_"

SECRET_PATTERNS = [
    re.compile(r"kdgt_[A-Za-z0-9_\-]{16,}"),            # 이 서비스의 API 키
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),            # Anthropic API 키
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),   # Authorization 헤더 값
    re.compile(r"(?i)(x-api-key[\"':=\s]+)[A-Za-z0-9._\-]{8,}"),
]


def scrub(text: str) -> str:
    for p in SECRET_PATTERNS:
        text = p.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", text)
    return text


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class KeyStore:
    """keys.json: {key_id: {name, role, sha256, created_at, revoked}} — 원문은 저장하지 않는다."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def add(self, name: str, role: str) -> tuple[str, str]:
        if role not in ROLES:
            raise ValueError(f"role 은 {ROLES}")
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        with self.lock:
            data = self._load()
            key_id = f"k{len(data) + 1:03d}"
            data[key_id] = {"name": name, "role": role, "sha256": hashlib.sha256(key.encode()).hexdigest(),
                            "created_at": now_iso(), "revoked": False}
            self._save(data)
        return key_id, key

    def revoke(self, key_id: str) -> bool:
        with self.lock:
            data = self._load()
            if key_id not in data:
                return False
            data[key_id]["revoked"] = True
            self._save(data)
            return True

    def list(self) -> list[dict]:
        return [{"key_id": k, **{f: v[f] for f in ("name", "role", "created_at", "revoked")}} for k, v in self._load().items()]

    def verify(self, presented: str | None) -> tuple[str, str] | None:
        """(key_id, role) 또는 None. 상수 시간 비교."""
        if not presented:
            return None
        h = hashlib.sha256(presented.encode()).hexdigest()
        found = None
        for key_id, v in self._load().items():
            if hmac.compare_digest(v["sha256"], h) and not v["revoked"]:
                found = (key_id, v["role"])
        return found


def presented_key(headers: dict) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key")


class AuditLog:
    """JSON Lines 감사 로그. 한 줄 = 한 요청."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def write(self, **fields):
        line = scrub(json.dumps({"ts": now_iso(), **fields}, ensure_ascii=False, default=str))
        with self.lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def tail(self, n: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines]
