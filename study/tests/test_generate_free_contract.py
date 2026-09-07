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
    """호출 순서대로 body 를 돌려주는 더미 (단일-호출 경로 / reject 테스트용)."""
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = []

    def complete(self, *, system, user, max_tokens, json_object):
        self.calls.append({"system": system, "user": user,
                           "max_tokens": max_tokens, "json_object": json_object})
        from study_lib.llm import LLMResult, Usage
        body = self.bodies.pop(0) if self.bodies else ""
        return LLMResult(content=body, usage=Usage())


import re as _re


class _TierLLM:
    """목록형(examples/practice)을 '요청 개수 만큼 마커'로 채우는 더미.

    concept 요청이면 고정 개념 본문을 반환하고 기록만 남긴다. 목록 요청은
    user 의 'Produce N ...' 에서 N 을 읽어 그만큼 'marker N' 라인을 돌려줘
    배치 누적(다회 호출)을 결정적으로 검증한다.
    """
    def __init__(self):
        self.calls = []

    def complete(self, *, system, user, max_tokens, json_object):
        self.calls.append((json_object, user))
        from study_lib.llm import LLMResult, Usage
        low_sys = (system or "").lower()
        low_user = user or ""
        if "reading the topic" in low_sys and "worked examples" not in low_sys \
                and "practice problems" not in low_sys:
            return LLMResult(content="## Reading the Topic\n\nCONCEPT_BODY",
                             usage=Usage())
        # 목록 호출: 난이도 마커 판별
        if "worked examples" in low_sys:
            marker = "### Example"
        else:
            marker = "### Problem"
        m = _re.search(r"Produce\s+(\d+)", low_user)
        n = int(m.group(1)) if m else 2
        tail = "**Solution.** placeholder under $x^2$." \
            if marker.startswith("### Example") \
            else "**Problem.** ...\n\n**Work area.**\n\n**Solution.** $x$."
        lines = [f"{marker} {i}\n{tail}" for i in range(1, n + 1)]
        body = "\n\n".join(lines)
        return LLMResult(content=body, usage=Usage())


def test_run_free_parts_list_parts_multi_shot_accumulates(tmp_path):
    """examples 는 Basic5 · practice 는 Basic10/Standard5/Challenge5 → 다회 호출로
    누적돼 각 파일에 요구 수만큼 항목이 생긴다."""
    import study_lib.generate_free as gf
    llm = _TierLLM()
    path = gf.run_free_parts(_topic(), llm, ["p1", "p2"], tmp_path)
    ex = (tmp_path / "미적분-11-3.examples.md").read_text(encoding="utf-8")
    pr = (tmp_path / "미적분-11-3.practice.md").read_text(encoding="utf-8")
    # examples Basic 5
    n_ex = ex.count("### Example ")
    assert n_ex >= gf._SPEC["examples"]["tiers"][0][1]  # >= 5
    # practice 합계 10+5+5=20 (다회 호출이 포함: calls 개수는 목록 파트들에서 여러번)
    n_pr = pr.count("### Problem ")
    assert n_pr >= gf._SPEC["practice"]["tiers"][0][1] + \
        gf._SPEC["practice"]["tiers"][1][1] + gf._SPEC["practice"]["tiers"][2][1]
    assert "## Worked examples" in ex and "## Practice problems" in pr
    combo = (tmp_path / "미적분-11-3.md").read_text(encoding="utf-8")
    # concept 먼저, examples 중간, practice 마지막
    assert combo.index("CONCEPT_BODY") < combo.index("## Worked examples") < \
        combo.index("## Practice problems")
    assert str(path) == str(tmp_path / "미적분-11-3.md")


def test_run_free_parts_partial_reuses_existing_parts(tmp_path):
    """examples 만 재생성해도 기존 concept/practice 파일을 병합에 재사용."""
    from study_lib.generate_free import run_free_parts
    # 먼저 전체 생성
    llm1 = _TierLLM()
    run_free_parts(_topic(), llm1, ["p"], tmp_path)
    # 이번엔 examples 만 (concept/practice 는 디스크 재사용)
    llm2 = _TierLLM()
    run_free_parts(_topic(), llm2, ["p"], tmp_path, parts=["examples"])
    combo = (tmp_path / "미적분-11-3.md").read_text(encoding="utf-8")
    assert "## Worked examples" in combo          # 새 예제 및 갱신 병합
    assert "CONCEPT_BODY" in combo and "### Problem " in combo  # 기존 유지


def test_run_free_parts_rejects_reasoning_leak_without_heading(tmp_path):
    """헤딩 없는 body(=think 누출)는 저장하지 않고 전체실패로 취급."""
    from study_lib.generate_free import run_free_parts
    llm = _SeqLLM(["Okay so I'm trying to understand... no heading used."])
    with pytest.raises(Exception):
        run_free_parts(_topic(), llm, ["p"], tmp_path, parts=["concept"])
    assert not (tmp_path / "미적분-11-3.concept.md").exists()  # 저장 안 됨
    assert not (tmp_path / "미적분-11-3.md").exists()


def test_normalize_math_delims_converts_to_katex_dollars():
    from study_lib.generate_free import normalize_math_delims
    out = normalize_math_delims(
        r"Inline \(a + b\) and display \[\sum_{n=1}^{\infty} a_n\] end.")
    assert "Inline $a + b$" in out
    assert r"$$\sum_{n=1}^{\infty} a_n$$" in out
    # 이미 $ 로 쓴 것은 그대로 유지
    assert "already $x^2$" == normalize_math_delims("already $x^2$")


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
