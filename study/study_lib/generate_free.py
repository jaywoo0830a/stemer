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

import os

# 언어에 따른 프롬프트 조각. LOCAL_FREE_LANG=ko 이면 한글로, 기본은 영어.
_LANG = {
    "en": {
        "system": (
            "You are an expert math tutor writing a self-contained study note for ONE topic.\n"
            "The note must actually TEACH: a reader should be able to redo every step.\n\n"
            "STRUCTURE (in this order):\n"
            "1. ## Reading the Topic -- a flowing, readable explanation: what the idea is for, "
            "   the intuition, definitions, why each key formula holds, and the one common mistake "
            "   students make. Ground it in the textbook passages.\n"
            "2. ## Worked examples -- provide AT LEAST 3, preferably 4-5, of increasing difficulty:\n"
            "   one basic/template case, one typical exam-style case, and one application/word "
            "   problem (create a plausible extension if the passage has no word problem). "
            "   For EACH example include the FULL step-by-step solution headed **Solution.**: "
            "   explain each algebraic/calculus move line by line (what rule is applied and why), "
            "   not just the final answer. State the conclusion explicitly.\n"
            "3. ## Practice problems -- provide AT LEAST 5, ordered by rising difficulty, then "
            "   right below each give a fully worked **Solution**: (steps + final answer, as a "
            "   built-in answer key). Do not leave problems unresolved.\n\n"
            "QUALITY RULES:\n"
            "- Be mathematically correct. Never state a false step; if a step relies on a theorem, "
            "   say so (e.g. 'f is continuous/positive/decreasing on [1,oo), so Integral Test applies').\n"
            "- Cite the textbook source inline like (textbook EXAMPLE 3) or (11.3 Exercises #7); "
            "   create a labelled extension only when the passage lacks the needed item.\n"
            "- Markdown only. Wrap every math expression in $...$ (display: $$...$$). One short "
            "   symbol inline, never leave bare math outside $."
        ),
        "user": (
            "TOPIC: {topic}\n"
            "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
            "Textbook source passage (consult as needed):\n"
            "{passages}\n\n"
            "Write this topic's study note in English now (markdown body only, no YAML header). "
            "Keep the Reading concise but make the WORKED EXAMPLES (>=3) and PRACTICE with full "
            "solutions (>=5) the strongest part. Do not truncate -- finish every solution."
        ),
        "cite": "textbook EXAMPLE 3",
    },
    "ko": {
        "system": (
            "You are an expert math tutor writing a self-contained study note for ONE topic.\n"
            "The note must actually TEACH: a reader should be able to redo every step.\n\n"
            "STRUCTURE (in this order):\n"
            "1. ## Reading the Topic -- a flowing, readable explanation: what the idea is for, "
            "   the intuition, definitions, why each key formula holds, and the one common mistake "
            "   students make. Ground it in the textbook passages.\n"
            "2. ## Worked examples -- provide AT LEAST 3, preferably 4-5, of increasing difficulty:\n"
            "   one basic/template case, one typical exam-style case, and one application/word "
            "   problem (create a plausible extension if the passage has no word problem). "
            "   For EACH example include the FULL step-by-step solution headed **Solution.**: "
            "   explain each algebraic/calculus move line by line (what rule is applied and why), "
            "   not just the final answer. State the conclusion explicitly.\n"
            "3. ## Practice problems -- provide AT LEAST 5, ordered by rising difficulty, then "
            "   right below each give a fully worked **Solution**: (steps + final answer, as a "
            "   built-in answer key). Do not leave problems unresolved.\n\n"
            "QUALITY RULES:\n"
            "- Be mathematically correct. Never state a false step; if a step relies on a theorem, "
            "   say so (e.g. 'f is continuous/positive/decreasing on [1,oo), so Integral Test applies').\n"
            "- Cite the textbook source inline like (textbook EXAMPLE 3) or (11.3 Exercises #7); "
            "   create a labelled extension only when the passage lacks the needed item.\n"
            "- Markdown only. Wrap every math expression in $...$ (display: $$...$$). One short "
            "   symbol inline, never leave bare math outside $."
        ),
        "user": (
            "TOPIC: {topic}\n"
            "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
            "Textbook source passage (consult as needed):\n"
            "{passages}\n\n"
            "Write this topic's study note, presenting the body text in Korean. "
            "Keep Reading concise but make WORKED EXAMPLES (>=3) and PRACTICE with full "
            "solutions (>=5) the strongest part. Do not truncate -- finish every solution."
        ),
        "cite": "textbook EXAMPLE 3",
    },
}


def _free_lang() -> str:
    lang = os.environ.get("LOCAL_FREE_LANG", "en").strip().lower()
    return lang if lang in _LANG else "en"


def _prompts() -> dict:
    return _LANG[_free_lang()]


def system_prompt() -> str:
    return _prompts()["system"]


def build_user(topic, passages) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return _prompts()["user"].format(
        topic=topic.title or topic.topic_id,
        book=topic.book_id, section=topic.section or "-",
        subject=topic.subject, passages=src)


def run_free_one(topic, llm, passages, notes_dir: str | Path) -> str:
    """topic 의 자유 md 학습자료를 notes_dir/<topic>.md 로 저장, 경로 반환."""
    try:
        res = llm.complete(system=system_prompt(), user=build_user(topic, passages),
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
