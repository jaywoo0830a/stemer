# POST /run-plans 내부 동작

> 대상 코드: `agent/` 패키지 (오케스트레이터). 서버에서 실행되는
> `/run-plans` 에이전트 요청 하나가 어떤 단계로 실제 로컬 LLM 들을 거쳐
> 결과 파일까지 만들어지는지 정리했다.

```bash
curl -X POST localhost:8000/run-plans -H 'Content-Type: application/json' \
  -d '{"plan":"[Task 1: explain] why integral of sin(wx) zero symmetric"}'
```

전체 단계: **① 라우팅 → ② 모델/게이트웨이 구성 → ③ 티켓 분해 → ④ RAG 근거 →
⑤ (병렬) 역할 서버 호출 → ⑥ 취합·markdown 저장 → ⑦ JSON 반환**

---

## ① HTTP 진입 — FastAPI(`agent/api.py`)

- Uvicorn(도커 `AGENT_MODE=live`)이 `POST /run-plans` 를 받는다.
- `RunPlanRequest` 모델로 body 를 검증한다.
  ```json
  { "plan": "...", "note_stem": null, "rag": null, "use_parser": true }
  ```
  - `plan` 은 반드시 1 글자 이상 (아니면 HTTP **422**).
- `create_app()`에서 잡은 **mode = live** (env `AGENT_MODE`). live 는 실제 서버 호출,
  mock 은 오프라인 echo.
- 핸들러는 블로킹 요청이 이벤트 루프를 막지 않게 `loop.run_in_executor(None, _run_sync, ...)`
  로 작업을 스레드 풀에 넘긴다(이후 단계는 그 워커 스레드에서 실행).

## ② 구성 — role→서버 주소 (`agent/registry.py`)

`build_orchestrator()` 가 새 `Orchestrator` 를 만든다. 여기서 **Registry** 가
역할(role)을 추론 서버 주소로 매핑한다. myllm 이 호스트에 띄운 llama-server 들:
`network_mode: host`라 컨테이너 안 `127.0.0.1:8081…` 이 곧 호스트 LLM.

| role | 주소 | 실제 모델 |
|---|---|---|
| parser | `127.0.0.1:8081` | Qwen2.5-7B (티켓 정규화) |
| worker ×4 | `127.0.0.1:8082–8085` | Phi-3.5-mini (병렬 논리 일꾼) |
| coder ×2 | `127.0.0.1:8086–8087` | qwen2.5-coder-7b |
| reasoner | `127.0.0.1:8088` | DeepSeek-R1-Distill-Qwen-7B |
| embed | `127.0.0.1:11434` (Ollama) | qwen2.5:3b (벡터 임베딩만) |

- 주소는 `agent/config/agent.yaml` 또는 env `AGENT_PARSER/WORKERS/…`로 덮어쓸 수 있다.
- 이 단계에서 **네트워크는 아직 안 보냄**. 주소 맵만 준비하는 것.

## ③ 티켓 분해 — parser (또는 로컬 `split_plan`, `agent/planner.py`)

`use_parser=true`이므로 **parser 서버(8081)로 티켓 정규화**를 시도한다.
`PlanParser.parse(plan)`:

1. 시스템 프롬프트에 JSON 스키마 힌트를 넣고 parser 서버 게이트웨이를 호출:
   ```json
   {"tasks":[{"id":int,"action":str,"target":str,"input":str,"role":"worker|coder|reasoner"}]}
   ```
2. 응답은 JSON 이므로 `_find_json_object` 로 온전한 `{…}` 만 뽑는다.
   (R1 류 reasoning 모델이 콘텐츠에 `<think>`를 섞으면 `strip_thinking` 이 먼저 제거.)
3. `task_from_dict` 가 `action`→기본 role 을 붙인다 (예 `fix→coder`, `proof→reasoner`, `explain→worker`).
4. **parser 가 죽어 있거나 JSON 이 아니면** 로컬 `split_plan` 으로 폴백
   → `[Task N: …]` 블록을 regex 로 자르고 키워드로 role 배정.

이 예시에서는 한 줄짜리 `[Task 1: explain]` 이라 최종적으로
`Task(id=1, action=explain, role=worker)` 하나가 만들어진다.

## ④ RAG 근거 조회 — (요청에 `rag`가 없으면 skip, `agent/rag.py`)

- 예시는 `"rag": null` 이므로 **근거 컨텍스트가 없는 상태**로 진행.
- `rag: "book-store"` 라면 `StudyStoreRetriever` 가
  - Ollama(11434)로 query 를 벡터화(`/api/embed`)하고,
  - `store.search_dense`(코사인) + BM25 어휘 검색으로 가까운 청크 몇 개 반환.
- 근거가 있을 때만 그 텍스트를 모델 프롬프트에 주입한다(환각 차단).
- 이 예시는 skip 되어 아래 `(no reference context …)` 프롬프트를 받는다.

