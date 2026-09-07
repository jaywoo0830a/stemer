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

from .llm import LLMError
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

# ---- 단일 파일 전체 교재 시스템 (run_free_one 용: 3개 부분을 한 호출에) ----
_FULL_SYSTEM = (
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
)


def _full_user(topic, passages) -> str:
    src = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    return (
        "TOPIC: {topic}\n"
        "BOOK: {book}   SECTION: {section}   SUBJECT: {subject}\n\n"
        "Textbook source passage (consult as needed):\n"
        "{passages}\n\n"
        "Write this topic's complete study note in English now (markdown body only, no YAML "
        "header): Reading the Topic, WORKED EXAMPLES (>=3) and PRACTICE with full solutions "
        "(>=5). Do not truncate -- finish every solution."
    ).format(
        topic=topic.title or topic.topic_id,
        book=topic.book_id, section=topic.section or "-",
        subject=topic.subject, passages=src)


def run_free_one(topic, llm, passages, notes_dir: str | Path) -> str:
    """topic 의 자유 md 학습자료를 notes_dir/<topic>.md 로 저장, 경로 반환(영어)."""
    try:
        res = llm.complete(system=_FULL_SYSTEM, user=_full_user(topic, passages),
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

# ---- 공통 최상 품질 표준(DeepSeek-R1-Distill-Qwen-32B 등 강 추론 모델용) ----
# 아래 _STD 는 모든 부분 앞에 붙는 공통 원칙이다. 출력 언어는 항상 영어(_EN_KICK)다.
# 목표: 기술적으로 틀림 없고 왜 그 단계인지 설명하며, 예제·문제가 '진짜 훈련'이 되는 교재.
_STD = (
    "You are a rigorous university math tutor writing material that a motivated student "
    "can study WITHOUT the textbook open. Every claim must be checkable from your words "
    "and the cited passage; when a theorem is used you must state and verify its "
    "HYPOTHESES (e.g. 'f is continuous on [1,∞), positive, and decreasing' BEFORE applying "
    "the Integral Test).\n"
    "Language: prose should flow like a good lecturer -- never a bullet dump -- yet every "
    "logical step must be explicit.\n"
    "Math: write ALL mathematics in LaTeX inside $...$ (inline) or $$...$$ (display). Do "
    "not leave a bare symbol outside dollars. Use \\sum, \\int, \\frac, \\lim, \\sqrt "
    "properly; no plain-text math.\n"
    "Notation must be introduced before use and reused consistently. State the result at "
    "the end of each worked item in a boxed/emphasised form (e.g. '**Answer.** $S=...$').\n"
    "Ground in the given passages: quote or paraphrase, and cite inline the way the "
    "passage labels items -- e.g. (textbook EXAMPLE 3), (11.3 Exercises #7) -- or mark "
    "genuinely new items as (extension).\n"
    "Never write anything mathematically false. If you are not fully certain of a numeric "
    "fact, do the arithmetic carefully in the working and show it.\n"
)

# 부분별 작업 지시. 평문은 부분 '만' 출력한다.
_PARTS = {
    "concept": (
        "Write ONLY the concept/lecture part of the study note: a section titled "
        "'## Reading the Topic'.\n"
        "Give a connected lecture (several paragraphs, not a box list) that:\n"
        "1. MOTIVATES: the concrete question the idea answers and why a student should care.\n"
        "2. BUILDS INTUITION first, then gives the precise definition(s) and theorem(s) with "
        "   the exact hypotheses and what each hypothesis is FOR.\n"
        "3. Justifies every key formula: either a short derivation or a clear reason it holds "
        "   (name the ingredient, e.g. 'this is just the limit definition of the integral').\n"
        "4. Shows one worked non-example or the single most common student misconception and "
        "   why it is wrong.\n"
        "5. Tells the reader 'what to check' before using the tool (a compact procedure box).\n"
        "No solved practice items go here; that is a separate part. End with a short "
        "'You are ready when...' self-check list."
    ),
    "examples": (
        "Write ONLY the worked-examples part: a section titled '## Worked examples'.\n"
        "Provide 4-6 examples of clear increasing difficulty and variety:\n"
        "  (i) one template/basic case, "
        "(ii) one typical exam-style case with a trap or a common wrong turn, "
        "(iii) one case requiring combining two techniques, and "
        "(iv) one application / word problem (invent a plausible labelled extension only if "
        "the passage provides no such problem).\n"
        "Format each example as:\n"
        "  ### Example N (source tag)\n"
        "  **Problem.** precise statement in one or two sentences.\n"
        "  **Solution.** a complete, line-by-line derivation: name each rule/step as you use "
        "it (e.g. 'substitute u=...', 'compare with p-series p=3/2>1'), verify any theorem "
        "hypotheses explicitly, do the algebra/integral/limit in displayed steps, then give "
        "the emphasised **Answer.** -- nothing left to the reader's imagination.\n"
        "Add ONE short 'why this step matters' remark to at least two examples to teach "
        "technique, not just answers."
    ),
    "practice": (
        "Write ONLY the practice-problems part: a section titled '## Practice problems'\n"
        "The problems are a graduated training set with a BUILT-IN FULL ANSWER KEY (this "
        "note doubles as the solution manual):\n"
        "Provide 6-10 problems ordered from routine to challenging. Across the set, cover "
        "every technique/formula introduced for this topic, and label each problem's "
        "difficulty '[basic]', '[standard]', '[challenge]' and its skill (e.g. computational / "
        "conceptual / proof / application).\n"
        "Format each as:\n"
        "  ### Problem N (difficulty · skill, source tag)\n"
        "  **Problem.** precise wording, no ambiguity about what is asked.\n"
        "  (leave a short blank '**Work area.**' line)\n"
        "  **Solution.** the complete worked answer: state the method, verify hypotheses, "
        "show every step in displayed math, and end with the final **Answer.**.\n"
        "Make the [challenge] items genuinely require care (a proof, a counterexample, or a "
        "multi-step synthesis) -- not just bigger numbers. Do not strand any problem without "
        "its solved answer."
    ),
}

# English-only: 본문 해설/풀이는 항상 영어.
_EN_KICK = (
    "Present all prose, explanations, and solutions in English. Output exactly the "
    "requested part as clean markdown body only (no YAML header). Do not truncate -- "
    "finish every example and every solution completely before stopping."
)

# 각 부분의 최대 생성 토큰. ctx 16384 · prompt ≤~4096 인 채 남는 생성 여유 ≈ ~12k.
# R1 이 reasoning 을 먼저 쓰므로 content 만 뽑으면 실제는 더 짧다. 그래서 요청 상한을
# 가능한 '풍부'로 두되(concept/examples/practice 각각 독립 요청) ctx 를 넘기지는 않게:
# 각 부분 최대를 생성 여유 끝(10k~12k)에 맞춘다.
_PART_MAX = {"concept": 6000, "examples": 12000, "practice": 14000}


def _part_user(topic, passages, part: str) -> str:
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
        subject=topic.subject, passages=src, kick=_EN_KICK)


