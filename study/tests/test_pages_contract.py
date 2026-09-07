"""pages — 페이지 구간(span) 유틸 계약 테스트.

docling 이 page 를 개별 추출하지는 않지만, 증분 누적 인제스트에서 "어느 페이지가
이미 책 store 에 들어 있고(inventory), 이번 요청 중 새 페이지만 docling 으로 돌릴지"
를 판정하는 데 순수 로직으로 쓴다.

규칙:
- span 은 (start, end) 1-based inclusive. disjoint·정렬 유지.
- `missing_spans(held, requested)`: requested 중 held 에 없는 페이지 구간들.
- `add_and_merge(held, request)`: 이번에 실제 인제스트된 구간을 병합한 결과.
"""
from study_lib.pages import add_and_merge, cover, missing_spans


def test_missing_entire_range_when_nothing_held():
    assert missing_spans([], (42, 47)) == [(42, 47)]


def test_missing_partial_overlap():
    # 하나도 안 든 8-10 만 새로 필요하다 (42-90 은 있음)
    held = [(42, 90)]
    assert missing_spans(held, (88, 95)) == [(91, 95)]
    assert missing_spans(held, (40, 43)) == [(40, 41)]
    assert missing_spans(held, (45, 46)) == []       # 전부 이미 소유


def test_missing_splits_around_gap():
    # requested (95,105): 42-90 과 100-110 이 이미 있어 91~99 위치가 비어있다.
    # 단 95-99 는 두 구간 사이 gap 아님? (100-110 은 95~105 중 100-105 를 이미 가짐)
    # → 새로 필요한 것만: (95, 99). 105 는 100-110 에 이미 속함.
    held = [(42, 90), (100, 110)]
    assert missing_spans(held, (95, 105)) == [(95, 99)]


def test_missing_disjoint_held():
    # req (90,202): 90은 42-90에, 200-202는 (200,210)에 이미 → gap (91,199)만
    held = [(42, 90), (200, 210)]
    assert missing_spans(held, (90, 202)) == [(91, 199)]


def test_add_merge_unions_adjacent():
    # disjoint 병합 — 인접/중복은 하나로
    assert add_and_merge([], (42, 47)) == [(42, 47)]
    assert add_and_merge([(42, 47)], (44, 90)) == [(42, 90)]
    assert add_and_merge([(42, 47), (60, 70)], (48, 60)) == [(42, 70)]


def test_merge_sorted_disjoint():
    assert add_and_merge([(60, 70), (10, 20)], (5, 12)) == [(5, 20), (60, 70)]


def test_cover():
    held = [(42, 90), (100, 110)]
    assert cover(held, 42) is True
    assert cover(held, 91) is False
    assert cover(held, 105) is True
    assert cover(held, 111) is False
