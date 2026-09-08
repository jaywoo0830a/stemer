"""registry — 역할(role) ↔ 추론 서버 주소 매핑 (NEW-METHOD §3).

myllm server/config/models/*.env 가 myllm 저장소에 있지만, 여기선 "이 리포지토리
(오케스트레이터)"가 자기 설정(config/agent.yaml)을 가진다. 역할별 base_url 과
동시 실행(중복 서버 pool)을 명시한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Role:
    """한 역할의 추론 서버 정의."""

    name: str                    # parser / worker / coder / reasoner / embed
    kind: str = "chat"           # chat=llama-server /v1/chat/completions, embed=Ollama-style
    urls: tuple[str, ...] = ()   # base URLs (worker 는 여러 개) — 첫 서버는 기본
    model: Optional[str] = None  # OpenAI body 의 model 이름 (없으면 생략)

    @property
    def base_url(self) -> str:
        if not self.urls:
            raise RegistryError(f"role '{self.name}' has no configured server")
        return self.urls[0]

    def pick_url(self, index: int = 0) -> str:
        if not self.urls:
            raise RegistryError(f"role '{self.name}' has no configured server")
        return self.urls[index % len(self.urls)]


@dataclass(frozen=True)
class Server:
    role: str
    role_index: int = 0
    url: str = ""


class RegistryError(Exception):
    pass


DEFAULT_ROLES: Dict[str, list[str]] = {
    "parser": ["http://127.0.0.1:8081"],
    "worker": [f"http://127.0.0.1:{p}" for p in range(8082, 8086)],  # 8082-8085
    # 코더 4: 8086-8087 + 8089-8090 (8088 은 reasoner) — 서버 표준 배치
    "coder": ["http://127.0.0.1:8086", "http://127.0.0.1:8087",
              "http://127.0.0.1:8089", "http://127.0.0.1:8090"],
    "reasoner": ["http://127.0.0.1:8088"],
    # (선택) 전용 문제생성기(SETTER) 와 판사(JUDGE) — env/agent.yaml 가 있으면 활성
    "setter": ["http://127.0.0.1:8091"],
    "judge": ["http://127.0.0.1:8092"],
    # 임베딩은 llama-server(--embeddings 없음)가 아니라 Ollama 를 쓴다.
    "embed": ["http://127.0.0.1:11434"],
}

# agent.yaml(설정)이 없으면 이 기본값 + env 오버라이드 사용
_ENV_OVERRIDE = {
    "AGENT_PARSER", "AGENT_WORKERS", "AGENT_CODERS",
    "AGENT_REASONER", "AGENT_EMBED", "AGENT_SETTER", "AGENT_JUDGE",
}


def _env_base(key: str) -> Optional[str]:
    v = os.environ.get(key)
    return v.rstrip("/") if v else None


def build_role(name: str, urls: Optional[list[str]] = None, *, kind: str = "chat",
               model: Optional[str] = None) -> Role:
    """명시 urls 없으면 기본값 + env 오버라이드를 적용한다."""
    if urls is not None:
        base = [u.rstrip("/") for u in urls]
    else:
        base = list(DEFAULT_ROLES[name])
        env_key = {  # role -> env 변수 (CSV of urls)
            "parser": "AGENT_PARSER",
            "worker": "AGENT_WORKERS",
            "coder": "AGENT_CODERS",
            "reasoner": "AGENT_REASONER",
            "setter": "AGENT_SETTER",
            "judge": "AGENT_JUDGE",
            "embed": "AGENT_EMBED",
        }[name]
        raw = os.environ.get(env_key)
        if raw:
            base = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]
    if not base:
        raise RegistryError(f"role '{name}' has no server url")
    return Role(name=name, kind=kind, urls=tuple(base), model=model)


class Registry:
    """role 이름 → Role. 역할 미정의/추가 감지용 메타도 제공."""

    def __init__(self, roles: Optional[Dict[str, Role]] = None) -> None:
        if roles is None:
            roles = {
                "parser": build_role("parser"),
                "worker": build_role("worker"),
                "coder": build_role("coder"),
                "reasoner": build_role("reasoner"),
                "embed": build_role("embed", kind="embed"),
            }
            # 전용 서버가 env 로 선언되면 setter(문제생성)/judge(판사) 를 붙인다.
            if _env_base("AGENT_SETTER"):
                roles["setter"] = build_role("setter")
            if _env_base("AGENT_JUDGE"):
                roles["judge"] = build_role("judge")
        self._roles: Dict[str, Role] = roles

    def role(self, name: str) -> Role:
        try:
            return self._roles[name]
        except KeyError:
            raise RegistryError(f"unknown role '{name}' (known: {sorted(self._roles)})") from None

    def has(self, name: str) -> bool:
        return name in self._roles

    def roles(self) -> Dict[str, Role]:
        return dict(self._roles)

    def with_overrides(self, *, parser: Optional[str] = None,
                       workers: Optional[list[str]] = None,
                       coders: Optional[list[str]] = None,
                       reasoner: Optional[str] = None,
                       embed: Optional[str] = None) -> "Registry":
        """포트/주소를 명시적으로 바꾼 사본 생성 (테스트·설정 파일용)."""
        roles = {}
        for name, role in self._roles.items():
            urls = list(role.urls)
            if name == "parser" and parser:
                urls = [parser]
            elif name == "worker" and workers:
                urls = list(workers)
            elif name == "coder" and coders:
                urls = list(coders)
            elif name == "reasoner" and reasoner:
                urls = [reasoner]
            elif name == "embed" and embed:
                urls = [embed]
            roles[name] = Role(name=name, kind=role.kind, urls=tuple(urls), model=role.model)
        return Registry(roles)


def load_registry(config_path: Optional[str | Path] = None) -> Registry:
    """agent/config/agent.yaml(있으면) → Registry. 없으면 env/기본값.

    기본 config 경로: <repo>/agent/config/agent.yaml
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent / "config" / "agent.yaml"
    p = Path(config_path)
    if not p.is_file():
        return Registry()
    import yaml  # 선택 의존성 (설정 파일 쓸 때만)

    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    servers = cfg.get("servers", {}) or {}
    roles: Dict[str, Role] = {}
    for name in ("parser", "worker", "coder", "reasoner", "setter", "judge",
                 "embed"):
        entry = servers.get(name) or {}
        urls = entry.get("urls")
        kind = entry.get("kind", "embed" if name == "embed" else "chat")
        model = entry.get("model")
        roles[name] = build_role(name, urls=urls, kind=kind, model=model)
    return Registry(roles)


class ServerPool:
    """순환 라운드로 role 의 다중 서버(worker/coder pool)를 분배한다."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._counters: Dict[str, int] = {}

    def next(self, role: str) -> Server:
        r = self._registry.role(role)
        if r.kind != "chat" or len(r.urls) <= 1:
            return Server(role=role, role_index=0, url=r.base_url)
        i = self._counters.get(role, 0)
        idx = i % len(r.urls)          # 순환 인덱스: 0,1,…,n-1,0,…
        url = r.pick_url(idx)
        self._counters[role] = i + 1
        return Server(role=role, role_index=idx, url=url)
