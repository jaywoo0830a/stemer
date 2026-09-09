# stemer — 매일 공부 루틴 가이드

> **한 줄 목표:** 매일 **공부할 페이지 범위를 책에서 골라 → 그 페이지를 로컬 store(RAG)에 넣고
> → 개념·예제·문제를 책 근거로 로컬 LLM에 물어보는 것.**

## 1. 결론 — 당신이 매일 하는 일 (3단계)

```
① 공부할 페이지 범위를 정한다   → accumulate (store에 누적, 기존 유지·멱등)
② 물어본다 (책 근거로)          → agent 질문 (POST /run-plans + rag:"store")
③ 이어서 범위를 넓힌다          → 다음 날 또 ①
```

**질문 패턴 5가지 (셸 템플릿, 보고 싶은 것을 넣어 각각 실행):**

```bash
URL=http://127.0.0.1:18080
q() { curl -s -X POST "$URL/run-plans" -H 'Content-Type: application/json' \
       -d "{\"plan\":\"$1\",\"rag\":\"store\"}"; }

# 1) concept explanation
q "[Task 1: explain]  Explain what a vector field is, definition and intuition"
# 2) solve an example from the book
q "[Task 1: explain]  Work through the example (Worked Problem) on pages 1159-1165 the way the book does"
# 3) a slightly-varied example
q "[Task 1: problems]  Make one similar example by changing only the numbers/conditions of the example in the source, then solve it the book's way"
# 4) concept check problem
q "[Task 1: problems]  From the source, make a problem that checks this concept"
# 5) a slightly-varied exercise
q "[Task 1: problems]  Make one variant exercise by changing only the numbers of an exercise in the source, then solve it"
```

> 모두 **책 store를 근거**로 논리적으로 답하고(`rag:"store"`), 각 답에 출처(페이지)가 붙습니다.
> `explain`=설명, `problems`=문제 만들기+풀이. `rag`를 빼면 책 근거를 안 쓰므로 **반드시 유지**.

## 2. 전체 그림 (배경 이해용)

```
  ╔══════════ WSL/로컬 ══════════╗
  ║  책 PDF ─ upload-books.sh ─▶ 서버 books/  ║
  ╚═══════════════════════════════╝
                ▼
  ╔════════════════════ 서버 ════════════════════╗
  ║  [A] study: books/ → accumulate(범위 누적) → store(RAG 청크)  ║
  ║  [B] myllm : 로컬 LLM 서버들 띄움 (모델)                    ║
  ║  [C] agent : 18080 ← store 근거로 질문에 답 (로컬 LLM 호출)   ║
  ╚══════════════════════════════════════════════╝
```

- **누적 = 멱등**: 이미 넣은 범위는 자동 skip → 매일 조금씩 앞으로 나아가면 store가 커짐.
- "토픽"은 선택 관리용 태그일 뿐, **질문할 때는 꼭 필요하지 않다.** (아래 참고)

---

## 3. 서버에서 하는 일 (단계별)

### 0) 최초 1회 — 책 등록 & 서버 기동

| 스크립트 | 역할 | 실행 위치 |
|---|---|---|
| `upload-books.sh` | 교재 PDF를 서버로 업로드 | 로컬(WSL) |
| `server-up.sh` | agent API 도커 기동/빌드 | 서버 |
| `server-down.sh` | agent API 내리기(데이터 보존) | 서버 |

- 업로드: `./upload-books.sh "user@서버IP" 'C:\Users\...\수학' ~/study/books/math`
- 책 1번 등록(그 후 매일 ①로 페이지만 추가):
  ```bash
  bash docker/run.sh books add --id calc --title "Calculus" --subject math \
      --source /books/math/calc.pdf
  ```
- 서버 기동(agent 질문용): `bash server-up.sh` → `http://localhost:18080`
- 임베딩(bge-m3)은 `HF_ENDPOINT=https://hf-mirror.com`(미러), 수식은
  `DOCLING_FORMULAS=1 DOCLING_FORMULA_FP32=1`.

> 실행 위치는 전부 **서버의 `~/projects/stemer/study`**. 로컬은 `.venv` 테스트만.

### ① 매일 — "오늘 공부할 범위만 store에 넣기" (accumulate)

교재를 펼쳐 오늘 페이지 범위를 정한다 → 한 줄 실행(기존 유지·누적, 멱등):

