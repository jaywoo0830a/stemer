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


def _uniq(vals):
    """소수점 표기 제거 후 오름차 정렬 표현 (진단 메시지용)."""
    uniq = sorted({round(float(v), 6) for v in vals})
    return [int(x) if float(x).is_integer() else x for x in uniq]

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


# --- 결정론적 숫자 앵커 (워크드 답과 최종 답 대조) — LLM 산술 실수에 안 지는 하드 gate --
_NUM = re.compile(r"\d+(?:\.\d+)?")
_OUTCOME_WORDS = re.compile(
    r"(terms|need|require|required|approx|about|more than|at least|"
    r"at most|equals?|is about|=|within|cannot|can't|not covered)",
    re.IGNORECASE)
_INTENT_WORDS = re.compile(
    r"(how many|terms|approxim|within|find\s+n|value of|what|equals?|"
    r"should be|error|to within)",
    re.IGNORECASE)


def anchor_values(chunks: Sequence[Chunk]) -> list[float]:
    """결과줄(terms/need/…/within)에 나온 숫자 → source 의 워크드 앵커 값."""
    out: list[float] = []
    for c in chunks:
        for line in c.text.splitlines():
            if not _OUTCOME_WORDS.search(line):
                continue
            for tok in _NUM.findall(line):
                try:
                    out.append(float(tok))
                except ValueError:
                    continue
    return out


def numeric_anchor_check(question: str, chunks: Sequence[Chunk],
                         answer: str) -> tuple[bool, str]:
    """답의 숫자가 source 의 워크드 숫자와 하나라도 겹치는지(결정론).

    - 질문이 수치/개수 인트이고 source 에 앵커(결과 숫자)가 있을 때만 체크.
    - 앵커가 없으면 통과. 답이 'not covered' 로 회피하면 통과(단, 인용 요구 상실).
    - 앵커가 있는데 답의 어떤 숫자와도 안 겹치면 False (오답 조기 차단).
    """
    anchors = anchor_values(chunks)
    if not anchors:
        return True, "no numeric anchor"
    if not _INTENT_WORDS.search(question):
        return True, "question not numeric-intent"
    low = (answer or "").lower()
    if ("not covered" in low or "not present" in low
            or "no source" in low or "cannot say" in low):
        return True, "answer defers to absence (no explicit numeric assertion)"
    ans_nums = [float(t) for t in _NUM.findall(answer or '')
                if t.replace('.', '1', 1).replace('-', '', 1).lstrip('0').isdigit()]
    if not ans_nums:
        return False, (f"answer has no number but source prints worked value(s) "
                       f"{_uniq(anchors)}")
    for a in anchors:
        if any(abs(x - a) < 1e-6 for x in ans_nums):
            return True, "numeric anchor matched"
    return False, (f"answer value(s) {_uniq(ans_nums)} disagree with source "
                   f"worked value(s) {_uniq(anchors)}")


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
