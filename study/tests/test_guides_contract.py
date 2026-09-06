"""과목 가이드(B) 계약 — 콘텐츠 존재·핵심 표지 검사.

가이드는 캐시되는 고정 상수(프롬프트 prefix B)이므로, 파일이 항상 존재하고
해당 과목을 쓰는 데 필요한 표지(문법·슬롯 지침)를 담고 있어야 한다.
"""
from study_lib.cli import default_guide_path


def test_math_guide_exists_and_covers_notation_and_slots():
    text = default_guide_path("math").read_text(encoding="utf-8")
    assert len(text) > 500
    assert "## " in text
    assert "formula" in text.lower()
    assert "payload" in text.lower()


def test_phys_guide_exists_and_mentions_units_laws_and_sign():
    text = default_guide_path("phys").read_text(encoding="utf-8")
    assert len(text) > 500
    assert "unit" in text.lower()
    assert "law" in text.lower()
    assert "sign" in text.lower()


def test_chem_guide_exists_and_mentions_reactions_and_species():
    text = default_guide_path("chem").read_text(encoding="utf-8")
    assert len(text) > 500
    assert "reaction" in text.lower()
    assert "species" in text.lower()
    assert "balance" in text.lower()


def test_bio_guide_exists_and_mentions_pathways_and_cycles():
    text = default_guide_path("bio").read_text(encoding="utf-8")
    assert len(text) > 500
    assert "pathway" in text.lower()
    assert "cycle" in text.lower()
    assert "structure" in text.lower()


def test_unwritten_subject_guide_returns_empty_gracefully():
    # 미작성 과목(예: astro)은 기본 경로에 파일이 없으면 빈 문자열로 안전 처리
    assert not default_guide_path("astro").exists()