```bash
cd ~/projects/stemer/study

# 16-1 Vector Fields
DOCLING_FORMULAS=1 DOCLING_FORMULA_FP32=1 HF_ENDPOINT=https://hf-mirror.com \
  bash docker/run.sh accumulate --book calc --pages 1159-1165 --source /books/math/calc.pdf

# 16-2 Line Integrals
DOCLING_FORMULAS=1 DOCLING_FORMULA_FP32=1 HF_ENDPOINT=https://hf-mirror.com \
  bash docker/run.sh accumulate --book calc --pages 1166-1178 --source /books/math/calc.pdf
```

- 성공: `ingested calc: +N chunks` / `now covered intervals: [...]`.
- 중복: `skip ... already covered` → 다음 범위로 한 줄 더. **이미 넣은 건 다시 안 넣는다.**
- 이렇게 쌓인 게 **RAG store** = 질문의 근거가 된다. (지금 calc는 1159-1165 ✅, 1166-1178 진행 중)

### ② 매일 — 책 근거로 물어보기 (agent 질문)

방금 넣은 범위를 근거로 개념·예제·문제를 묻는다. **여기가 핵심**:

```bash
URL=http://127.0.0.1:18080
q() { curl -s -X POST "$URL/run-plans" -H 'Content-Type: application/json' \
       -d "{\"plan\":\"$1\",\"rag\":\"store\"}"; }

q "[Task 1: explain]  Explain what a vector field is: definition and intuition"
q "[Task 1: explain]  Work through the example in the source the way the book does"
q "[Task 1: problems]  Make a similar example by changing only the numbers, then solve it"
q "[Task 1: explain]  From the source, make and answer one question that checks this concept"
q "[Task 1: problems]  Make a variant exercise by changing only the numbers of an exercise, then solve it"
```

- agent가 커져 있어야 함(처음에 `server-up.sh`). 미리 켜져 있으면 그냥 실행.
- **`rag:"store"` 필수** (책 근거). 답에 출처 페이지가 붙으면 정상 recall.
- `explain`=설명 · `problems`=문제 만들기+풀이. 여러 Task를 한 번에 넣어도 됨(병렬):
  ```bash
  q "[Task 1: explain] concept explanation  [Task 2: problems] variant example"
  ```

### (선택) 토픽·노트 파일로 관리하고 싶다면

