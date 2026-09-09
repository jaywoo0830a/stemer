"""myllm_client — myLLM Script Runner API(리포 myllm) 연동.

DOC/1(myLLM Script Runner API) 스펙 구현:
    POST /v1/run       {"action", "arg"}   화이트리스트 스크립트 실행
    GET  /v1/allowlist                      실행 가능한 액션/슬러그
    GET  /health                            헬스체크

- 모든 /v1 호출은 `EXTERNAL_TOKEN` 이 설정돼 있으면 `Authorization: Bearer <token>` 필요.
- 실제 네트워크는 httpx(선택 의존) — 테스트는 Transport 주입(fake)으로 계약 고정.
  (gateway.py 와 같은 transport 프로토콜을 재사용한다.)

역할 슬러그 (DOC/1·DOC/3):
    parser, worker1~4, coder1~4, reasoner, setter, judge
액션 (DOC/1):
    up | down | start_all | start_heavy | status | restart | log

사용:
    from agent.myllm_client import MyllmClient
    c = MyllmClient(base_url="http://127.0.0.1:18080", token="...")
    c.start_all()                      # parser+worker+coder+reasoner (항시 셋)
    c.start_heavy("setter")            # 14B 단독 on-demand (상호배타)
    rep = c.status()                   # {"ok":..,"stdout":..,...}
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .gateway import GatewayError, Transport


class MyllmError(Exception):
    """myllm API 호출 실패/비허용 응답."""


DEFAULT_MYLLM_URL = "http://127.0.0.1:18080"  # 외부 LLM 서비스(myllm Script Runner)

# DOC/1 에 정의된 액션
ACTIONS = frozenset({"up", "down", "start_all", "start_heavy", "status",
                     "restart", "log"})


class MyllmClient:
    """myLLM Script Runner HTTP 클라이언트 (allowlist 위임·주입 가능).

    Args:
        base_url: myllm Script Runner 루트 (기본 env MYLLM_API).
        token:    EXTERNAL_TOKEN (env MYLLM_TOKEN 우선). None 이면 Bearer 없음.
        transport: HTTP 추상화 (기본 httpx). 테스트는 fake 로 교체.
        timeout:  요청 타임아웃 (초). 모델 기동은 수십 초 걸릴 수 있으니 넉넉히.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        token: Optional[str] = None,
        transport: Optional[Transport] = None,
        timeout: float = 240.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("MYLLM_API")
                         or DEFAULT_MYLLM_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get("MYLLM_TOKEN")
        self.timeout = timeout
        if transport is not None:
            self._transport = transport
        else:
            from .gateway import _HttpxTransport
            self._transport = _HttpxTransport()

    # ---- 공통 요청 -------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = self._transport.post_json(f"{self.base_url}{path}", body,
                                             self.timeout, self._headers())
        except Exception as exc:  # noqa: BLE001 — httpx/Gateway/네트워크 실패 모두 래핑
            raise MyllmError(f"myllm {path} failed: {exc}") from None
        if not isinstance(data, dict):
            raise MyllmError(f"myllm {path} returned non-JSON object: {data!r}")
        if data.get("ok") is False:
            raise MyllmError(f"myllm {path} errored: {data}")
        return data

    # ---- DOC/1 액션 --------------------------------------------------
    def run(self, action: str, arg: Optional[str] = None) -> Dict[str, Any]:
        """임의 액션 실행 (화이트리스트는 서버가 검증). arg 는 있을 때만 포함."""
        if action not in ACTIONS:
            raise MyllmError(f"unknown myllm action '{action}'")
        body: Dict[str, Any] = {"action": action}
        if arg:
            body["arg"] = arg
        return self._post("/v1/run", body)

    def status(self) -> Dict[str, Any]:
        """상태 리포트 생성(STDOUT 에 담김)."""
        return self.run("status")

    def start_all(self) -> Dict[str, Any]:
        """항시 셋(parser+worker+coder+reasoner) 부팅 (DOC/2 on-demand)."""
        return self.run("start_all")

    def start_heavy(self, heavy: str) -> Dict[str, Any]:
        """14B 모델 단독 부팅 (setter|judge, 상호배타 — DOC/2/3)."""
        if heavy not in ("setter", "judge"):
            raise MyllmError(f"start_heavy arg must be setter|judge, got '{heavy}'")
        return self.run("start_heavy", heavy)

    def up(self, slug: str) -> Dict[str, Any]:
        return self.run("up", slug)

    def down(self, slug: str) -> Dict[str, Any]:
        return self.run("down", slug)

    # ---- 조회 --------------------------------------------------------
    def allowlist(self) -> list[Dict[str, Any]]:
        """실행 가능한 액션/슬러그 목록 (없으면 [])."""
        try:
            data = self._transport.post_json(f"{self.base_url}/v1/allowlist",
                                             {}, self.timeout, self._headers())
        except Exception as exc:  # noqa: BLE001
            raise MyllmError(f"myllm allowlist failed: {exc}") from None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("actions", "allowlist", "items"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    def health(self) -> Dict[str, Any]:
        try:
            data = self._transport.post_json(f"{self.base_url}/health", {},
                                             self.timeout, self._headers())
        except Exception as exc:  # noqa: BLE001
            raise MyllmError(f"myllm health failed: {exc}") from None
        return data if isinstance(data, dict) else {"ok": bool(data)}

    # ---- 진단 (best-effort) ------------------------------------------
    @property
    def reachable(self) -> bool:
        """연결 가능 여부 (실패해도 예외 대신 False)."""
        try:
            self._post("/v1/run", {"action": "status"})
            return True
        except (MyllmError, GatewayError):
            return False