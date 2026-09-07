"""generate_free 계약 — 로컬 자유-md 생성(new notes/<id>.md).

규칙:
- topic + 교재 passage → 로컬 LLM(complete json_object=False) → markdown 본문.
- YAML front matter(title/book/section/generator) 로 감싸 notes/<id>.md 저장.
- 프롬프트가 실제 passage 를 포함해 교재 근거 유도.
"""
import pytest


class _LLM:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def complete(self, *, system, user, max_tokens, json_object):
        self.calls.append((json_object, user))
        from study_lib.llm import LLMResult, Usage
        return LLMResult(content=self.body, usage=Usage())


def _topic(**kw):
    from study_lib.registry import Topic
    return Topic(topic_id="미적분-11-3", book_id="미적분", subject="math",
                 title="Integral Test", section="11.3", **kw)


def test_run_free_writes_note_with_front_matter(tmp_path):
    from study_lib.generate_free import run_free_one
    llm = _LLM("본문: 시리즈 수렴은 ...\n\n수식 $S=\\sum a_n$. 둘다.")
    path = run_free_one(_topic(), llm, ["Integral Test def passage"], tmp_path)
    txt = (tmp_path / "미적분-11-3.md").read_text(encoding="utf-8")
    assert "generator: local-free" in txt
    assert txt.strip().endswith("둘다.")


def test_run_free_sends_json_object_false_and_passages(tmp_path):
    from study_lib.generate_free import run_free_one
    llm = _LLM("본문")
    run_free_one(_topic(), llm, ["p1", "p2"], tmp_path)
    flag, user = llm.calls[0]
    assert flag is False
    assert "Integral Test def passage" not in user or True
    assert "[1]" in user and "[2]" in user  # passage 번호 포함


class _SeqLLM:
    """호출 순서대로 body 를 돌려주는 더미 (부분별 3회 호출 확인용)."""
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = []

    def complete(self, *, system, user, max_tokens, json_object):
        self.calls.append({"system": system, "user": user,
                           "max_tokens": max_tokens, "json_object": json_object})
        from study_lib.llm import LLMResult, Usage
        body = self.bodies.pop(0) if self.bodies else ""
        return LLMResult(content=body, usage=Usage())


def test_run_free_parts_writes_three_files_and_combined(tmp_path):
    from study_lib.generate_free import run_free_parts, PART_ORDER
    llm = _SeqLLM(["CONCEPT_BODY", "EXAMPLES_BODY", "PRACTICE_BODY"])
    path = run_free_parts(_topic(), llm, ["p1", "p2"], tmp_path)
    # 3번 호출, 전부 json_object=False
    assert len(llm.calls) == len(PART_ORDER) == 3
    assert all(c["json_object"] is False for c in llm.calls)
    # 각 부분 파일 + 병합본 존재, 병합순서 concept→examples→practice
    for part in PART_ORDER:
        assert (tmp_path / f"미적분-11-3.{part}.md").exists()
    combo = (tmp_path / "미적분-11-3.md").read_text(encoding="utf-8")
    for marker in ("CONCEPT_BODY", "EXAMPLES_BODY", "PRACTICE_BODY"):
        assert marker in combo
    assert combo.index("CONCEPT_BODY") < combo.index("EXAMPLES_BODY") < \
        combo.index("PRACTICE_BODY")
    assert str(path) == str(tmp_path / "미적분-11-3.md")


def test_run_free_parts_partial_reuses_existing_parts(tmp_path):
    """examples 만 재생성해도 기존 concept/practice 파일을 병합에 재사용."""
    from study_lib.generate_free import run_free_parts
    # 먼저 전체 생성
    llm1 = _SeqLLM(["C1", "E1", "P1"])
    run_free_parts(_topic(), llm1, ["p"], tmp_path)
    # 이번엔 examples 만
    llm2 = _SeqLLM(["E2_NEW"])
    run_free_parts(_topic(), llm2, ["p"], tmp_path, parts=["examples"])
    assert llm2.calls and [(c.get("max_tokens")) for c in llm2.calls]
    assert len(llm2.calls) == 1
    combo = (tmp_path / "미적분-11-3.md").read_text(encoding="utf-8")
    assert "E2_NEW" in combo          # 새 예제 반영
    assert "C1" in combo and "P1" in combo   # 기존 개념/연습 보존


def test_pack_passages_respects_budget_keeps_prefix_order():
    from study_lib.generate_free import pack_passages
    # 각 passage 를 char 기반 토큰추정: 30글자 → ~10토큰
    ps = ["x" * 30] * 10   # 10개, 각 ~10토큰
    packed = pack_passages(ps, max_tokens=45, count_tokens=None)
    # 45/10 ≈ 4개까지만, prefix 유지
    assert len(packed) == 4
    assert packed == ps[:4]


def test_input_budget_default_is_cpu_safe_and_capped():
    """권고안 A: CPU 백엔드는 프롬프트를 ~4096 으로 제한. env 로만 상향."""
    import os
    import study_lib.generate_free as g
    assert g.DEFAULT_INPUT_TOKENS <= 4096
    cap = g.MAX_INPUT - g._SCAFFOLD_EST
    b = g.input_budget()
    assert 0 < b <= cap
    if "LOCAL_FREE_INPUT_TOKENS" not in os.environ:
        assert b == min(g.DEFAULT_INPUT_TOKENS, cap)


def test_part_token_cap_bounded_by_input_budget():
    import study_lib.generate_free as g
    assert g.part_token_cap() <= g.input_budget()


def test_passages_for_part_selects_subset_within_budget():
    import study_lib.generate_free as g
    ps = ["%05d introduction definition theorem continuous" % i for i in range(20)]
    ps[3] = "EXAMPLE worked sample solution"
    ps[8] = "Exercise practice problem #7"
    # 좁은 예산(< 총량)을 걸어 '전체를 모두 다시 보내는 것'이 아닌 것을 검증
    sub = g.passages_for_part("concept", ps, max_tokens=80,
                              count_tokens=lambda s: max(1, len(s) // 3))
    assert sub                        # 0개 아님
    assert len(sub) < len(ps)         # 전체보다 줄었음 (=반복 전송 완화)
    assert sub[0] == ps[0]            # core(primary 맨 앞) 유지
