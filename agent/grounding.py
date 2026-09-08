"""grounding — 근거 충실도 Tier-1 (네트워크 없이, 일반 규칙).

LLM judge(verify.py) 로 진실성/오류/예외를 검증하기 전에 비싼 LLM 호출을 아끼는
저렴한 1차 게이트. 사용자 제안대로 **도메인 키워드 하드코딩(_MARKERS) 은 없앤다**.
규칙은 "답이 구조적으로 잘못됐거나(빔/너무 짧음), 제공된 source 와 어휘/수식이
하나라도 겹치지 않는 drift" 만 잡는다 — 의미적 참/거짓은 LLM judge 가 판단하도록.

- leeremic(문장/LaTeX 조각) 공유: 완전 동떨어진 다른 주제로 빠진 걸 1차 배제.
- 이 모듈은 순수함수 + 결정론 → 단위 테스트 가능.
"""
from __future__ import annotations

import re
from typing import Callable, Sequence

from .rag import Chunk

_LATEX_FRAG = re.compile(r"\\[a-zA-Z]+\s*[\w{}\\^+*/=<\]]{2,90}")

# LLM judge(verify.py) 가 검증 전에 '너무 짧아 근거 불가'로 볼 최소 길이
MIN_ANSWER_CHARS = 40


def empty_or_too_short(answer: str) -> bool:
    t = (answer or "").strip()
    if not t:
        return True
    return len(t) < MIN_ANSWER_CHARS


def lexical_ok(question: str, chunks: Sequence[Chunk], answer: str) -> tuple[bool, str]:
    """답이 source 중 하나와 조금이라도 어휘/LaTeX 로 섞였는지.

    - chunk 가 없으면(free answer) True.
    - 답이 어느 source 의 단어(≥4자)도, LaTeX 조각도 안 쓰고 완전히 동떨어졌으면
      False (완전 drift) → LLM judge 까지 갈 필요 없이 조기 배제.
    - 좁힌 오류(이중경계 등)의 의미 판단은 여기서 안 함: Tier-2 LLM judge 담당.
    """
    text = (answer or "").strip()
    if not chunks:
        return True, "no source (free)"
    if empty_or_too_short(answer):
        return False, "answer empty or too short to ground"
    out_words = set(re.findall(r"[A-Za-z]{4,}", text.lower()))
    for c in chunks:
        src_words = set(re.findall(r"[A-Za-z]{4,}", c.text.lower()))
        if out_words & src_words:
            return True, "shares vocabulary with a source"
        # LaTeX 조각 공유
        if any(f in text for f in re.findall(_LATEX_FRAG, c.text) if len(f) >= 8):
            return True, "shares LaTeX fragment with a source"
    return False, "answer is off-topic (shares nothing with any supplied source)"


# 교정 재시도 프롬프트 (judge 의 reason 을 받아 '원문 그대로 재현' 강제)
def correction_prompt(question: str, chunks: Sequence[Chunk], reason: str) -> str:
    block = "\n\n".join(f"[{c.source} / {c.section}]\n{c.text}" for c in chunks)
    return (
        f"Your previous answer was REJECTED by a verifier.\n"
        f"Verifier note: {reason}\n\n"
        "STRICT RULES NOW:\n"
        "- Base the ENTIRE answer ONLY on the reference text below.\n"
        "- When a fact/theorem/formula/bound appears in the reference, reproduce "
        "it VERBATIM (do not simplify, re-derive, or swap in your own version).\n"
        "- Do not add conclusions, numbers, exceptions, or cases that are not in "
        "the reference. If something is genuinely absent there, say 'not covered "
        "in the supplied source' rather than guessing.\n\n"
        "ORIGINAL QUESTION:\n" + question.strip() + "\n\n"
        "REFERENCE CONTEXT (the ONLY allowed source):\n" + block
    )
