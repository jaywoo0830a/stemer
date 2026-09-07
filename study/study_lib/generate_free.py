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


# ---- 부분(개념 / 예제 / 연습·풀이) 분할 생성 --------------------------------
# 단일 슬롯 llama-server 를 순차로 돌며, 커다란 한 파일이 토큰 한도(16000)에서
# 잘리는 것을 피한다. 각 부분을 자기 파일로 저장 후 병합된 notes/<topic>.md 를 만든다.
_HEADER = (
    "---\n"
    "title: {title}\n"
    "subject: {subject}\n"
    "book: {book}\n"
    "section: {section}\n"
    "part: {part}\n"
    "generator: local-free\n"
    "---\n\n"
)

# PART_ORDER 순서로 병합한다.
PART_ORDER = ("concept", "examples", "practice")

# 각 언어·부분별 분리 프롬프트. system 은 해당 부분 "만" 쓰게 하고,
# user 끝맺음은 언어 글쓰기 지시가 담긴다(아래 LANGUAGE_KICK).
_PARTS = {
    "en": {
        "concept": (
            "You are an expert math tutor. Write ONLY the concept/lecture part of a study "
            "note for ONE topic -- a flowing, readable explanation section titled "
            "'## Reading the Topic.'\n"
            "Cover: what the idea is for, the intuition, precise definitions, why each key "
            "formula holds (derive or motivate it), and ONE common student mistake to avoid. "
            "Ground everything in the given textbook passages and cite inline like "
            "(textbook EXAMPLE 3) or (11.3 Exercises #7).\n"
            "Math in $...$ / $$...$$. Do NOT include examples, practice problems, or solutions "
            "here -- that is a separate part."
        ),
        "examples": (
            "You are an expert math tutor. Write ONLY the worked-examples part of a study note "
            "for ONE topic, titled '## Worked examples.'\n"
            "Give AT LEAST 3, preferably 4-5, examples of rising difficulty: a basic/template "
            "case, a typical exam-style case, and an application/word problem (invent a "
            "plausible labelled extension only if the passage lacks one).\n"
            "For EACH example give the FULL step-by-step **Solution.:** explain every "
            "algebraic/calculus move line by line (which rule and why), not just the final "
            "answer, and state the conclusion. Reference the textbook source inline when it "
            "matches. Math in $...$ / $$...$$."
        ),
        "practice": (
            "You are an expert math tutor. Write ONLY the practice-problems part of a study "
            "note for ONE topic, titled '## Practice problems.'\n"
            "Give AT LEAST 5 problems ordered by rising difficulty, spanning the key formula "
            "uses for this topic. Right after each problem give a FULLY WORKED **Solution.:** "
            "with all steps and the final answer (built-in answer key). Never leave a problem "
            "without its solved answer. Where a problem matches the textbook, cite like "
            "(11.3 Exercises #7). Math in $...$ / $$...$$."
        ),
    },
    "ko": {
        "concept": (
            "You are an expert math tutor. Write ONLY the concept/lecture part of a study note "
            "for ONE topic -- a flowing, readable explanation section titled "
            "'## Reading the Topic.'\n"
            "Cover: what the idea is for, the intuition, precise definitions, why each key "
            "formula holds (derive or motivate it), and ONE common student mistake to avoid. "
            "Ground everything in the given textbook passages and cite inline like "
            "(textbook EXAMPLE 3) or (11.3 Exercises #7).\n"
            "Math in $...$ / $$...$$. Do NOT include examples, practice problems, or solutions "
            "here -- that is a separate part."
        ),
        "examples": (
            "You are an expert math tutor. Write ONLY the worked-examples part of a study note "
            "for ONE topic, titled '## Worked examples.'\n"
            "Give AT LEAST 3, preferably 4-5, examples of rising difficulty: a basic/template "
            "case, a typical exam-style case, and an application/word problem (invent a "
            "plausible labelled extension only if the passage lacks one).\n"
            "For EACH example give the FULL step-by-step **Solution.:** explain every "
            "algebraic/calculus move line by line (which rule and why), not just the final "
            "answer, and state the conclusion. Reference the textbook source inline when it "
            "matches. Math in $...$ / $$...$$."
        ),
        "practice": (
            "You are an expert math tutor. Write ONLY the practice-problems part of a study "
            "note for ONE topic, titled '## Practice problems.'\n"
            "Give AT LEAST 5 problems ordered by rising difficulty, spanning the key formula "
            "uses for this topic. Right after each problem give a FULLY WORKED **Solution.:** "
            "with all steps and the final answer (built-in answer key). Never leave a problem "
            "without its solved answer. Where a problem matches the textbook, cite like "
            "(11.3 Exercises #7). Math in $...$ / $$...$$."
        ),
    },
}

