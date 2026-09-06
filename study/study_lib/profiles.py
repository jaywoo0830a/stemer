"""청크 프로필 레지스트리 — 이름 → ChunkProfile (책 단위 연결용).

클라이언트 관점:
    load_profile("compact")   # 이름으로 프로필 객체 조회 (미지 이름은 ValueError)
    profile_names()
"""
from __future__ import annotations

from .chunk import ChunkProfile

_BUILTIN: dict[str, ChunkProfile] = {
    "default": ChunkProfile(),
    "compact": ChunkProfile(name="compact", min_tokens=120, target_tokens=250,
                            max_tokens=350, overlap_tokens=40),
    "long": ChunkProfile(name="long", min_tokens=300, target_tokens=600,
                         max_tokens=800, overlap_tokens=80),
}


def profile_names() -> tuple[str, ...]:
    return tuple(_BUILTIN)


def load_profile(name: str) -> ChunkProfile:
    try:
        return _BUILTIN[name]
    except KeyError:
        raise ValueError(f"unknown chunk profile {name!r}; known: {profile_names()}") from None