def _part_system(part: str) -> str:
    """부분 시스템 프롬프트 = 공통 품질 표준 + 해당 부분 작업 지시."""
    return _STD + "\n" + _PARTS[part]


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

    각 부분은 완료 즉시 자기 파일(<topic>.<part>.md)에 저장한다. 부분 하나가
    실패해도 이후 부분은 계속 진행(장시간 무인 실행용). realtime log 는 들을 수
    있도록 각 부분 완료/실패를 stdout 에 남긴다(log 인자).
    parts 에 없는 나머지는 디스크의 기존 <topic>.<part>.md 를 재사용해 병합에
    포함한다. 전부 실패(재사용할 것도 없음)면 마지막에 LLMError 를 던진다.
    병합본 notes/<topic>.md 경로를 반환한다.
    """
    import sys

    topic_vars = dict(title=topic.title or topic.topic_id, subject=topic.subject,
                      book=topic.book_id, section=topic.section or "-")
    base = Path(notes_dir)
    base.mkdir(parents=True, exist_ok=True)

    part_bodies: dict[str, str] = {}
    generated: list[str] = []
    # 새로 생성할 부분 (부분별로 독립 생성·저장 — 실패해도 나머지는 계속)
    for part in parts:
        sysp = _part_system(part)
        sub = passages_for_part(part, passages)   # 권고안 B: part 전용 passage
        usrp = _part_user(topic, sub, part)
        print(f"[free:{part}] generating {topic.topic_id} "
              f"(passages {len(passages)}→{len(sub)}, "
              f"max_tokens={_PART_MAX.get(part, 8000)})...", flush=True)
        try:
            res = llm.complete(system=sysp, user=usrp,
                               max_tokens=_PART_MAX.get(part, 8000),
                               json_object=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[free:{part}] FAILED {topic.topic_id}: {exc}",
                  file=sys.stderr, flush=True)
            continue
        body = res.content if isinstance(res.content, str) else str(res.content)
        body = (body or "").strip()
        part_bodies[part] = body
        generated.append(part)
        pf = Path(base) / f"{topic.topic_id}.{part}.md"
        pf.write_text(_HEADER.format(part=part, **topic_vars) + body + "\n",
                      encoding="utf-8")
        print(f"[free:{part}] done -> {pf.name} ({len(body)} chars)",
              flush=True)
    # 생성하지 않은 나머지 부분: 디스크에 있으면 병합에 재사용
    for part in PART_ORDER:
        if part not in part_bodies:
            existing = _read_part_file(base, topic.topic_id, part)
            if existing:
                part_bodies[part] = existing

    body_chunks = [part_bodies[p] for p in PART_ORDER if part_bodies.get(p)]
    if not body_chunks:
        raise LLMError(
            f"all requested parts failed for {topic.topic_id} and no part file "
            "exists on disk to reuse")
    combined = (base / f"{topic.topic_id}.md")
    combined.write_text(_FRONT.format(**topic_vars) + "\n\n".join(body_chunks)
                        + "\n", encoding="utf-8")
    ok_parts = [p for p in PART_ORDER if p in part_bodies]
    print(f"[free] merged {topic.topic_id}: ok_parts={ok_parts} "
          f"-> {combined.name}", flush=True)
    return str(combined)


# ---- 입력 컨텍스트 균형(CPU 백엔드용) ------------------------------------
# 총 ctx = 16384(804를 8081 llama-server). CPU(9700X) 는 프롬프트가 ~4k 를
# 넘기며 O(n^2) 어텐션이 비선형 폭주하므로, passage 예산 기본을 ~4096 으로 잡는다.
# env LOCAL_FREE_INPUT_TOKENS 로 상향 조정 가능(그러나 ctx 여유 초과 주의).
MAX_INPUT = 16384      # 총 컨텍스트 (llama-server --ctx-size)
HARD_CTX = MAX_INPUT
_SCAFFOLD_EST = 640    # 시스템+토픽 헤더 등 passage 외 고정 오버헤드 근사
DEFAULT_INPUT_TOKENS = 4096   # CPU 백엔드 실용 입력 한도



def input_budget() -> int:
    """입력 passage 에만 쓸 토큰 예산(권고안 A). env LOCAL_FREE_INPUT_TOKENS 로 조정.
    상한 ≈ MAX_INPUT - _SCAFFOLD_EST(스캐폴드 포함해도 ctx 안쪽)이며 기본은 4096."""
    import os
    try:
        want = int(os.environ.get("LOCAL_FREE_INPUT_TOKENS",
                                  DEFAULT_INPUT_TOKENS))
    except ValueError:
        want = DEFAULT_INPUT_TOKENS
    cap = MAX_INPUT - _SCAFFOLD_EST
    return max(1000, min(want, cap))


def estimate_tokens(text: str) -> int:
    """토크나이저 없을 때의 문자 근사(한글 혼합 3자 당 ~1토큰)."""
    return max(1, (len(text) + 2) // 3)


def pack_passages(passages, max_tokens: int | None = None,
                  *, count_tokens=None) -> list[str]:
    """관련성 우선의 passages 를 입력 토큰 예산 내 최장 접두부로 자른다.

    - max_tokens: 주어지면 이 값 사용, 아니면 input_budget().
    - count_tokens: (str)->int; 없으면 estimate_tokens 근사.
    """
    if max_tokens is None:
        max_tokens = input_budget()
    if count_tokens is None:
        count_tokens = estimate_tokens
    # 긴 passage 하나가 예산을 넘는 최악을 위해 시스템/유저 고정 오버헤드는
    # 여기서 여유로 보지 않고 estimate 의 안전폭으로 다룬다.
    used = 0
    packed: list[str] = []
    for p in passages:
        n = count_tokens(p)
        if used + n > max_tokens:
            if not packed:          # 첫 passage 조차 넘침 → 강제 1개 포함
                packed.append(p)
            break
        packed.append(p)
        used += n
    return packed


# ---- 부분별 passage 전문화(권고안 B) ---------------------------------------
# 세 부분이 같은 passages 전체를 그대로 반복해 보내지 않도록, 각 part 에 어울리는
# passage 를 우선 뽑아 그 부분에만 쓴다(전체 예산보다 작게). passage 텍스트에
# 예/연습/정의 등의 표식이 있으면 그 표식에 맞는 조각을 높은 우선순위로 선택하고,
# 부족하면 앞쪽(primary) passage 로 채워 grounding 을 유지한다.
_PART_KEYWORDS = {
    "concept": ["definition", "define", "theorem", "정의", "정리", "introduction",
                "continuous", "increasing", "decreasing", "property"],
    "examples": ["example", "sample", "solution", "예제", "예 ", "worked"],
    "practice": ["exercise", "problem", "practice", "연습", "문제", "#7", "#24"],
}
_DEFAULT_PART_TOKENS = 2048    # 각 part 의 passage 예산 (권고안 B)


def _part_score(part: str, text: str) -> int:
    low = text.lower()
    return sum(low.count(k.lower()) for k in _PART_KEYWORDS.get(part, []))


def part_token_cap() -> int:
    """각 part 의 passage 예산(기본 2048, env LOCAL_FREE_PART_TOKENS 로 조정). 전역
    input_budget() 을 초과하지 않도록 상한도 함께 적용한다."""
    import os
    try:
        want = int(os.environ.get("LOCAL_FREE_PART_TOKENS",
                                  _DEFAULT_PART_TOKENS))
    except ValueError:
        want = _DEFAULT_PART_TOKENS
    return max(256, min(want, input_budget()))


def passages_for_part(part: str, passages, *, max_tokens: int | None = None,
                      count_tokens=None):
    """part 에 어울리는 passage 부분집합 (예산 내, 최소한의 grounding 유지)."""
    if not passages:
        return []
    if max_tokens is None:
        max_tokens = part_token_cap()
    if count_tokens is None:
        count_tokens = estimate_tokens
    # 맨 앞 'primary' 일부는 항상 유지 (모든 part 의 공통 근거)
    core_n = max(1, min(len(passages), max(2, len(passages) // 5)))
    core = passages[:core_n]
    # 나머지를 part 키워드 점수에 따라 내림차순 정렬 (동률은 원순서)
    rest = list(enumerate(passages[core_n:], start=core_n))
    rest_sorted = sorted(rest, key=lambda it: (-_part_score(part, it[1]), it[0]))
    ordered = list(core) + [p for _, p in rest_sorted]
    return pack_passages(ordered, max_tokens, count_tokens=count_tokens)

