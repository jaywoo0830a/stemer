"""compress contract - chapter->study-guide md."""
from study_lib.compress import (assemble_passages, build_front_matter,
                                strip_fences, compress_body, split_into_batches)
from study_lib.llm import LLMResult, Usage


class _FakeLLM:
    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def respond(self, text):
        self._texts.append(text)

    def complete(self, *, system, user, max_tokens, json_object=False):
        self.calls.append(max_tokens)
        body = self._texts.pop(0) if self._texts else ''
        return LLMResult(content=body, usage=Usage(completion_tokens=10))

    def count_tokens(self, text):
        return max(1, (len(text) + 2) // 3)


class _Chunk:
    def __init__(self, text):
        self.text = text


def test_front_matter():
    fm = build_front_matter(title='V', book='calc', scope='1159-1165')
    assert 'title: V' in fm and 'book: calc' in fm and 'generator: compress' in fm


def test_strip_fences():
    assert strip_fences('```md\nA\nB\n```') == 'A\nB'
    assert strip_fences('  hi ') == 'hi'


def test_assemble_passages():
    out = assemble_passages([_Chunk('one'), 'two'], section_label='3.1')
    assert out.startswith('## 3.1\n')
    assert '[1] one' in out and '[2] two' in out


def test_split_into_batches():
    ps = ['a' * 300, 'b' * 300, 'c' * 300]
    bs = split_into_batches(ps, max_tokens=60)
    assert len(bs) >= 2
    assert sum(len(b) for b in bs) == 3


def test_compress_single_call():
    llm = _FakeLLM(['## Key Concepts\na\n## Summary\nz'])
    out = compress_body(['p1', 'p2'], llm, title='T', book='b', scope='s')
    assert llm.calls == [8000]
    assert '## Summary' in out


def test_compress_splits_batches():
    llm = _FakeLLM(['## Key Concepts\nA', '## Summary\nB'])
    out = compress_body(['y' * 90, 'z' * 90], llm, title='T', book='b',
                        scope='s', in_budget=20)
    assert len(llm.calls) == 2
    assert '## Summary' in out
