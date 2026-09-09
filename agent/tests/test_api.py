"""api — 웹 API(endpoint) 계약. fastapi/starlette 있는 환경에서만 실행(skip guard).

run:
    python -m pytest agent/tests -q            # 없으면 test_api 자동 skip
    study/.venv/bin/python -m pytest agent/tests -q   # 웹 deps 가 있을 때 전체
"""
from __future__ import annotations

import pytest

try:
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from agent.api import create_app
    from agent.myllm_client import MyllmClient
    from agent.tests._fakes import FakeMyllmTransport
    HAVE_API = True
except Exception:  # noqa: BLE001 — fastapi 미설치 환경 skip
    HAVE_API = False


pytestmark = pytest.mark.skipif(not HAVE_API, reason="fastapi/starlette not installed")


@pytest.fixture
def client(tmp_path):
    app = create_app(live=False, note_dir=str(tmp_path))
    return TestClient(app), tmp_path


def test_health_reports_mock_mode(client):
    c, _ = client
    h = c.get("/health").json()
    assert h["ok"] is True
    assert h["mode"] == "mock"
    assert {"parser", "worker", "coder", "reasoner", "embed"} <= set(h["roles"])


def test_split_plans(client):
    c, _ = client
    body = {"plan": "[Task 1: explain] why zero symmetric\n[Task 2: fix] idx bug"}
    j = c.post("/split-plans", json=body).json()
    assert j["ok"] is True
    assert [(t["id"], t["role"]) for t in j["tasks"]] == [(1, "worker"), (2, "coder")]


def test_run_plans_returns_file_and_markdown(client):
    c, tmp = client
    j = c.post("/run-plans", json={
        "plan": "[Task 1: explain] sine integral\n[Task 2: proof] orthogonality"}).json()
    assert j["total"] == 2 and j["ok_count"] == 2 and j["mode"] == "mock"
    assert [t["role"] for t in j["tasks"]] == ["worker", "reasoner"]
    # 파일이 실제 저장됨
    assert (tmp / (j["stem"] + ".md")).exists()
    assert "Task 1" in j["markdown"] and "Task 1" in j["markdown"]


def test_run_plans_error_surface_for_bad_payload(client):
    """빈 plan 은 pydantic min_length=1 로 422 (앱 크래시 없이 검증 거부)."""
    c, _ = client
    r = c.post("/run-plans", json={"plan": "", "use_parser": False})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# myllm(모델 기동) 연동
# --------------------------------------------------------------------------- #
def _myllm_client():
    return MyllmClient(base_url="http://127.0.0.1:8000", token="secret",
                       transport=FakeMyllmTransport())


def test_health_exposes_myllm_config(tmp_path):
    app = create_app(live=False, note_dir=str(tmp_path),
                     myllm=_myllm_client(), boot_on_run=True)
    h = TestClient(app).get("/health").json()
    assert h["myllm"] == {
        "configured": True,
        "base_url": "http://127.0.0.1:8000",
        "token_set": True,
        "boot_on_run": True,
    }


def test_run_plans_boots_always_models(tmp_path):
    m = _myllm_client()
    app = create_app(live=False, note_dir=str(tmp_path), myllm=m,
                     boot_on_run=True)
    c = TestClient(app)
    c.post("/run-plans", json={"plan": "[Task 1: explain] why zero"})
    actions = [call["body"].get("action") for call in m._transport.calls
               if call["url"].endswith("/v1/run")]
    assert "start_all" in actions


def test_run_plans_boots_setter_for_problems(tmp_path):
    m = _myllm_client()
    app = create_app(live=False, note_dir=str(tmp_path), myllm=m,
                     boot_on_run=True)
    c = TestClient(app)
    c.post("/run-plans", json={
        "plan": "[Task 1: problems] make a set on sequences"})
    runs = [call["body"] for call in m._transport.calls
            if call["url"].endswith("/v1/run")]
    assert any(b.get("action") == "start_all" for b in runs)
    assert any(b.get("action") == "start_heavy" and b.get("arg") == "setter"
               for b in runs)


def test_run_plans_no_boot_when_disabled(tmp_path):
    m = _myllm_client()
    app = create_app(live=False, note_dir=str(tmp_path), myllm=m,
                     boot_on_run=False)
    c = TestClient(app)
    c.post("/run-plans", json={"plan": "[Task 1: explain] why zero"})
    assert [call for call in m._transport.calls
            if call["url"].endswith("/v1/run")] == []