## ⑤ 역할 서버 선택 — `ServerPool` 라운드 로빈

- `Task.role == "worker"` → `ServerPool.next("worker")` 가 worker 서버 **하나**를 고른다.
  - `worker`는 4대라 매 요청·Task 마다 8082→8083→8084→8085→… 로 **라운드 로빈** 분산(부하 분산).
  - `parser/reasoner` 는 단일이라 그냥 자기 포트.
- 각 에이전트는 **독립 스레드**에서 실행되므로 여러 Task 가 있으면 `ThreadPoolExecutor`
  로 **동시에** 서로 다른 llama-server를 병렬 호출한다(NEW-METHOD §6 병렬 실행).

## ⑥ 실제 LLM 호출 — `Gateway.chat` (`agent/gateway.py` → llama-server)

선택된 worker 주소(예 8082)를 가리키는 `Gateway(base_url=…)`가 OpenAI 호환
`POST /v1/chat/completions` 를 보낸다. (`agent/prompts.py` 가 만든 프롬프트)

- **system** — role 설명 + “답은 아래 근거 청크로만, 없으면 지어내지 말라” (환각 방어)
- **user** —
  ```
  QUESTION / TICKET INPUT:
  why integral of sin(wx) zero symmetric?

  REFERENCE CONTEXT (grounding only):
  (no reference context found — do not fabricate; note this to the planner)
  ```
- llama.cpp 가 이 요청을 CPU 로 추론해 `{"choices":[{"message":{"content":"…"}}]}`
  형태로 응답. `Gateway.chat` 이 `content`만 떼고 `<think>` 태그를 벗긴 문자열을 반환.

이 예시는 `use_parser`에 따라 다르지만, 본문만 보면 worker(8082) 하나가
“적분 대칭 구간에서 0” 근거가 없음을 밝히고 답변을 시도한다.

## ⑦ 취합 + markdown 저장 (`agent/orchestrator.py`, `agent/prompts.merge_results`)

- 각 `WorkerResult`(task/role/url/output/error)를 모은다.
  - `error`가 있으면 그 Task 는 `ok=False`, 로그에 사유. 개별 실패가 전체 요청을 죽이진 않음.
- `merge_results()` 가 각 worker 결과를 `## Task {id} · {role}` 블록으로 묶어 하나의
  조직화된 마크다운을 만든다.
- 파일명은 `agent-<UTC:YYYYMMDD-HHMMSS>.md` (env `AGENT_NOTES_DIR`). 도커에선
  `/app/notes` = 호스트 `./agent-notes/` bind → 서버에 영속된다.

## ⑧ JSON 응답 (`agent/api.py._to_response`)

```json
{
  "stem": "agent-20260908-…",
  "note_path": "/app/notes/agent-20260908-….md",
  "total": 1, "ok_count": 1,
  "tasks": [ { "task":1,"role":"worker","url":"http://127.0.0.1:8082",
               "ok":true,"output":"…","error":null,"sources":[] } ],
  "markdown": "## Task 1 · worker\n\n…",
  "mode": "live"
}
```
- `markdown`은 위에서 저장한 파일 내용 그대로(편의성). 응답만으로도 결과 소비 가능.

---

## 흐름 요약 (다이어그램)

```mermaid
flowchart LR
  C[curl POST /run-plans] --> F{{FastAPI agent/api}}
  F --> R[Registry: role→주소 매핑]
  F --> P{use_parser?}
  P -- yes --> PARSER[[parser 8081 Qwen2.5-7B]] --> TASK[Task list]
  P -- no/fail --> SPLIT[split_plan (로컬 결정적)]
  TASK --> RAG{rag 지정?}
  RAG -- yes --> EMB[[Ollama 11434 embed]] --> STORE[(study store)] --> CTX[근거 chunks]
  RAG -- no --> CTX
  TASK --> POOL[ServerPool 라운드] --> GW((Gateway /v1/chat/completions))
  CTX --> GW
  GW -- worker/coder/reasoner --> LLMS[[llama.cpp 8081-8088]]
  LLMS --> RES[WorkerResult]
  RES --> MERGE[merge_results → markdown]
  MERGE --> SAVE[(notes/*.md)]  --> JSON[(JSON 응답+note_path)]
```

## 병렬/‘로컬’ 원칙이 지켜지는 지점
- **추론은 전부 외부 API 아님** — 로컬 llama.cpp/llama-server(Ollama는 임베딩만).
- **근거가 없으면** 각 시스템 프롬프트가 “없는 지식 창작 금지” 경고 → 환각 차단 (§NEW-METHOD 핵심).
- **여러 Task**는 서로 다른 llama 인스턴스(멀티 worker)에 병렬로 분산돼 CPU 8/16 스레드를 분담.
- **단일 worker 질문**이면 이 흐름이 worker 한 대를 쓰고 끝 — 부하가 과하지 않게 라운드 배분.
