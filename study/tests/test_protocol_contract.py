"""GEN-PROTOCOL 계약 — 클라이언트 관점 테스트.

모델(또는 테스트 더블)이 만든 100% JSON 페이로드가
과목 + kind 스키마와 필드 예산을 지키는지 검증한다. (GEN-PROTOCOL.md §4)
"""
from study_lib import protocol


def char_tokenize(text: str) -> int:
    """테스트용 토크나이저: 문자 하나 = 토큰 1개 (결정적 예산 검증)."""
    return len(text)


def _exam_payload():
    """GEN-PROTOCOL.md 의 exam 와이어 예시 (math)."""
    return {
        "cs": [{
            "c": "Limit of a sequence",
            "d": ("A sequence (a_n) converges to L if for every epsilon>0 there is "
                  "an N after which all terms stay within epsilon of L."),
            "f": "lim a_n = L ⇔ ∀ε>0 ∃N: n>N ⇒ |a_n−L|<ε",
            "k": "Only the tail matters — the first N terms are irrelevant.",
            "m": "Treating divergence to infinity as convergence.",
        }],
        "r": "1) Guess L 2) Form |a_n−L| 3) Bound below ε 4) Solve for N.",
        "as": ["Stability of iterative solvers: error → 0 as n → ∞"],
        "ex": [{
            "p": "Prove a_n = n/(n+1) converges to 1.",
            "s": "|a_n−1| = 1/(n+1) < ε ⇔ n > 1/ε − 1, choose N = ⌈1/ε⌉.",
        }],
        "pr": [
            "a_n = (2n+3)/(3n−1), find the limit",
            "a_n = (−1)^n / n, find the limit",
        ],
    }


def test_exam_payload_from_protocol_doc_is_valid(schema):
    # when: GEN-PROTOCOL 문서의 exam 예시 그대로 검증
    report = protocol.validate(schema, _exam_payload(), subject="math", kind="exam")
    # then: 파싱·키·예산 모두 통과해야 한다
    assert report.ok, report.issues
    assert report.patch_slots == set()


def test_subject_pack_keys_are_allowed_only_for_that_subject(schema):
    # given: 화학 전용 키(eq/cond)가 든 페이로드
    payload = {
        "cs": [{
            "c": "Redox",
            "d": "Electron transfer between species.",
            "f": "ox + ne− ⇌ red",
            "k": "LEO says GER.",
            "m": "Confusing oxidation number with charge.",
        }],
        "eq": ["2 H2 + O2 → 2 H2O"],
        "cond": "Catalyst, 25 °C, 1 atm",
        "r": "1) Assign oxidation numbers 2) Split half-reactions.",
    }
    # then: 화학(subject=chem)에선 통과
    assert protocol.validate(schema, payload, "chem", "exam").ok
    # then: 수학(subject=math)에선 eq/cond 가 미등록 키로 거부
    report = protocol.validate(schema, payload, "math", "exam")
    assert not report.ok
    assert any("eq" in i for i in report.issues)
    assert any("cond" in i for i in report.issues)


def test_unknown_key_is_rejected_with_readable_message(schema):
    payload = {**_exam_payload(), "zzz": "surprise"}
    report = protocol.validate(schema, payload, "math", "exam")
    assert not report.ok
    assert any("unknown key 'zzz'" in i for i in report.issues)


def test_note_kind_rejects_ex_slot(schema):
    # note kind 에는 ex 가 없다 → ex 를 보내면 거부
    payload = {"cs": _exam_payload()["cs"], "ex": _exam_payload()["ex"], "r": "x"}
    report = protocol.validate(schema, payload, "math", "note")
    assert not report.ok
    assert any("unknown key 'ex'" in i for i in report.issues)


def test_problems_kind_pr_items_are_objects_with_lvl(schema):
    ok = {"pr": [{"lvl": "b", "p": "Find the limit of a_n = 1/n.",
                  "s": "As n → ∞, 1/n → 0."}]}
    assert protocol.validate(schema, ok, "math", "problems").ok
    # 문자열 아이템은 거부
    bad = {"pr": ["Find the limit."]}
    assert not protocol.validate(schema, bad, "math", "problems").ok
    # 허용되지 않은 lvl 은 거부
    report = protocol.validate(schema, {"pr": [{"lvl": "x", "p": "p", "s": "s"}]},
                               "math", "problems")
    assert not report.ok
    assert any("lvl" in i for i in report.issues)


def test_over_budget_field_is_flagged_and_slot_queued_for_patch(schema):
    # given: 정의(d)가 예산(90토큰)을 한참 넘는 페이로드
    payload = {
        "cs": [{"c": "c", "d": "x" * 200, "f": "f", "k": "k", "m": "m"}],
        "r": "r", "as": ["a"], "ex": [{"p": "p", "s": "s"}], "pr": ["q"],
    }
    report = protocol.validate(schema, payload, "math", "exam", tokenize=char_tokenize)
    assert not report.ok
    assert any("cs[0].d: exceeds budget 90" in i for i in report.issues)
    assert "cs" in report.patch_slots


def test_empty_slots_are_flagged_for_patch(schema):
    # when: 키는 있지만 내용이 전부 빈 페이로드
    payload = {
        "cs": [{"c": "", "d": "", "f": "", "k": "", "m": ""}],
        "r": "", "as": [], "ex": [], "pr": [],
    }
    report = protocol.validate(schema, payload, "math", "exam")
    assert not report.ok
    # then: 다시 채워야 할 슬롯이 보고되어야 한다
    assert {"cs", "r", "as", "ex", "pr"} <= report.patch_slots


def test_non_object_payload_and_unknown_kind_are_rejected(schema):
    assert not protocol.validate(schema, [], "math", "exam").ok
    assert not protocol.validate(schema, {}, "math", "exam").ok
    assert not protocol.validate(schema, {"cs": []}, "math", "no-such-kind").ok
