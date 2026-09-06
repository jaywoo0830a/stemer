"""그림 — 교재 원본만 (생성 금지, GEN-PROTOCOL 그림 정책).

클라이언트 관점:
    reg = FigureRegistry()
    reg.add(Figure(fig_id="f1", book_id="calc", section="3.5",
                   path="figs/f1.png", caption="A sketch", page=102))
    figs = reg.figures_for(book_id="calc", section_numbers="3.5, 3.6")  # 매핑 섹션 그림만
    md = attach_figures(rendered_md, figs)   # 없으면 무변경, 있으면 "## Figures" 추가

그림은 **절대 생성하지 않는다** — 검색된 교재 그림을 렌더된 노트에 부착만 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SECTION_NUM_RE = re.compile(r"\d+(?:\.\d+)+")


def _section_numbers(text: str) -> set[str]:
    return set(_SECTION_NUM_RE.findall(text))


def _wanted(text: str) -> set[str]:
    return {s.strip() for s in re.split(r"[,;]", text) if s.strip()}


@dataclass(frozen=True)
class Figure:
    fig_id: str
    book_id: str
    section: str        # 출처 섹션 (예: "3.5", "3.5 The Limit ...")
    path: str           # 교재 그림 파일 경로
    caption: str = ""
    page: int | None = None


class FigureRegistry:
    """그림 코퍼스 레지스트리 — 등록·섹션 조회."""

    def __init__(self) -> None:
        self._figures: list[Figure] = []

    def add(self, figure: Figure) -> Figure:
        if any(f.fig_id == figure.fig_id for f in self._figures):
            raise ValueError(f"figure {figure.fig_id!r} already registered")
        self._figures.append(figure)
        return figure

    def figures(self, *, book_id: str | None = None) -> list[Figure]:
        return [f for f in self._figures
                if book_id is None or f.book_id == book_id]

    def figures_for(self, book_id: str, section_numbers) -> list[Figure]:
        """매핑 섹션 번호와 일치하는 교재 그림만 (없으면 빈 목록 = 그림 없음)."""
        wanted = _wanted(section_numbers) if isinstance(section_numbers, str) \
            else {str(s) for s in section_numbers}
        if not wanted:
            return []
        return [
            f for f in self._figures
            if f.book_id == book_id and (_section_numbers(f.section) & wanted)
        ]


def attach_figures(markdown: str, figures: list[Figure]) -> str:
    """렌더된 노트 끝에 '## Figures' 섹션으로 교재 그림을 부착한다.

    - 그림이 없으면 입력을 그대로 돌려준다 (그림 없음 = 만들지 않음).
    - 이미 '## Figures' 가 있으면 멱등하게 그대로 둔다.
    - 캡션·페이지 출처는 서버 메타데이터에서 주입한다 (모델 출력 아님).
    """
    if not figures:
        return markdown
    if "\n## Figures" in markdown:
        return markdown

    lines = [markdown.rstrip("\n"), "", "## Figures", ""]
    for i, fig in enumerate(figures, 1):
        alt = fig.caption or fig.fig_id
        lines.append(f"![{alt}]({fig.path})")
        caption_line = f"*Figure {i}." + (f" {fig.caption}" if fig.caption else "") + "*"
        lines.append(caption_line)
        if fig.page is not None:
            lines.append(f"*(source: {fig.book_id}, p.{fig.page})*")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
