"""registry 계약 — 역할↔서버 주소매칭 결정성."""
import pytest

from agent.registry import (Registry, RegistryError, ServerPool, build_role,
                            DEFAULT_ROLES)


def test_default_roles_exist():
    reg = Registry()
    assert set(reg.roles()) == {"parser", "worker", "coder", "reasoner", "embed"}
    assert reg.role("parser").base_url == "http://127.0.0.1:8081"
    # worker/coder 는 다중 pool
    assert len(reg.role("worker").urls) == 4
    assert reg.role("worker").urls[0] == "http://127.0.0.1:8082"
    assert len(reg.role("coder").urls) == 2
    assert reg.role("embed").kind == "embed"


def test_unknown_role_raises():
    reg = Registry()
    with pytest.raises(RegistryError):
        reg.role("nope")


def test_overrides_create_snapshot_not_mutate():
    reg = Registry()
    g = reg.with_overrides(parser="http://x:9000", workers=["http://w:9001"])
    assert g.role("parser").base_url == "http://x:9000"
    assert reg.role("parser").base_url == "http://127.0.0.1:8081"  # 원본 불변
    assert g.role("worker").urls == ("http://w:9001",)


def test_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_PARSER", "http://10.0.0.5:8081")
    r = build_role("parser")
    assert r.base_url == "http://10.0.0.5:8081"


def test_server_pool_rotates_worker():
    reg = Registry()
    # worker pool: 라운드로분배
    pool = ServerPool(reg)
    got = [pool.next("worker").role_index for _ in range(4)]
    assert got == [0, 1, 2, 3]
    # 5번째는 처음으로 순환 (server 만 4대니 url 도 반복)
    assert pool.next("worker").role_index == 0


def test_pool_single_urls_not_rotated():
    pool = ServerPool(Registry())
    s = pool.next("parser")
    assert s.role_index == 0 and s.url == "http://127.0.0.1:8081"


def test_default_ports_match_myllm():
    """실서버 myllm config 와 일치해야 한다 — 회귀 가드."""
    reg = Registry()
    assert reg.role("parser").urls[0].endswith("8081")
    assert [u.rpartition(":")[2] for u in reg.role("worker").urls] == ["8082","8083","8084","8085"]
    assert [u.rpartition(":")[2] for u in reg.role("coder").urls] == ["8086","8087"]
    assert reg.role("reasoner").urls[0].endswith("8088")
