"""grounding — Tier1 (근거 충실도 무료 게이트) 계약. 순수 함수 테스트."""
import pytest

from agent.grounding import empty_or_too_short, lexical_ok, correction_prompt
from agent.rag import Chunk

PASS = Chunk(source="calc", section="11.3", text=(
    "The Integral Test remainder sum bound: the tail satisfies "
    "\\int _ { n } ^ \\infty f dx on the upper side and \\int _ { n+1 } "
    "on the lower side."))


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
