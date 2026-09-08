```markdown
# 로컬 병렬 멀티 에이전트 스터디/개발 서버 구성안

> **목표:** 8코어 16스레드 CPU + DDR5 64GB RAM + 1TB SSD를 활용하여,  
> 외부 API 없이(비용 0원) 이공계 학습과 코딩을 보조하는 **초고속·저비용·저환각** 로컬 AI 시스템 구축.

---

## 1. 시스템 개요

- **사용자 역할:** 직접 문제를 분석하고, 작업 지시서(Ticket)를 작성하는 **플래너(Planner)**.
- **AI 역할:** 지시서를 수행하는 **논리적 일꾼(Worker)들**. 계획 능력은 불필요.
- **데이터 흐름:** 사용자 → 계획서 입력 → 서버가 분해 → 병렬 에이전트가 RAG 검색 후 실행 → 결과 취합 → 사용자 반환.
- **핵심 원칙:**
  - 모든 모델은 **필요할 때만 메모리에 로드/언로드** (On-Demand).
  - 답변은 **로컬 DB(RAG)에 근거한 내용만** 생성 (환각 차단).
  - 출력은 **GBNF 문법으로 구조화**하여 토큰 낭비 제거.

---

## 2. 하드웨어 및 OS 구성

| 구성 요소 | 권장 사항 | 비고 |
|-----------|------------|------|
| OS | Ubuntu Server 22.04/24.04 LTS (Headless) | 리소스 절약, Docker 친화적 |
| 컨테이너 | Docker + Docker Compose | 서비스 관리 용이 |
| 상시 프로세스 | FastAPI 서버 (Watchdog) | 질문 감지, 모델 로드/언로드 제어 |
| 경량 DB | SQLite / Redis | 캐시, 대화 기록 저장 |
| 디스크 | 1TB NVMe SSD | RAG 원본 문서, 코드, 캐시 영구 저장 |
| 메모리 전략 | 모델은 필요 시 로드, 평소에는 페이지 캐시로 활용 | RAM 최대 활용 |

---

## 3. 모델 레지스트리 (64GB RAM, On-Demand)

| 역할 | 모델 | 크기 | 용도 |
|------|------|------|------|
| **작업 분배 Parser** | Qwen2.5-7B-Instruct (Q4_K_M) | ~5GB | 사용자 계획서를 JSON 티켓으로 변환 |
| **논리 일꾼 (Worker)** | Phi-3.5-mini-instruct (3.8B) × 3~4 | 각 ~3GB | 수식 분석, 코드 버그 추적, 요약 |
| **코딩 일꾼** | Qwen2.5-Coder-7B-Instruct (Q4_K_M) × 2 | 각 ~5GB | 코드 수정, 생성, 리뷰 |
| **심화 추론가** | DeepSeek-R1-Distill-Qwen-7B | ~5GB | 수학적 증명, 복잡한 논리 검증 (필요 시 로드) |
| **임베딩 모델** | bge-small-en-v1.5 | ~0.5GB | 질문/문서 벡터화 (상시 대기) |

> **동시 로딩 전략:**  
> - 평상시: Parser 1 + Worker 2 (약 13GB)  
> - 딥 다이브 시: Worker 3 + Coder 2 + Reasoner 1 (약 25GB)  
> - 남는 RAM은 전부 리눅스 페이지 캐시로 활용.

---

## 4. 저장소 및 인덱싱 (1TB SSD + 64GB RAM)

### 4.1 텍스트 RAG (이공계 자료)
- **원본:** PDF, 마크다운, 논문 → `~/study/` 폴더.
- **처리:** 
  1. PyMuPDF 등으로 텍스트 추출
  2. 300~500 토큰 단위 청킹
  3. `bge-small` 임베딩 → **FAISS** (RAM 상주) & **ChromaDB** (SSD 영구 저장)
- **부가:** 각 청크의 요약본을 LLM이 미리 생성하여 Summary DB에 저장 (검색 속도 향상)

### 4.2 코드 인덱싱
- **대상:** 개인 코드, 라이브러리 소스(NumPy, PyQt 등)
- **방법:**
  - **AST (tree-sitter)** 로 함수/클래스 단위 분할
  - 각 코드 블록을 **CodeBERT**로 임베딩 → 의미 검색용 FAISS
  - 각 함수에 LLM이 생성한 **자연어 설명(Docstring)** 을 별도 임베딩 → 텍스트 검색용 FAISS
  - 함수 호출 관계를 **그래프 DB(SQLite)** 에 저장 → 영향 분석
- **저장:** 인덱스는 RAM, 원본은 SSD.

### 4.3 캐시
- 외부 API 호출 결과(과거 Q&A)를 임베딩과 함께 Redis에 저장 → 동일/유사 질문 시 재호출 방지.

---

## 5. 사용자 상호작용 및 오케스트레이션

### 5.1 계획서 입력 포맷 (마크다운)
사용자는 문제를 직접 분해하여 다음과 같이 입력:

```text
[Task 1: Bug Analysis]
File: heat_solver.py, function `explicit_euler_step`
Analyze the cause of divergence. Focus on boundary indexing.

