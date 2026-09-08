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


# --- 출제(문제 만들기) 구조 검증 v2 (결정론·엄격) -------------------------------
# 개별 문제 블록을 실제 표기로 분리하고, 각 블록이
#   Question(명령·수치 대상) + Solution/Answer(해법) + Difficulty  를 구조로
#   갖는지 강제. "(students must) Formulate a problem ..." 류 meta 지시를 거부.
_PROB_SPLIT = re.compile(r"(?m)^\s*(?=(?:PROBLEM\s*\d+|#+\s*Problem\b|"
                         r"문제\s*\d+|Question\s+[0-9]+|Problem\s*[0-9]+|[0-9]+[.)]))")
_SOLUTION_OK = re.compile(r"(Solution key|Solution:|Answer[:：]|답:|풀이:|Key:)",
                          re.IGNORECASE)
_IMPERATIVE = re.compile(
    r"(find|compute|evaluate|show that|prove|decide|test|determine|solve|check|"
    r"is it|converge|classify|which n|how many|for what)",
    re.IGNORECASE)
_META = re.compile(
    r"(?:formulate|design|make up|make|create|construct|invent)\s+(?:\w+\s+)?"
    r"(?:problem|exercise|question|example|task)\b|"
    r"students?\s+must", re.IGNORECASE)
_REQ_VERBS = ("find", "compute", "evaluate", "show", "prove", "decide", "determine",
              "solve", "classify", "converge")


def _split_problem_blocks(text: str) -> list[str]:
    """문제 블록들을 자른다. 경계는 번호/헤더 시작. (0이면 전체로 취급)"""
    starts = [m.start() for m in _PROB_SPLIT.finditer(text)]
    if not starts:
        return [text] if text.strip() else []
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        blocks.append(text[s:e].strip())
    return [b for b in blocks if b]


def problems_ok(question: str, chunks: Sequence[Chunk], answer: str) -> tuple[bool, str]:
    """엄격 구조 검증:
      1) 빈/짧음;
      2) meta-서술 포기(reject 하는 "Formulate ..."형) 없음;
      3) 실제 문제 블록 >= req(요청 개수 default 1, "two"같이 지정 시 반영);
         각 블록은 Question 대상 + Solution/Answer + Difficulty*;
      4) 문제 개념이 source 랑 어휘/메서드로 엉켰는지(leak 방지).
    *Difficulty 는 '권장 구조' — 여러 해답 문구가 오면 유연하게(임의 금지) 요구하지 않음.
    """
    text = (answer or "").strip()
    if empty_or_too_short(answer):
        return False, "answer empty or too short for problems"
    if _META.search(text):
        return False, ("output writes 'Formulate/make/students must …' (meta) "
                       "instead of concrete exercises")
    blocks = _split_problem_blocks(text)
    if not blocks:
        return False, "no concrete problem blocks found"

    req = _requested_count(question)          # 1+ ; 지정 없으면 1
    if len(blocks) < req:
        return False, (f"only {len(blocks)} problem block(s) but request asked "
                       f"for at least {req}")

    # 각 블록이 "실행/대상 질문 + 해답" 을 갖는지
    for b in blocks:
        bl = b.lower()
        if _META.search(b):
            return False, "a block is a meta prompt, not a concrete problem"
        has_ask = ("question" in bl) or ("?" in b) or (":" in b)
        has_answer = bool(_SOLUTION_OK.search(b))
        concreteness = bool(_NUM.search(b) or _IMPERATIVE.search(b))
        if not (has_ask and has_answer and concreteness):
            return False, ("a problem block lacks required structure "
                           "(imperative/numeric Question + Solution/Answer)")

    ok, reason = lexical_ok(question, chunks, text)
    if not ok:
        return False, reason
    return True, f"problems ok (N={len(blocks)})"


def _requested_count(question: str) -> int:
    low = (question or "").lower()
    m = re.search(r"(\d+)\s*(?:problems|exercises|questions|문제)", low)
    if m:
        return max(1, int(m.group(1)))
    if re.search(r"\btwo\b|두\s*문제", low):
        return 2
    if re.search(r"\bthree\b|세\s*문제", low):
        return 3
    return 1


# 출제 전용 교정 프롬프트 (인용-재현이 아니라 '출제 지침' 강조)
def problems_correction_prompt(question, chunks, reason) -> str:
    block = "\n\n".join(f"[{c.source} / {c.section}]\n{c.text}" for c in chunks)
    return (
        f"Your generated problem set was REJECTED.\nReason: {reason}\n\n"
        "PROBLEM-SETTER STRICT RULES:\n"
        "- Produce the requested number of CONCRETE exercises, each with explicit "
        "numbers/series (no 'Formulate a problem' meta). "
        "- For EVERY problem write, in order:\n"
        "    PROBLEM <n> — [concept]\n"
        "    Question: <a specific ask, e.g. 'Find the smallest n so R_n < …'>\n"
        "    Solution key: <exact steps & numeric answer; final answer printed>\n"
        "    Sympy verification: <```sympy``` block that computes that answer>\n"
        "    Difficulty: easy|medium|hard\n"
        "- Concepts/methods only from the reference below; numbers may be new but "
        "facts must be in the source. Never require a theorem/value that the source "
        "cannot support (e.g. don't drag in 'absolute convergence' if absent).\n"
        "- SYMPY-ONLY NUMBERS (Critical): never derive a final number in your head. "
        "Any numeric endpoint (a count n, a definite integral, a value) MUST be "
        "produced by an executable ```sympy``` (or ```python```) block immediately "
        "after that Solution key, and the block must print the final result. The "
        "Solution key then either states that number verbatim (it MUST equal the "
        "block's printed output) or writes the placeholder <<RESULT>> at the "
        "answer position for the system to fill. "
        "A missing code block (missing_code_block), a block that fails to run "
        "(execution_error), or a Solution-key number that differs from the code's "
        "output (value_mismatch, e.g. writing 45 when code computes 32) is "
        "rejected automatically with no judge. Concept-only problems omit the "
        "block.\n"
        "COUNTER-CONTRADICTION (consistency): the exact remainder/bound/formula "
        "you use MUST be lifted VERBATIM from the REFERENCE CONTEXT below (for "
        "Σ 1/n^3 the remainder bound is what the source prints — do NOT pretend "
        "the general term 1/n^3 is the remainder). If 'find smallest n/terms', "
        "your block must produce ONE positive finite integer; a block that prints "
        "a Symbol, returns EmptySet, or outputs ≤0 means your premise is "
        "inconsistent — correct the inequality/formula so execution yields that "
        "positive n.\n\n"
        "ORIGINAL REQUEST:\n" + question.strip() + "\n\n"
        "REFERENCE CONTEXT (concepts only):\n" + block
    )


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
