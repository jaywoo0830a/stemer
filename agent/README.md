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

## WEB API (FastAPI) — agent/api.py
동기 `POST /run-plans` 로 계획서 한 건 → 병렬 에이전트 → `notes/*.md` + JSON.
기본 mock(오프라인)이 아닌 **live 기본**이라 서버에서 띄우면 실제 llama/Ollama 를 부른다.

```bash
# 의존성 (웹)
study/.venv/bin/python -m pip install -r agent/requirements-api.txt

# 서버에서 실행 (실제 추론 서버) — live 기본
study/.venv/bin/python -m uvicorn agent.api:app --host 0.0.0.0 --port 8080

# 간단 사용
curl -X POST localhost:8080/run-plans -H 'Content-Type: application/json' \
  -d '{"plan":"[Task 1: explain] why integral of sin(wx) on symmetric interval is zero"}'
curl localhost:8080/health
```

로컬/오프라인 데모(서버 안 부를 때)는 `live=False` 로 앱을 만들어 `TestClient` 로 검증한다.

## Docker 로 웹 서버 띄우기 (권장 — 서버 실사용)
**myllm 이 띄운 llama/Ollama(8081-8088/11434)는 호스트 프로세스라서** 컨테이너는
`network_mode: host` 로 호스트의 그 포트들을 그대로 본다(study 컨테이너와 동일 관례).

```bash
bash server-up.sh      # 이미지 빌드(없으면)+ 기동 → http://localhost:8080
bash server-down.sh    # 정지/제거 (notes 는 ./agent-notes 에 유지)
```
- 기본 **live**(실제 추론). 오프라인 echo 데모는 `AGENT_MODE=mock bash server-up.sh`.
- 역할 주소 재정의: `AGENT_PARSER/WORKERS/CODERS/REASONER/EMBED`(CSV).
- 파일: `docker/agent-gateway.Dockerfile`, `docker-compose.agent.yml`,
  `server-up.sh`, `server-down.sh`, `.dockerignore`.
- 재빌드: `docker build -f docker/agent-gateway.Dockerfile -t agent-gateway:latest .`

## 테스트
```bash
cd /home/rlawjddn/projects/stemer
python -m pytest agent/tests -q      # 40 tests (네트워크/서버 없이 통과; test_api 는 web deps 없으면 skip)
study/.venv/bin/python -m pytest agent/tests -q   # fastapi 설치된 venv → api 계약 포함 전체
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
  prompts.py   역할별 system + 근거 인용(ctx block) + merge_results(markdown)
  grounding.py  Tier-1 무료 게이트 (모듈 일반 규칙: 빈 답/완전 drift 차단, 교정 프롬프트)
  symrun.py     [SYMPYMETHOD] 결정론 수치 게이트 — 출제자가 낸 ```sympy``` 블록을
                샌드박스 exec 로 실제 실행해, 손계산 드리프트(예: 45 vs 32)를 차단.
                실행 오류/코드 결과값과 Solution key 불일치 = 자동 거부. (sympy 필요)
  verify.py     Tier-2 LLM 판사 — 논리적 모델(기본 reasoner 8088; 생산자가 reasoner면 coder)
                로 답의 참/거짓·오류·예외를 구조화 JSON 평결. self-confirmation 방지.
  orchestrator.py  Orchestrator(run_plan/run_tasks, ThreadPool 병렬, WorkerResult,
                 겹레계정: Tier1→(판사)→재시도→소진 시 UNGROUNDED 플래그)
  cli.py       run/split/roles
  config/agent.yaml(.example)   ...
  tests/       contract-style (fake transport/retriever/judge)
```

## 남은 후속(선택)
- `rag.StudyStoreRetriever` 로 실제 study store 연동 확정 (서버 `/data` 볼륨 경로).
- `rag.CodeIndex` 실제 AST(tree-sitter) 인덱서 + 호출그래프(SQLite) = NEW-METHOD §4.2.
- FastAPI Watchdog(`gate` 엔드포인트)로 위 cli 를 HTTP 화하여 "입력→결과 반환" 구성.
