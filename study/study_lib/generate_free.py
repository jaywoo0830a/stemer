"""generate_free — 스키마/렌더 없이 로컬 LLM 이 곧장 마크다운 학습자료를 씀.

배경(실증): Qwen3-14B·R1 같은 '해설형/추론' 로컬 모델은 상세 구조화 payload
(스키마 JSON) 요구를 안 지키고 자연스러운 해설 md 를 쓴다 → 그 강점을 그대로,
"한 편의 markdown"을 직접 만들게 한다(LOCAL_LLM_FREE=1).

- 입력: topic(제목/책/섹션) + 교재 passage(store retrieve).
- 출력: notes/<topic>.md (앞에 YAML front matter 추가, 본문은 모델이 쓴 md 그대로).
- 시스템/유저 프롬프트가 "교재를 근거로 개념을 풀어 설명 + 예제/연습(n개) + 출처"를
  요구해 우리가 원하는 학습자료 형태를 유도한다. 슬롯별 hard-validation 은 없다.
- CLI: study_lib.cli 의 _generate-free 경로(env LOCAL_LLM_FREE=1)로 갈 때 사용.
  llm 은 'complete_text' 가 아니라 LLMClient(complete) 로, json_object=False 로 호출.
"""
from __future__ import annotations

from pathlib import Path

from .registry import DRAFT

_FRONT = (
    "---\n"
    "title: {title}\n"
    "subject: {subject}\n"
    "book: {book}\n"
    "section: {section}\n"
    "generator: local-free\n"
    "---\n\n"
)

SYSTEM = (
    "You are a patient math/sciences tutor writing concise study notes for one topic.\n"
    "RULES:\n"
    "- Write in Korean, human-readable prose that builds understanding (not a rigid "
    "box list): start from what the idea answers, then definitions+why, key formulas, "
    "common mistake, then WORKED EXAMPLE(S) and PRACTICE problems -- grounded in the "
    "given textbook passages (cite source like (교재 EXAMPLE 3) or (11.3 Exercises #7)).\n"
    "- Markdown only. Wrap every math expression in $...$; one short symbol inline, "
    "do not leave bare math outside $."
)

USER_TPL = (
    "TOPIC: {topic}\n"
    "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
    "교재 출처 passage (필요한 만큼 참고):\n"
    "{passages}\n\n"
    "위 토픽의 학습자료(markdown 본문만, header 없이)를 작성하라."
)


def build_user(topic, passages) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return USER_TPL.format(topic=topic.title or topic.topic_id,
                           book=topic.book_id, section=topic.section or "-",
                           subject=topic.subject, passages=src)


def run_free_one(topic, llm, passages, notes_dir: str | Path) -> str:
    """topic 의 자유 md 학습자료를 notes_dir/<topic>.md 로 저장, 경로 반환."""
    try:
        res = llm.complete(system=SYSTEM, user=build_user(topic, passages),
                           max_tokens=16000, json_object=False)
    except Exception as exc:  # noqa: BLE001
        raise exc
    body = res.content if isinstance(res.content, str) else str(res.content)
    front = _FRONT.format(title=topic.title or topic.topic_id, subject=topic.subject,
                          book=topic.book_id, section=topic.section or "-")
    p = Path(notes_dir) / f"{topic.topic_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(front + (body or "").strip() + "\n", encoding="utf-8")
    return str(p)