[Task 2: Code Retrieval]
Search local codebase for implicit Euler implementation.

[Task 3: Fix Proposal]
Provide minimal patch for the divergence.
```

### 5.2 서버 처리 흐름
1. **Watchdog**이 입력 감지 → Parser(Qwen-7B) 로드.
2. Parser가 텍스트를 JSON 티켓으로 변환 (`{"task_id":1, "action":"analyze", "target":"heat_solver.py", ...}`).
3. 필요 모델들(Worker, Coder)을 병렬로 로드.
4. 각 에이전트는 RAG 검색(FAISS) 후 결과 생성.
5. 결과를 합쳐 사용자에게 반환.
6. 5분간 입력 없으면 모든 모델 언로드.

### 5.3 GBNF 문법 활용
- Parser 출력: JSON 스키마 강제 (`{"tasks": [{"id": int, "description": str}]}`).
- Worker 출력: `{"summary": str, "source": str}` 형식으로 제한.
- 이로써 출력 토큰 70% 이상 절감, 파싱 오류 제거.

---

## 6. 병렬 실행 아키텍처

- **CPU 스레드 할당 예시 (딥 다이브 모드):**
  - Worker A: 5스레드
  - Worker B: 5스레드
  - Coder C: 6스레드
- **메모리 할당:** 모델별 Q4_K_M 양자화로 각각 3~5GB, 총 20GB 내외 사용.
- **통신:** 각 에이전트는 독립적 컨텍스트 + RAG 검색 결과만 공유. 중간 결과는 파일이나 메모리 큐로 교환.
- **동시성:** FastAPI의 `asyncio` 또는 `multiprocessing`으로 구현.

---

## 7. 워크플로우 예시

### 7.1 수학 문제 풀이 중 막혔을 때

1. **사용자 티켓 작성 (영어):**
   ```text
   [Concept] Explain why integral of sin(wx) over symmetric interval is zero.
   [Proof] Show step-by-step using orthogonality of Fourier basis.
   ```
2. 서버가 교과서 PDF에서 관련 챕터 검색.
3. Worker A: 개념 설명 / Worker B: 증명 작성.
4. 결과: 출처와 함께 답변 반환.
5. 그래도 이해 안 되면 `[Deep Dive]`로 추론 모델 호출.

### 7.2 코딩 디버깅

1. 사용자 티켓:
   ```text
   [Bug] heat_solver.py line 23 IndexError. Explain cause.
   [Fix] Provide corrected boundary handling code.
   ```
2. Parser가 작업 분배.
3. Worker A: 버그 원인 분석 (AST + RAG).  
   Worker B: 코드베이스에서 유사 구현 검색.  
   Coder C: 수정 코드 생성.
4. 사용자 적용 후 에러 나면 에러 메시지와 함께 재요청.

---

## 8. 자동화 및 유지보수 (매일 새벽)

- **Crontab 스케줄 (새벽 3시):**
  - `~/study/today/` 폴더의 신규 파일 스캔.
  - 로컬 LLM이 요약 및 임베딩 생성 → RAG DB 업데이트.
  - 오래된 캐시 정리, 인덱스 재구축.
- **전력 절약:** 평상시 모든 모델 언로드, Watchdog만 대기 (RAM 50MB, CPU 0.1%).

---

## 9. 속도 및 최적화 팁

- **프롬프트 캐싱:** 시스템 프롬프트와 GBNF 스키마는 고정이므로 `llama.cpp`의 `--prompt-cache` 사용.
- **컨텍스트 길이 제한:** RAG에서 검색된 청크만 주입, 최대 4096 토큰으로 유지.
- **모델 양자화:** Q4_K_M 또는 Q5_K_M 사용. 속도와 품질 균형.
- **인메모리 DB:** FAISS는 RAM에 올려 디스크 I/O 제거.
- **영어 프롬프트:** 모델 성능 극대화.

---

## 10. 구축을 위한 To-Do (요약)

1. **환경 구축:** Docker, Ollama/llama.cpp 설치, Ubuntu 설정.
2. **모델 다운로드:** Phi-3.5, Qwen2.5-7B/Coder, DeepSeek-R1-Distill, bge-small.
3. **RAG 파이프라인 코딩:** 문서/코드 청킹, 임베딩, FAISS/ChromaDB 저장 스크립트 작성.
4. **오케스트레이터 개발:** FastAPI + Parser 로직 + 병렬 실행 코드.
5. **UI 연결:** 간단한 웹 UI (또는 터미널) 부착.
6. **자동화 스크립트:** 야간 업데이트, 모델 수명 관리.
7. **테스트:** 실제 학습/코딩 시나리오로 성능 확인.

---

> **결론:** 이 구성안은 사용자가 계획을 직접 세우고, 경량이지만 논리적인 AI 모델들이 병렬로 문제를 해결하는 **인간 중심 멀티 에이전트 시스템**이다.  
> 외부 API 의존도를 90% 이상 줄이고, 모든 데이터와 연산을 로컬에서 처리하여 **비용, 속도, 개인정보 보호**를 모두 달성한다.
```