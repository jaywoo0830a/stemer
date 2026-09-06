"""렌더 계약 — 클라이언트 관점 테스트.

검증된 GEN-PROTOCOL payload → 최종 마크다운. 구조·헤더·라벨은 템플릿(서버)이
소유하므로, 테스트는 섹션 순서·라벨·내용 보존(수식 포함)을 고정한다.
"""
from study_lib.render import render_guide

EXAM_PAYLOAD = {
    "cs": [{
        "c": "Limit of a sequence",
        "d": "A sequence (a_n) converges to L if for every epsilon>0 there is an N ...",
        "f": "lim a_n = L ⇔ ∀ε>0 ∃N: n>N ⇒ |a_n−L|<ε",
        "k": "Only the tail matters.",
        "m": "Treating divergence to infinity as convergence.",
    }],
    "r": "1) Guess L 2) Form |a_n−L| 3) Bound below ε 4) Solve for N.",
    "as": ["Stability of iterative solvers: error → 0 as n → ∞"],
    "ex": [{
        "p": "Prove a_n = n/(n+1) converges to 1.",
        "s": "|a_n−1| = 1/(n+1) < ε ⇔ choose N = ⌈1/ε⌉.",
    }],
    "pr": ["a_n = (2n+3)/(3n−1), find the limit",
           "a_n = (−1)^n / n, find the limit"],
}


def _positions(text: str, *needles: str) -> list[int]:
    return [text.find(n) for n in needles]


def test_render_guide_produces_meta_and_title():
    md = render_guide(EXAM_PAYLOAD, title="Limit of a sequence", subject="math",
                      kind="exam", section="3.5", book_id="calc")
    assert md.startswith("---")
    assert "title: Limit of a sequence" in md
    assert "subject: math" in md and "kind: exam" in md
    assert "section: 3.5" in md and "book: calc" in md
    assert "# Limit of a sequence" in md


def test_render_guide_section_order_is_fixed():
    md = render_guide(EXAM_PAYLOAD, title="T", subject="math", kind="exam")
    p = _positions(md, "## Concepts", "## Worked recipe", "## Applications",
                   "## Worked examples", "## Practice problems")
    assert all(x != -1 for x in p)
    assert p == sorted(p)   # 문서 순서 고정


def test_render_guide_preserves_formula_and_concept_labels():
    md = render_guide(EXAM_PAYLOAD, title="T", subject="math", kind="exam")
    assert "### Limit of a sequence" in md
    assert "**Definition.**" in md and "**Formula.**" in md
    assert "**Intuition.**" in md and "**Common mistake.**" in md
    assert "∀ε>0" in md                       # 수식(유니코드)이 그대로 보존
    assert "**Solution.**" in md
    assert "1. a_n = (2n+3)/(3n−1), find the limit" in md


def test_subject_pack_keys_render_as_labeled_sections():
    chem = {
        "cs": [{"c": "Redox", "d": "Electron transfer.", "f": "ox + ne− ⇌ red",
                "k": "LEO says GER.", "m": "Confusing oxidation number with charge."}],
        "eq": ["2 H2 + O2 → 2 H2O"],
        "cond": "Catalyst, 25 °C, 1 atm",
        "mech": ["Assign oxidation numbers.", "Split half-reactions."],
        "r": "Balance atoms, then charge.",
    }
    md = render_guide(chem, title="Redox", subject="chem", kind="exam")
    assert "## Key equations" in md and "2 H2 + O2 → 2 H2O" in md
    assert "## Reaction conditions" in md and "Catalyst, 25 °C, 1 atm" in md
    assert "## Mechanism" in md
    assert "- Assign oxidation numbers." in md


def test_note_kind_omits_examples_and_practice():
    note = {"cs": EXAM_PAYLOAD["cs"], "r": EXAM_PAYLOAD["r"],
            "as": EXAM_PAYLOAD["as"]}
    md = render_guide(note, title="Note", subject="math", kind="note")
    assert "## Concepts" in md and "## Worked recipe" in md
    assert "## Worked examples" not in md
    assert "## Practice problems" not in md


def test_problems_kind_renders_solved_problems_with_level():
    problems = {"pr": [{"lvl": "b", "p": "Find the limit of a_n = 1/n.",
                        "s": "As n → ∞, 1/n → 0."},
                       {"lvl": "a", "p": "Show a_n = n/(n+1) → 1.",
                        "s": "Use ε−N definition."}]}
    md = render_guide(problems, title="Problems", subject="math", kind="problems")
    assert "## Problems" in md
    assert "### Basic Problem 1" in md
    assert "### Advanced Problem 2" in md
    assert "**Solution.**" in md
    assert "## Concepts" not in md
