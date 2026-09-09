# stemer — STEM 교재 학습용 로컬 멀티에이전트 + RAG

공부할 교재(PDF)를 **RAG 저장소**로 만들고, 로컬 CPU에서 **여러 AI 역할**(설명·증명·코드·출제·판정)이
병렬로 문제/강의노트를 생성·검증·누적하는 시스템.

> 📘 운영/실행/모듈 안내: **[`OPERATION.md`](OPERATION.md)**
> — 매일 공부 누적 워크플로, 커맨드 모음, 전체 모듈 개요.

---

## 무엇을 하는가

```
교재 PDF → 청크 → 임베딩 → 검색 store (study)
   → 근거 청크를 병렬 LLM 역할로 전환 → 검증(판정) → 노트 / 문제 생성 (agent)
```

- **교재 RAG 공장** `study/` : PDF를 마크다운→청크→임베딩해 store에 누적(멱등), 토픽별 생성.
- **멀티에이전트 오케스트레이터** `agent/` : 근거 청크만 주입해 병렬 LLM(parser/worker/coder/reasoner)로
  답을 만들고, 2단 게이트(문법/어휘 drift + 다른 역할 LLM 판사 + sympy 수치 실행)로 검증.

## 구조

| 디렉터리 | 역할 |
|---|---|
| `study/` (`study_lib/`) | parse · chunk · embed · store · retrieve · factory · generate_free · problembank · cli |
| `agent/` | planner · rag · gateway · grounding · symrun · verify · orchestrator · api(FastAPI) |
| `docker/`, `server-up.sh`, `upload-books.sh` | 배포·운영 |
| `brain/` | (보존) 이전 마이크로서비스 계약 예시 |

## 빠른 시작 (서버)

```bash
# 1) study RAG 팩토리 (study/ 에서)
bash docker/run.sh accumulate --book 미적분 --pages 759-772 --source /books/math/미적분.pdf
bash docker/run.sh generate --topic 미적분-11-1

# 2) agent API (루트에서)
bash server-up.sh
curl localhost:8000/health
```

자세한 명령어·운영 순서·모듈 설명은 **`OPERATION.md`** 참고.

## 테스트 (모델 없이)

```bash
python -m pytest agent/tests -q     # agent 40+ (web deps 미설치 시 api skip)
python3 -m pytest study -q          # study contract 테스트
```