#!/usr/bin/env python
"""docling 범위 파싱 미리보기 — 페이지 범위만 처리해 헤딩 구조/시간 검증.

사용:
    python tools/parse_range_preview.py <pdf> <start> <end>
    # 예: study/미적분.pdf 42-141 (100페이지) — 최적화 DoclingParser 그대로 사용

출력:
    - 처리 시간 (docling 성능 최적화 실측)
    - `## N.N 제목` 헤딩 여부 (discover 가 토픽을 잡을 수 있는지)
    - 페이지 범위 밖 본문이 섞이지 않았는지
"""
from __future__ import annotations

import sys
import time

from study_lib.parse import parse_source


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    path, start, end = argv
    page_range = f"{start}-{end}"

    print(f"parsing {path} pages {page_range} (docling optimized)...")
    t0 = time.monotonic()
    book = parse_source(path, profile="docling", book_id="probe",
                        page_range=page_range)
    el = time.monotonic() - t0

    lines = book.markdown.splitlines()
    heads = [ln for ln in lines if ln.startswith("## ")]
    heads1 = [ln for ln in lines if ln.startswith("# ") and not ln.startswith("## ")]

    print(f"\n== done in {el:.0f}s | chars={len(book.markdown)} | "
          f"## headings={len(heads)} | # headings={len(heads1)}")
    print("\n-- ## headings (numbered-section candidates):")
    for h in heads[:40]:
        print(h[:110])
    print("\n-- # headings (chapter-level):")
    for h in heads1[:15]:
        print(h[:110])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
