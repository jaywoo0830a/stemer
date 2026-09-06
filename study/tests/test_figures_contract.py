"""그림 계약 — 클라이언트 관점 테스트.

그림은 **교재 원본만** 사용한다(GEN-PROTOCOL 그림 정책): 레지스트리에서 매핑 섹션의
교재 그림을 조회하고, 렌더된 노트에 부착한다. 그림이 없으면 만들지 않는다.
"""
import pytest

from study_lib.figures import Figure, FigureRegistry, attach_figures


def test_figures_for_returns_only_matching_section_of_that_book():
    reg = FigureRegistry()
    reg.add(Figure("f1", "calc", "3.5 The Limit of a Sequence",
                   "figs/f1.png", "A sketch", page=102))
    reg.add(Figure("f2", "calc", "3.2 Computing Limits", "figs/f2.png", "B"))
    reg.add(Figure("f3", "calc", "4.1 The Derivative", "figs/f3.png", "C"))
    figs = reg.figures_for("calc", "3.5, 3.6")
    assert [f.fig_id for f in figs] == ["f1"]


def test_figures_are_scoped_to_book():
    reg = FigureRegistry()
    reg.add(Figure("a1", "calc", "3.5", "a.png"))
    reg.add(Figure("b1", "other", "3.5", "b.png"))
    assert [f.fig_id for f in reg.figures_for("calc", "3.5")] == ["a1"]
    assert [f.fig_id for f in reg.figures_for("other", "3.5")] == ["b1"]


def test_no_matching_figure_means_no_figure():
    reg = FigureRegistry()
    reg.add(Figure("f1", "calc", "3.5", "f1.png"))
    assert reg.figures_for("calc", "9.9") == []
    assert reg.figures_for("calc", "") == []


def test_duplicate_figure_id_is_rejected():
    reg = FigureRegistry()
    reg.add(Figure("f1", "calc", "3.5", "f1.png"))
    with pytest.raises(ValueError, match="already registered"):
        reg.add(Figure("f1", "calc", "4.1", "f1b.png"))


def test_attach_figures_appends_section_with_caption_and_source():
    md = "# Notes\n\nbody\n"
    figs = [Figure("f1", "calc", "3.5", "figs/f1.png", "A sketch", page=12)]
    out = attach_figures(md, figs)
    assert out.startswith("# Notes")
    assert "## Figures" in out
    assert "![A sketch](figs/f1.png)" in out
    assert "*Figure 1. A sketch*" in out
    assert "*(source: calc, p.12)*" in out


def test_attach_without_figures_returns_markdown_unchanged():
    md = "# Notes\n\nbody\n"
    assert attach_figures(md, []) == md


def test_attach_is_idempotent():
    md = "# Notes\n\nbody\n"
    figs = [Figure("f1", "calc", "3.5", "figs/f1.png", "A sketch")]
    once = attach_figures(md, figs)
    twice = attach_figures(once, figs)
    assert once == twice
