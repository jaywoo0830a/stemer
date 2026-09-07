"""postproc_katex 계약 테스트 — 로컬 LLM(모의) KaTeX 후처리 + 안전 가드.

원칙(사용자): "완성된 결과물(내용)은 건드리지 말고, 수식 표현만 다양화".
- 수식 밖(문장·헤딩·출처·번호) 텍스트가 바뀌면 자동 거부 → 원본 유지.
- KaTeX 벨런스/금지 매크로 위반해도 거부.
- 예외/공백 응답도 원본 유지.
"""
import pytest

from study_lib.postproc_katex import (
    guard_ok,
    plain_signature,
    refine_with,
    text_preserved,
)

NOTE = (
    "A series $S=\\sum_{n\\ge1} a_n$ converges when its tail vanishes.\n\n"
    "**Formula.** $S$ converges $\\iff$ $\\int_1^{\\infty} f\\,dx$ converges.\n"
)


class Dummy:
    """지정 결과 또는 예외를 돌려주는 모의 LLM."""

    def __init__(self, out_or_exc):
        self._out = out_or_exc

    def complete_text(self, *, system, user):
        if isinstance(self._out, Exception):
            raise self._out
        return self._out


def _display_only(md: str) -> str:
    """'**Formula.**' 줄의 인라인 수식 3개를 각각 그대로 두되,
    두 번째(정의 수식)만 단독 display 라인으로 승격시킨 후보."""
    return md.replace(
        "**Formula.** $S$ converges $\\iff$ $\\int_1^{\\infty} f\\,dx$ converges.",
        "**Formula.**\n\n$$S\\;\\text{conv}$$\n\n$\\iff$\n\n$\\int_1^{\\infty} f\\,dx$ converges.",
    )


def test_plain_signature_ignores_math_only_locations():
    # 수식 내용/배치만 다르면 비수식 서명은 동일 (문장 보존 기능)
    a = "Series $\\sum_n x$ and $x^2$ end."
    b = "Series $$\\sum_n x$$ and\n$x^2$ end."
    assert plain_signature(a) == plain_signature(b)


def test_text_preserved_rejects_when_words_change():
    assert not text_preserved(NOTE, "Changed word here" + NOTE[12:])
    assert not text_preserved(NOTE, NOTE + " EXTRA sentence.")


def test_guard_lint_blocks_bad_candidate():
    # 불균형 $ (후보가 닫는 $ 를 지움) → lint 거부
    bad = NOTE.replace("\\,dx$", "\\,dx")
    ok, why = guard_ok(NOTE, bad)
    assert ok is False
    assert why


def test_refine_adopts_layout_only_candidate():
    # LLM 이 문장/번호는 그대로, '$$...$$' display 승격만 한 경우 채택
    src = (
        "We have $f'=2x$ and $g=1$.\n\nFormula: $a+b=c$ end.\n"
    )
    good = (
        "We have $f'=2x$ and $g=1$.\n\nFormula:\n\n$$a+b=c$$\n\nend.\n"
    )
    assert text_preserved(src, good)
    assert refine_with(Dummy(good), src) == good


def test_refine_keeps_original_when_llm_changes_prose():
    changed = NOTE.replace("A series", "The series")  # 문장 변경
    assert refine_with(Dummy(changed), NOTE) == NOTE


def test_refine_keeps_original_on_exception_or_empty():
    assert refine_with(Dummy(RuntimeError("llm down")), NOTE) == NOTE
    assert refine_with(Dummy(""), NOTE) == NOTE


def test_refine_gated_candidate_rejected():
    # KaTeX 금지 매크로 \text 가 이 프로젝트 lint 를 통과하지 못하는 후보 → 거부
    src = "We have $a=b$."
    with_badtext = "We have $$a = \\text{the} b$$."
    # text 가 문장화되어 signature 도 달라짐 + \text 금지 → 원본 유지
    assert refine_with(Dummy(with_badtext), src) == src
