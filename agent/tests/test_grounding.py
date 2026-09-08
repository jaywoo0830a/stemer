"""grounding — Tier1 (근거 충실도 무료 게이트) 계약. 순수 함수 테스트."""
import pytest

from agent.grounding import (empty_or_too_short, lexical_ok, correction_prompt,
                             numeric_anchor_check)
from agent.rag import Chunk

PASS = Chunk(source="calc", section="11.3", text=(
    "The Integral Test remainder sum bound: the tail satisfies "
    "\\int _ { n } ^ \\infty f dx on the upper side and \\int _ { n+1 } "
    "on the lower side."))

WORKED = Chunk(source="calc", section="11.3 Exercises", text=(
    "We need 32 terms to ensure accuracy to within 0.0005, since n > 31.6."))


def test_short_flagged():
    assert empty_or_too_short("   ")
    assert empty_or_too_short("short answer")
    assert not empty_or_too_short("A sufficiently long explanatory sentence." * 2)


def test_lexical_ok_no_source_true():
    ok, _ = lexical_ok("q", [], "any free-form text here is allowed")
    assert ok


def test_lexical_offtopic_rejected():
    text = "Quantum chromodynamics confinement and SU(3) color gluon fields."
    ok, reason = lexical_ok("why is integral zero", [PASS], text)
    assert ok is False
    assert "off-topic" in reason


def test_lexical_shares_word_passes():
    text = ("The Integral Test remainder bound tail is the upper integral "
            "estimate and proves convergence for this series question.")
    ok, reason = lexical_ok("integral test remainder", [PASS], text)
    assert ok is True
    assert "shares" in reason


def test_lexical_short_flagged():
    ok, reason = lexical_ok("q", [PASS], "tiny")
    assert ok is False
    assert "empty or too short" in reason


def test_correction_prompt_repeats_source_and_is_strict():
    s = correction_prompt("question?", [PASS], "off-topic")
    assert "REJECTED" in s
    assert "VERBATIM" in s
    assert "REFERENCE CONTEXT" in s
    assert "not covered in the supplied source" in s
    assert "Integral Test" in s


# --- numeric_anchor: 워크드 답과 최종 숫자 대조 (결정론) -------------------------
def test_numeric_anchor_no_anchor_passes():
    ok, reason = numeric_anchor_check("state theorem", [PASS], "no numbers")
    assert ok is True           # 앵커 없음 → 통과


def test_numeric_anchor_working_value_matched():
    q = "how many terms n make partial sum accurate to within 0.0005?"
    ok, reason = numeric_anchor_check(q, [WORKED], "we need 32 terms")
    assert ok is True, reason


def test_numeric_anchor_wrong_value_rejected():
    # 45/'15' 등 source 의 32와 어긋나면 결정론적으로 reject (책값 32 정답)
    q = "how many terms n make partial sum accurate to within 0.0005?"
    ok, reason = numeric_anchor_check(q, [WORKED], "it needs 45 terms (my calc)")
    assert ok is False
    assert "disagree" in reason and "32" in reason


def test_numeric_anchor_deferral_passes_without_number():
    q = "how many terms n ... within 0.0005?"
    ok, _ = numeric_anchor_check(q, [WORKED],
                                 "not covered in the supplied source")
    assert ok is True            # 회피 → 강제 숫자 요구는 안 함(단 no assertion)


