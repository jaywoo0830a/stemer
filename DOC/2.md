# MODEL-ANALYSIS — CPU-only 다중 모델 배치의 수학적 분석

> 타깃 하드웨어: **AMD Ryzen 9 9700X (8코어/16스레드, Zen5) · DDR5 64GB · CPU-only**
>
> 이 문서는 현재 myLLM 리포의 다중-모델 배치(PLAN.md / `server/scripts/*`)를 이상적 자원 배분 모델과 대조해
> *어디가 어떤 문제*인지를 **숫자로** 설명한다. 모든 수식은 KaTeX 문법이며 `README`에서 표시 가능하다.

---

## 1. CPU-only LLM 배치의 이상적 수학 모델

### 1.1 디코딩은 메모리 대역폭 결합 문제 (M1)

자기회귀 디코딩은 토큰마다 모델 **전체 가중치를 메모리에서 한 번 읽는다**.
CPU 연산은 충분히 빠르므로 병목은 **메모리 대역폭**이다.

$$
\tau_d \;=\; \frac{M_w}{B_{\text{mem}}}
\qquad\Rightarrow\qquad
\upsilon_{\text{dec}} \;=\; \frac{B_{\text{mem}}}{M_w}
$$

- `M_w` : 모델 가중치 상주 크기 (GGUF + 오버헤드)
- `B_mem` : 실효 메모리 대역폭 (DDR5-5600 2채널 이론 ~89 GB/s, 실효 ~45–55 GB/s)

| 모델 | M_w | 예상 `υ_dec` (B_mem ≈ 45GB/s) |
|---|---|---|
| 7B Q4_K_M | ~4.7 GB | **~7–9 tok/s** |
| 14B Q5_K_M | ~9.9 GB | **~4–5 tok/s** |

**핵심 정리 — 디코딩은 "동시 서버 수"가 아니라 "동시 생성 GGUF 종류"에 좌우된다.**

$$
\sum_i \upsilon_i \;\le\; \upsilon_{\text{tot}} \;=\; \frac{B_{\text{mem}}}{\overline{M}}
$$

1개 모델이 8 tok/s면 **동시 4개 서버는 각각 ~2 tok/s**로 총량은 그대로다.
→ 동시 프로세스 수를 늘려도 총 처리량은 늘지 않고 **레이턴시와 메모리만 는다.**
따라서 여러 인스턴스를 프로세스로 띄우는 대신 **한 프로세스의 `--parallel N` 슬롯**으로 멀티플렉싱해야 한다.

### 1.2 프리필은 연산 결합 문제 (M2)

프롬프트(in-context) 처리 속도는 8코어 + AVX‑512 기준:

$$
t_{\text{prefill}} \;=\; \frac{n_{\text{in}}}{\upsilon_{\text{pre}}}
\qquad
\upsilon_{\text{pre}}^{\text{7B}}\approx 150\text{–}300,\quad
\upsilon_{\text{pre}}^{\text{14B}}\approx 60\text{–}120\ \text{[tok/s]}
$$

입력이 큰 작업(판사·세터)은 **프리필 시간이 지배적**이다.

### 1.3 메모리 예산 방정식 / OOM 판별식 (M3)

$$
\sum_{r\in R} n_r\, R_r(\text{ctx}) \;\le\; 0.85 \times 64\,\text{GB}\;\approx\; 54\,\text{GB}
$$

각 서버의 상주 크기:

$$
R_r(\text{ctx}) \;=\; \underbrace{M_w}_{\text{GGUF}} \;+\; \underbrace{KV(\text{ctx})}_{\text{KV 캐시}} \;+\; \underbrace{O}_{\text{오버헤드}}
$$

KV 캐시는 컨텍스트 토큰당:

$$
KV \approx 2 \times n_{\text{layers}} \times n_{\text{kv\_heads}} \times d_{\text{head}} \times \text{dtype}
$$

| 모델 | KV/tok (FP16) | KV @ ctx 16K | q8_0이면 |
|---|---|---|---|
| Qwen2.5-7B (kv_heads=4) | ~56 KB | ~0.9 GB | ~0.45 GB |
| Qwen2.5-14B (kv_heads=8) | ~192 KB | ~3.0 GB | ~1.5 GB |

### 1.4 지연 예산(SLA) 검증식 (M4)

스테이지 실시간 지연:

$$
T_{\text{stage}} \;=\; \underbrace{\frac{n_{\text{in}}}{\upsilon_{\text{pre\_d}}}}_{\text{prefill}} + \underbrace{\frac{n_{\text{out}}}{\upsilon_{\text{dec}}}}_{\text{decode}}
$$

### 1.5 최적 배치 = 자원 할당 최적화 (M5)

$$
\begin{aligned}
\min_{\{n_r\}} \quad & \sum_{r} n_r\, R_r(\text{ctx}_r) \\[4pt]
\text{s.t.} \quad
  & \sum_{r} n_r\, t_r \;\le\; 16   & \text{(스레드 총량)} \\
  & \sum_{r} n_r\, R_r \;\le\; 54\,\text{GB} & \text{(메모리 총량)} \\
  & n_r \le |\mathcal{G}_r|\quad \text{exactly} \;=\; |\mathcal{G}_r| \text{ 종류 수} &
\end{aligned}
$$

> **최적 인스턴스 수 = "서로 다른 GGUF 파일 수"이며, 총 인스턴스 수가 아니다.**

**이 하드웨어의 수치 궁극값 (이상적 배치)**

