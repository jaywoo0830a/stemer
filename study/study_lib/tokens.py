"""토큰 추정 — 출력/청크 토큰 수 근사 (공용).

클라이언트(청킹·프로토콜 검증)가 같은 척도로 토큰을 센다.
기본 척도: ASCII는 ~4자/토큰, 비ASCII(수식 유니코드 등)는 1자≈1토큰.
"""
from __future__ import annotations

import math
import re
from typing import Callable

Tokenize = Callable[[str], int]

_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")


def terms(text: str) -> set[str]:
    """검색/리랭크용 어휘 토큰 (영문·한글·숫자 단위)."""
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def default_tokenize(text: str) -> int:
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - ascii_chars
    return max(1, math.ceil(ascii_chars / 4) + other)