# 유저 프롬프트 언어 지시 (en/ko  글꼴): 본문/해설만 해당 언어로.
_LANGUAGE_KICK = {
    "en": (
        "Write the study note, presenting all prose and solutions in English. "
        "Finish every example/problem completely -- do not truncate.\n"
        "Output exactly the requested part as markdown body only (no YAML header)."
    ),
    "ko": (
        "Write the study note, presenting all prose and solutions in Korean "
        "(math stays as symbols/LaTeX). Finish every example/problem completely -- "
        "do not truncate.\n"
        "Output exactly the requested part as markdown body only (no YAML header)."
    ),
}

# 각 부분의 최대 생성 토큰 (개념은 짧게, 연습·풀이는 길게).
_PART_MAX = {"concept": 5000, "examples": 12000, "practice": 16000}


def _part_user(topic, passages, lang: str, part: str) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return (
        "TOPIC: {topic}\n"
        "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
        "Textbook source passage (consult as needed):\n"
        "{passages}\n\n"
        "{kick}"
    ).format(
        topic=topic.title or topic.topic_id,
        book=topic.book_id, section=topic.section or "-",
        subject=topic.subject, passages=src, kick=_LANGUAGE_KICK[lang])


def _read_part_file(base: Path, topic_id: str, part: str) -> str | None:
    pf = base / f"{topic_id}.{part}.md"
    try:
        txt = pf.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return txt.split("---\n\n", 2)[-1].rstrip()  # front matter 뒤 본문


def run_free_parts(topic, llm, passages, notes_dir: str | Path,
                   parts=PART_ORDER, *, return_combined: bool = True) -> str:
    """topic 에 대해 일부(기본 전체) 부분을 생성·저장하고 병합본을 쓴다.

    parts 에 없는 나머지 부분은 이미 디스크에 있는 <topic>.<part>.md 본문을
    재사용해 병합에 포함한다(있을 때만). 단일 슬롯 llama-server 를 순차 사용.
    병합본 notes/<topic>.md 경로를 반환한다.
    """
    lang = _free_lang()
    topic_vars = dict(title=topic.title or topic.topic_id, subject=topic.subject,
                      book=topic.book_id, section=topic.section or "-")
    base = Path(notes_dir)
    base.mkdir(parents=True, exist_ok=True)

    part_bodies: dict[str, str] = {}
    # 새로 생성할 부분
    for part in parts:
        sysp = _PARTS[lang][part]
        usrp = _part_user(topic, passages, lang, part)
        res = llm.complete(system=sysp, user=usrp,
                           max_tokens=_PART_MAX.get(part, 8000),
                           json_object=False)
        body = res.content if isinstance(res.content, str) else str(res.content)
        body = (body or "").strip()
        part_bodies[part] = body
        pf = Path(base) / f"{topic.topic_id}.{part}.md"
        pf.write_text(_HEADER.format(part=part, **topic_vars) + body + "\n",
                      encoding="utf-8")
    # 생성하지 않은 나머지 부분: 디스크에 있으면 병합에 재사용
    for part in PART_ORDER:
        if part not in part_bodies:
            existing = _read_part_file(base, topic.topic_id, part)
            if existing:
                part_bodies[part] = existing

    combined = (base / f"{topic.topic_id}.md")
    body_chunks = [part_bodies[p] for p in PART_ORDER if part_bodies.get(p)]
    combined.write_text(_FRONT.format(**topic_vars) + "\n\n".join(body_chunks)
                        + "\n", encoding="utf-8")
    return str(combined)

