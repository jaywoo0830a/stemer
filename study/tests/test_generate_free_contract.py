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
