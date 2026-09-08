# agent — 로컬 병렬 멀티에이전트 오케스트레이터 + RAG (NEW-METHOD 구현)

**myllm 은 "서버 띄우기" 전용**으로 두고, 이 패키지가 **오케스트레이터(**NEW-METHOD
§5/6**)** 와 **RAG 근거 검색(**§4**)** 을 한 몸(짬뽕)으로 담는다. 실제 추론은
myllm 이 띄운 `llama.cpp llama-server`(OpenAI 호환)들 — parser/worker/coder/reasoner가
각자 포트(8081~8088)로 대기 — 에서 일어난다.

## 실행 중인 모델 → 이 패키지 role 매핑

| myllm slug | 포트 | 이 패키지 role | model |
|---|---|---|---|
| parser     | 8081 | `parser`    | Qwen2.5-7B (티켓 정규화) |
| worker1-4  | 8082-8085 | `worker` (×4 pool) | Phi-3.5-mini |
| coder1-2   | 8086-8087 | `coder` (×2 pool) | qwen2.5-coder-7b |
| reasoner   | 8088 | `reasoner`  | DeepSeek-R1-Distill-Qwen-7B |
| (Ollama)   | 11434 | `embed`     | qwen2.5:3b (llama-server 는 `--embeddings` 없음) |

config 우선순위: `agent/config/agent.yaml` → env(`AGENT_PARSER` 등) → 기본값.
기본값은 위 표와 일치하므로 로컬 데모는 파일 없이도 동작.

## 흐름 (§5.2)

```
계획서(markdown) ─ parser(Qwen2.5-7B) or 로컬 split ─▶ Task[]
   Task ─ RAG 근거(r.rag.py) 조회 ─▶ role 서버(ServerPool 라운드) 
         ─ prompts.py(근거만 주입·환각 차단) ─▶ chat ─▶ WorkerResult
   여러 Task 를 ThreadPool 로 병렬 실행 ─▶ merge → notes/*.md
```

## 사용법

```bash
# 1) 모델 없이 티켓 분해 확인
python -m agent.cli split plan.md
# 2) 역할/서버 확인
python -m agent.cli roles

# 3) 로컬 DEMO — 서버 안 부르고 목(echo)으로 파이프라인만 (오프라인 테스트)
python -m agent.cli run plan.md --notes out

# 4) 실제 서버(192.99.201.121)에 배치되어 LLM 최종 사용
python -m agent.cli run plan.md --live --parser-live --notes notes
#  근거까지 붙이려면 (study store 가 놓인 경로에서):
python -m agent.cli run plan.md --live --rag book-store --store study/data/store \
       --notes notes
```

### 원격 서버에서 이 패키지를 쓰려면(ssh 터널)
```bash
ssh -N -L 8081:127.0.0.1:8081 -L 8088:127.0.0.1:8088 ubuntu@192.99.201.121
# 여러 포트: 8081..8088 전부 −L
```

## 테스트
```bash
cd /home/rlawjddn/projects/stemer
python -m pytest agent/tests -q      # 40 tests (네트워크/서버 없이 통과)
```
계약(fake transport/retriever) 테스트라 실제 llama/ollama 를 띄우지 않아도 되고,
`test_*_match_myllm` 이 실서버 myllm 포트 매핑이 어긋나면 실패시켜 회귀를 막는다.

## 아키텍처
```
agent/
  gateway.py  Gateway(OpenAI호환 chat+json), OllamaEmbed, strip_thinking(>
              <think> 제거)      — 네트워크는 이 파일에서만
  registry.py  Registry(build_role/overrides/env), ServerPool(다중서버 순환)
  planner.py   PlanParser(parser 서버) + split_plan·_classify(로컬 결정적 티켓화)
  rag.py       KeywordRetriever / StudyStoreRetriever(study_lib store) / CodeIndex(§4.2 뼈대)
  prompts.py   역할별 시스템 + 근거 인용(ctx block) + merge_results(markdown)
  orchestrator.py  Orchestrator(run_plan/run_tasks, ThreadPool 병렬, WorkerResult)
  cli.py       run/split/roles
  config/agent.yaml(.example)   ...
  tests/       contract-style (fake transport/retriever)
```

## 남은 후속(선택)
- `rag.StudyStoreRetriever` 로 실제 study store 연동 확정 (서버 `/data` 볼륨 경로).
- `rag.CodeIndex` 실제 AST(tree-sitter) 인덱서 + 호출그래프(SQLite) = NEW-METHOD §4.2.
- FastAPI Watchdog(`gate` 엔드포인트)로 위 cli 를 HTTP 화하여 "입력→결과 반환" 구성.
