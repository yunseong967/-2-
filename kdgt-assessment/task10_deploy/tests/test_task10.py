"""과제 10 테스트. 실행: (kdgt-assessment 폴더에서) python -m pytest
전체 시나리오(로컬 프로세스)는 약 30초 걸린다. Docker 시나리오는 python -m task10_deploy.verify --runner docker."""
import json
import time

import pytest

from task06_lot_traceability import service as svc
from task10_deploy import backup as B, verify
from task10_deploy.app import Breaker
from task10_deploy.security import KeyStore, presented_key, scrub


# ---------------------------------------------------------------- 키·비밀값
def test_keystore_stores_hash_only_and_verifies(tmp_path):
    ks = KeyStore(tmp_path / "keys.json")
    kid, key = ks.add("관리자", "admin")
    raw = (tmp_path / "keys.json").read_text()
    assert key not in raw and json.loads(raw)[kid]["sha256"]
    assert ks.verify(key) == (kid, "admin")
    assert ks.verify(key + "x") is None and ks.verify(None) is None
    ks.revoke(kid)
    assert ks.verify(key) is None
    with pytest.raises(ValueError):
        ks.add("x", "root")


@pytest.mark.parametrize("text", [
    "Authorization: Bearer kdgt_AbCdEfGhIjKlMnOpQrStUvWxYz0123",
    "key=sk-ant-api03-SECRETSECRET-123", '{"x-api-key": "abcdefghijklmnop"}', "kdgt_ZZZZZZZZZZZZZZZZZZZZ"])
def test_scrub_removes_secrets(text):
    out = scrub(text)
    assert "[REDACTED]" in out and "SECRET" not in out and "ZZZZZZZZZZZZZZZZ" not in out and "AbCdEf" not in out


def test_presented_key():
    assert presented_key({"authorization": "Bearer abc"}) == "abc"
    assert presented_key({"x-api-key": "k"}) == "k"
    assert presented_key({}) is None


# ---------------------------------------------------------------- 백업·복원
@pytest.fixture
def db(tmp_path):
    p = tmp_path / "lot.db"
    c = svc.connect(p)
    svc.init_db(c)
    svc.receive(c, "M-1", "SUS304", 100, "KG")
    svc.inspect(c, "M-1", "PASS", "QC")
    c.close()
    return p


def test_backup_restore_roundtrip(db, tmp_path):
    m = B.backup(db, tmp_path / "bk")
    c = svc.connect(db)
    svc.receive(c, "M-2", "SUS304", 5, "KG")
    c.close()
    assert B.stats(db)["lots.count"] == 2
    with open(db, "r+b") as f:
        f.write(b"\0" * 4096)
    res = B.restore(tmp_path / "bk" / m["file"], db, tmp_path / "MAINT")
    assert res["match"] and B.stats(db) == m["stats"] and B.stats(db)["lots.count"] == 1
    assert not (tmp_path / "MAINT").exists()


def test_tampered_backup_is_refused(db, tmp_path):
    m = B.backup(db, tmp_path / "bk")
    f = tmp_path / "bk" / m["file"]
    data = bytearray(f.read_bytes())
    data[-10] ^= 0xFF
    f.write_bytes(bytes(data))
    before = db.read_bytes()
    with pytest.raises(B.RestoreError):
        B.restore(f, db, tmp_path / "MAINT")
    assert db.read_bytes() == before                      # 원본 DB 는 건드리지 않음


# ---------------------------------------------------------------- 차단기
def test_breaker_opens_after_threshold_and_recovers():
    b = Breaker(3, 0.2)
    for _ in range(2):
        b.record(False)
    assert b.allow()
    b.record(False)
    assert not b.allow() and b.state()["open"]
    time.sleep(0.25)
    assert b.allow()
    b.record(True)
    assert b.state()["consecutive_failures"] == 0


# ---------------------------------------------------------------- 전체 시나리오 (로컬 프로세스)
def test_full_scenario_local():
    checks, art = verify.run(verify.LocalRunner())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
