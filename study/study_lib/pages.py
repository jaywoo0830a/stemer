"""pages — 페이지 구간(span) 유틸 (증분 누적 인제스트용).

docling 은 PDF 페이지를 개별 추출하지 않지만, 이 유틸의 순수 로직으로
"책이 이미 인제스트한(store 에 넣은) 페이지 목록(inventory)"과 새 요청을 관리해
- 같은 페이지를 두 번 docling 으로 파싱하는 중복을 막고(멱등·구간 누적)
- 여러 조각이 같은 한 책 store 에 쌓여 결국 전체 DB 가 되게 한다.

표현: span 은 (start, end) (1-based inclusive). list 는 항상 disjoint·정렬.
"""
from __future__ import annotations

from itertools import chain


def _norm(span) -> tuple[int, int]:
    s, e = span
    return (int(s), int(e))


def merge(held: list, new_span) -> list:
    """정렬·disjoint 로 유지하며 new_span 을 병합한 사본을 돌려준다."""
    s, e = _norm(new_span)
    if e < s:
        return sorted(held)
    merged = sorted([_norm(x) for x in held] + [(s, e)])
    out: list = []
    for lo, hi in merged:
        if out and lo <= out[-1][1] + 1:   # 겹치거나 인접하면 이어 붙임
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def add_and_merge(held: list, request) -> list:
    """이번에 실제로 인제스트하기로 한 구간을 병합한 목록."""
    return merge(held, request)


def cover(held: list, page: int) -> bool:
    """page 가 held(이미 소유 구간)에 속하는가."""
    page = int(page)
    # 작은 목록이므로 이중탐색 대신 단순 순회 (대부분 조각 수가 적음)
    for lo, hi in held:
        if lo <= page <= hi:
            return True
    return False


def missing_spans(held: list, requested) -> list:
    """requested 중 held 에 아직 없는 페이지 구간들(부분 중복 시에만 새 것).

    예) held=[(42,90)], requested=(88,95) → [(91,95)]
    """
    r0, r1 = _norm(requested)
    if r1 < r0:
        return []
    # held 를 지나며 r0..r1 중 covered 가 아닌 조각들을 수집
    want: list = []
    cur = r0
    segs = merge([], (r0, r1))
    for lo, hi in segs:
        p = lo
        while p <= hi:
            # 연속된 미커버 구간을 한 스팬으로
            q = p
            while q <= hi and not cover(held, q):
                q += 1
            if q > p:
                want.append((p, q - 1))
            p = q
            while p <= hi and cover(held, p):
                p += 1
    return want