| 프로세스 | GGUF | resident | threads | 역할 |
|---|---|---|---|---|
| A | Qwen2.5-7B-Instruct + `--parallel 4` | ~6 GB | 8 | parser + worker×4 |
| B | Qwen2.5-Coder-7B + `--parallel 2` | ~6 GB | 8 | coder×4 |
| C | DeepSeek-R1-7B (on-demand) | ~6 GB | 8 | reasoner |
| D | 14B Setter (D/E 상호배타) | ~12 GB | 8 | problem set |
| E | 14B Judge (D/E 상호배타) | ~12 GB | 8 | verify |
| F | bge-small (in-process) | ~1 GB | – | embedding |

피크 동시 상주 ≈ $6+6+1+12 \approx 25\,\text{GB}$
→ **64GB의 절반도 사용하지 않는다.** 토큰 레이트: 7B ≈ 7–9 tok/s, 14B ≈ 4–5 tok/s (물리적 상한).

---

## 2. 이상적 모델 대비 현재 시스템 문제점 (파일·라인 단위)

### 🔴 P1. `server/scripts/start_all.sh:13-25` — on-demand 원칙 위반 (최심각)

parser + worker1~4 + coder1~4 + reasoner **총 9개 동시 로드**:

$$
\text{메모리}:\; 9\times6\,\text{GB}\approx 54\,\text{GB}
\qquad
\text{스레드}:\; 9\times8 = 72 \;\gg\; 16
$$

- PLAN의 "40GB+ 여유"를 정면 위반하고 **OOM/스왑 직전** 수치.
- 72개 스레드를 16개에 과잉 할당 → 컨텍스트 스위칭 폭증으로 오히려 저하.

### 🔴 P2. `server/scripts/llama_serve_generic.sh:34-40` — 튜닝 플래그 미적용

`exec llama-server`에 전달되는 플래그는 `-m, --alias, --host, --port, --ctx-size, -t`뿐:

| 설정(예시 env) | 예상 명령줄 | 실제 반영 |
|---|---|---|
| `KV_CACHE=q8_0` | `--cache-type k:q8_0,v:q8_0` | **미전달 → 무시** |
| `PARALLEL=1` | `--parallel N` | **미전달 → 무시** |
| `BATCH/UBATCH` | `-b/-ub` | **미전달 → 무시** |
| `NO_THINK` | – | **미전달 → 무시** |

→ 14B 판사의 KV는 계획 추정(1.5GB) 대비 **2배(~3GB)**.
→ "워커 4개"가 실상 **각자 GGUF 4.7GB를 중복 로드**하는 4개 독립 프로세스(총 ~24GB 낭비).

### 🟠 P3. 모델별 균일 `CTX_SIZE=16384` → KV 낭비

판사(GBNF, 20토큰 이내)는 16K가 불필요. 역할별 비대칭 컨텍스트:

$$
R_r \propto \text{ctx}_r
\qquad
\Rightarrow\quad \text{작은 판사/파서 } \text{ctx} \downarrow,\ \text{큰 세터/리즈너 } \text{ctx} \uparrow
$$

### 🟠 P4. 출력 토큰 예산 vs CPU 속도의 시간 수학 위반

Reasoner `max_tokens=2000` (PLAN:19):

$$
t_{\text{reasoner}} \approx \frac{2000}{7\text{–}9} \approx 220\text{–}290\ \text{초} \;(4\text{–}5분)
$$

PLAN의 "30초 내 생성/판사 1초"(PLAN:119,160)와 **수학적으로 모순**. 판사 1초는 입력 프리필(8–15초) 때문에 불가능 — 실제적 SLA는 **5–10초**.

### 🟡 P5. `performance_tuning.md` — 아키텍처·타깃 모델 오류

- 9700X를 "**Zen 3**"으로, 타깃을 **Mistral-Small-24B**로 기술 → 실제는 **Zen 5** + 7B/14B 파이프라인.
- 빌드 플래그는 $-\text{march=}\text{znver3}$ 보다 $-\text{march=}\text{native}$ (Zen5 AVX‑512) 가 적합.

### 🟡 P6. 잔재·명칭 드리프트

- `server/config/models/mistral-large.env(.example)`, `llama_serve_mistral.sh` — 삭제된 24B 잔재.
- `contract.yml`은 `generator/retriever/embedder` vs PLAN의 `worker/coder/judge/setter` — API 명세 불일치.
- reasoner: `server/README.md:23`은 "HF에 GGUF 없음"이라 서술하는데 env는 `unsloth/...GGUF`를 가리킴.

### 🟡 P7. 임베딩·RAG가 미구현 (contract만 존재)

`/embed,/retrieve,/generate`(contract.yml)·`embedding` 슬러그(model_registry.py)는 **구현 전**.
bge-small은 llama.cpp보다 Python(sentence-transformers) 상주가 적합.

---

## 3. 결론: 가장 중요한 두 가지

$$
\boxed{%
\begin{gathered}
\text{① 디코딩은 대역폭 결합} \;\Rightarrow\; \text{최적점 = "동시 GGUF 종류 수"}\\
\text{② 현재는 역행: 9중 로드 + 튜닝 플래그 미적용} \;\Rightarrow\; \text{메모리 2–3배 낭비, 스레드 과잉}
\end{gathered}}
$$

**수정 우선순위**

| 우선순위 | 조치 | 작업 위치 |
|---|---|---|
| P0 | `start_all.sh` on-demand 2–3개만 로드, setter/judge 상호배타 | `start_all.sh` + 오케스트레이터 |
| P0 | `--parallel`, `--cache-type k:q8_0,v:q8_0`, batch 전달 → GGUF당 1프로세스로 통폐합 | `llama_serve_generic.sh` + env |
| P1 | 스레드 합 ≤ 16, 역할별 CTX 차등화 | 각 model env |
| P1 | PLAN 시간 지표를 실측(프리필 포함)으로 교정 | PLAN.md |
| P2 | `performance_tuning.md` Zen5/native + 현재 모델 기준 갱신, mistral 제거 | doc + 서버 파일 |