질문만으로 충분하지만, **섹션 단위로 정리된 노트/*.md 를 남기고 싶다면**:
```bash
DOCLING_FORMULAS=1 DOCLING_FORMULA_FP32=1 HF_ENDPOINT=https://hf-mirror.com \
  bash docker/run.sh topics discover --book calc   # store 섹션에서 토픽 자동 생성
bash docker/run.sh generate      --topic calc-16.1  # 개념/강의 노트 (책 근거)
bash docker/run.sh problembank --topic calc-16.1    # 연습문제 세트 (책 근거)
```
- `--kind`는 그 토픽의 산출물 형태: `exam`(종합) · `note`(개념만) · `problems`(연습문제).
  질문을 바로 하는 게 목적이면 **굳이 안 만들어도 된다.**

### 매일 체크 한 줄

> 새 페이지 범위 → `accumulate` →  (필요시 토픽) → 책 근거로 질문(개념·예제·변형·문제·변형문제).

> ⚠️ 이미 넣은 범위는 다시 안 한다. 하루 한 조각씩 앞으로만 → store가 커진다.

---

## 4. 프로젝트 개요 — "stem"(STEM 교재 학습 + 멀티에이전트)

이 저장소는 **"공부할 교재 덩어리를 RAG 저장소로 만들고, 로컬에서 여러 AI 역할이
문제/설명/증명/코드를 병렬 생성·스스로 판정"**하는 시스템이다.

- **CPU/로컬 중심**: 큰 모델(14B)은 **판정/검증만**(출력 토큰 억제, 결정적), 작은 모델(7B)은
  **생성/분석** 담당(프롬프트로 품질 보정). 출력은 전부 JSON/GBNF로 구조화. (NEW-METHOD.md)
- **계약 우선 TDD**: 각 모듈 공개 동작을 테스트(`test_*_contract.py`)가 먼저 고정.
- **프롬프트 ≠ 코드**: 텍스트는 `agent/prompts/config.yaml`에 분리.

| 파트 | 디렉터리 | 하는 일 |
|---|---|---|
| **교재 RAG 공장** | `study/` (`study_lib/`) | PDF → 마크다운 → 청크 → 임베스트 → 검색 store |
| **멀티에이전트 오케스트레이터** | `agent/` | 근거 청크 → 병렬 LLM 역할 → 판정 → 노트 생성 |
| (레거시) | `brain/` | 옛 마이크로서비스 계약 예시 (agent로 대체 예정, 보존) |
| 배포/도구 | `docker/`, `server-up.sh` 등 | 컨테이너·업서트 스크립트 |

---

## 5. 모듈 목록

### 4.1 `study/` — 교재 RAG 공장 (`study_lib`)

| 모듈 | 역할 |
|---|---|
| `registry.py` | 책/토픽 레지스트리, 상태 `todo→draft→review→done`, 페이지 interval 누적 |
| `parse.py` | TXT/PDF → 마크다운 (text/fast/docling 프로파일) |
| `chunk.py` | 마크다운 → 300~500토큰 청크 (헤딩 섹션 경계) |
| `store.py` | 임베스트된 청크 영속화(JsonSink/PgSink) + dense/text 검색 |
| `embed.py` | 임베딩 인터페이스(StubEmbedder / TransformerEmbedder-bge-m3) |
| `retrieve.py` | 토픽 → 컨텍스트: 섹션 매핑(primary) + RRF 융합·리랭크(crossref) |
| `factory.py` / `generate_free.py` | generate_one: retrieve→prompt→LLM→검증→patch→render→저장 |
| `problembank.py` | 연습문제 배치 생성(10+10) |
| `protocol.py` / `render.py` / `lint.py` | 생성 출력 100% JSON 규약 / 최종 md / KaTeX 린트 |
| `ingest.py` | 배치+한권 인제스트, `--jobs N` 병렬, 재개/실패 관리 |
| `discover.py` | 인덱스 섹션 → 자동 todo 토픽 (`topics discover`) |
| `figures.py` | 교재 원본 그림 관리·첨부 |
| `bookmarks.py` / `pages.py` | 북마크·범위 / 페이지 보정 |
| `profiles.py` / `tokens.py` | 청크 프로필(compact/long) / 토큰 추정 |
| `llm.py` / `llm_local.py` | LLM 클라이언트 인터페이스·Usage·Flash(Local) |
| `cli.py` | 얇은 CLI 래퍼: books / topics / accumulate / index / ingest / generate / problembank / status / chapter / postkatex / pur |

### 4.2 `agent/` — 멀티에이전트 오케스트레이터 (NEW-METHOD 구현체)

| 모듈 | 역할 |
|---|---|
| `gateway.py` | LLM 서버 호출(OpenAI 호환 chat+embed), `thinking…/response` 제거, JSON 추출 |
| `registry.py` | 역할↔서버 주소 매핑 + `ServerPool`(다중 서버 순환) |
| `planner.py` | 계획서 → Task 티켓화 (로컬 split_plan / parser 서버 정규화) |
| `rag.py` | 근거 검색: `KeywordRetriever`(결정적) / `StudyStoreRetriever`(study store) / `CodeIndex`(뼈대) |
| `prompts.py` | 프롬프트 조립(동률 원본 = `prompts/config.yaml`), 근거 인용, 병합 |
| `grounding.py` | Tier-1 무료 게이트(빈 답·완전 drift 차단) |
| `symrun.py` | 출제자의 ```sympy``` 블록을 sandbox exec로 실행해 손계산 드리프트 차단 |
| `verify.py` | Tier-2 LLM 판사(생산자와 다른 역할)로 참/거짓·오류·예외 평결 JSON |
| `orchestrator.py` | 병렬(ThreadPool)·merge·run_plan, 문제 re-trail 루프, `UNGROUNDED` 플래그 |
| `cli.py` | run / split / roles |
| `api.py` | FastAPI 웹: `POST /run-plans` `/split-plans`, `GET /health` |
| `config/` | `agent.yaml(.example)` — 역할 주소 설정(파일 없으면 env/기본) |
| `prompts/config.yaml` | 실제 프롬프트 텍스트 단일 원본 |

역할 → 서버 매핑(기본):

| 역할(role) | 포트 | 모델 관련 |
|---|---|---|
| parser | 8081 | Qwen2.5-7B (티켓 정규화) |
| worker ×4 | 8082-8085 | Phi-3.5-mini 계열 (설명/요약) |
| coder ×4 | 8086/8087/8089/8090 | qwen2.5-coder-7b |
| reasoner | 8088 | DeepSeek-R1-Distill-Qwen-7B |
| setter / judge | 8091 / 8092 | (문제생성 14B / 판사 14B — 선택, env로 활성) |
| embed | 11434 | Ollama `qwen2.5:3b` |

`agent/requirements-api.txt`(웹 전용: fastapi/uvicorn/pydantic/httpx)로 가볍다.

### 4.3 지지/도구

| 항목 | 용도 |
|---|---|
| `brain/` | (보존) 이전 마이크로서비스 계약 예시 — 제거 금지, agent로 대체 예정 |
| `docker/` `docker-compose.agent.yml` | agent 게이트웨이 이미지·운영 방식(`network_mode: host`) |
| `study/docker/` `study/docker-compose.yml` | study RAG 팩토리 이미지(`build.sh EMBED=1`, `run.sh`) |
| `server-up.sh` / `server-down.sh` / `upload-books.sh` | 서버 운영 / 교재 업로드 |
| `settings.yaml` | agent API · 서비스 브리지 주소 설정 예시 |
| `NEW-METHOD.md` | 역할별 모델 배치(출력 토큰/GBNF) 원칙 |
| `ROUTINE.md` | 매일 하루 작업 요약(본 문서 근본) |
| `NEW-STRATEGY.md` / `GEN-PROTOCOL.md` / `EXECUTION-PLAN.md` | study 설계 / 생성 출력 규약 / 실행 계획 |
| `PROMPT-PROTOCOL.md` | 프롬프트 데이터/코드 분리 원칙 |
| `study-archive-20260906/` | (보존) 이전 study 구현 보관 |

---

## 6. 자주 쓰는 명령어

### study(서버, `~/projects/stemer/study`)
```bash
bash docker/run.sh status                          # 전역 상태
bash docker/run.sh accumulate --book X --pages A-B --source /books/math/X.pdf [--embedder stub]
bash docker/run.sh topics add --book X --title "11-1 .." --section 11.1 --kind exam
bash docker/run.sh topics list --book X [--status todo|draft|review|done]
bash docker/run.sh topics set <topic> --status done      # 완료 표시
bash docker/run.sh generate --topic X-11-1 | --book X     # 개별/일괄 개념노트
bash docker/run.sh problembank --topic X-11-1             # 연습문제 세트
bash docker/run.sh index book.md --book X --profile text --embedder stub
bash docker/run.sh ingest books/math --subject math --profile fast --jobs 4
bash docker/run.sh topics discover --book X
```

### agent API
```bash
bash server-up.sh                    # 서버에서 → http://localhost:18080
curl localhost:18080/health          # myllm(모델기동) 구성·자동부팅 상태 포함
curl -X POST localhost:18080/run-plans -H 'Content-Type: application/json' \
  -d '{"plan":"[Task 1: explain] ..질문..","rag":"store"}'
curl -X POST localhost:18080/split-plans -H 'Content-Type: application/json' -d '{"plan":".."}'
bash server-down.sh
```

### 로컬 테스트(모델 없이)
```bash
python -m pytest agent/tests -q           # agent 40+ 테스트 (web deps 없으면 api skip)
study/.venv/bin/python -m pytest agent/tests -q   # api 포함 전부
python3 -m pytest study -q                # study contract 테스트
python -m agent.cli split plan.md          # 티켓 분해만
python -m agent.cli roles                  # 역할/서버 확인
python -m agent.cli run plan.md --live --rag store --notes notes  # 실제 서버 사용
```

---

## 7. 주의사항 & 팁

- **누적 = 멱등**: `accumulate`는 이미 커버한 page interval을 건너뛰지만, 부분 중복은
  새 페이지만 추가. "안 지워지고 누적"이 기본 설계.
- **agent는 근거를 꼭**: 질문할 때 `"rag":"store"`를 붙이지 않으면 store를 안 씀.
- **모델 선택 → judge 8092 / setter 8091**: `config/agent.yaml`에 남기면 활성. 없으면
  problems 경로는 worker, 판사는 reasoner가 맡는다.
- 원격 접속 시 LLM 포트만 ssh 터널로 묶으면 된다(`ssh -L`).
- `upload-books.env`·`settings.yaml`·`config/agent.yaml`은 설정 정보 — 보안에 주의.