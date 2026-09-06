"""청킹 계약 — 클라이언트 관점 테스트.

파싱된 책 마크다운을 넣으면: 헤딩이 섹션 경계가 되고, 어떤 청크도 `max_tokens` 를
넘지 않으며, 동일 입력에는 동일 청크가 나온다. (NEW-STRATEGY: 300~500토큰 청크)
"""
from study_lib.chunk import ChunkProfile, chunk_markdown


def char_tokens(text: str) -> int:
    """테스트용: 문자 하나 = 토큰 1개 (결정적)."""
    return len(text)


P = ChunkProfile(name="t", min_tokens=4, target_tokens=10,
                 max_tokens=12, overlap_tokens=3)


def test_chunks_never_exceed_max_tokens():
    # given: 예산을 한참 넘는 한 문단
    markdown = "# 1.1\n\n" + "x" * 100 + "\n"
    chunks = chunk_markdown(markdown, book_id="calc", profile=P, tokenize=char_tokens)
    assert chunks
    assert all(c.tokens <= P.max_tokens for c in chunks)
    assert all(c.section == "1.1" for c in chunks)
    # 겹침 포함이므로 조각 전체 길이는 원문 이상이어야 한다
    assert sum(len(c.text) for c in chunks) >= 100


def test_headings_become_section_boundaries():
    markdown = ("# Chapter 1\n\nintro para\n\n"
                "## 1.1 Limits\n\nlimit body\n\n"
                "## 1.2 Continuity\n\ncont body\n")
    chunks = chunk_markdown(markdown, book_id="calc", profile=P, tokenize=char_tokens)
    by_section: dict[str, list[str]] = {}
    for c in chunks:
        by_section.setdefault(c.section, []).append(c.text)
    assert "intro para" in " ".join(by_section["Chapter 1"])
    assert "limit body" in " ".join(by_section["1.1 Limits"])
    assert "cont body" in " ".join(by_section["1.2 Continuity"])


def test_chunks_are_tagged_with_book_and_have_unique_ordered_ids():
    chunks = chunk_markdown("# S\n\npara one\n\npara two\n", book_id="calc",
                            profile=P, tokenize=char_tokens)
    assert chunks
    assert all(c.book_id == "calc" for c in chunks)
    ids = [c.chunk_id for c in chunks]
    assert len(set(ids)) == len(ids)
    assert [c.seq for c in chunks] == sorted(c.seq for c in chunks)


def test_short_paragraphs_merge_into_target_sized_chunks():
    markdown = "# S\n\n" + "\n\n".join("aaaa" for _ in range(5)) + "\n"
    chunks = chunk_markdown(markdown, book_id="calc", profile=P, tokenize=char_tokens)
    assert any("\n\n" in c.text for c in chunks)   # 문단이 합쳐진 청크가 있어야 한다
    assert all(c.tokens <= P.max_tokens for c in chunks)


def test_blank_only_input_and_short_section_are_safe():
    assert chunk_markdown("   \n\n  \n", book_id="b", profile=P,
                          tokenize=char_tokens) == []
    chunks = chunk_markdown("# Short\n\nhi\n", book_id="b", profile=P,
                            tokenize=char_tokens)
    assert len(chunks) == 1
    assert chunks[0].text == "hi"
    assert chunks[0].tokens == 2   # min 아래여도 섹션 끝 청크는 허용


def test_same_input_gives_identical_chunks():
    markdown = "# A\n\nhello world\n\n# B\n\nbye\n"
    a = chunk_markdown(markdown, book_id="b", profile=P, tokenize=char_tokens)
    b = chunk_markdown(markdown, book_id="b", profile=P, tokenize=char_tokens)
    assert [(c.chunk_id, c.section, c.text) for c in a] == \
           [(c.chunk_id, c.section, c.text) for c in b]
