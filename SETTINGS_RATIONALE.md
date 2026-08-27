# 설정 근거 문서 — Qwen3.6-27B 로컬 LLM 서버

`config.env`의 각 값과 스크립트 동작을 **왜 그렇게 설정했는지**에 대한 근거 모음입니다.
모든 수치는 추정치이며, 실측으로 검증하는 것을 권장합니다.

---

## 0. 전제: 하드웨어의 물리적 한계

| 자원 | 사양 | 함의 |
|---|---|---|
| CPU | Ryzen 7 9700X (8C/16T, Zen 5) | 512비트 AVX-512 지원 → **프롬프트 처리(프리필)가 구형 대비 2배 이상** |
| RAM | 64GB DDR5-5200 듀얼채널 | 이론 83GB/s, 실효 **약 60GB/s** → **토큰 생성 속도의 상한** |
| SSD | NVMe 소프트 RAID 2개 | 모델 로딩 속도에만 영향, 추론 중 영향 없음 |

> CPU 사양 출처: [AMD 공식 사양 포털](https://www.amd.com/en/products/specifications/processors.html)
> (9700X: 8코어/16스레드, Zen 5, AVX-512 지원, DDR5-5600 공식 지원 — DDR5-5200은 이 범위 내).
> 대역폭 계산: $5200 \times 8 \times 2 = 83.2\text{ GB/s}$ (이론), 실효는 보통 70~80%.

### 핵심 물리 공식 2개

**① 토큰 생성은 메모리 대역폭 지배 (bandwidth-bound)**

$$ \text{생성 속도 상한} \approx \frac{\text{실효 대역폭}}{\text{모델 크기}} = \frac{60 \text{ GB/s}}{29 \text{ GB}} \approx 2.1 \text{ tok/s} $$

토큰 1개를 만들 때마다 가중치 전체(29GB)를 읽어야 하기 때문입니다.
CPU 코어가 아무리 많아도 이 벽은 넘을 수 없습니다.

**② 프리필(컨텍스트 읽기)은 연산 지배 (compute-bound)**

프롬프트 토큰은 배치로 한 번에 처리되므로, AVX-512 벡터 폭(512bit vs 256bit)이
그대로 성능으로 이어집니다. 9700X의 프리필은 구형 VPS의 3~4배입니다.

→ 이 두 공식이 이 문서의 **모든 결정의 출발점**입니다.

---

## 1. 모델 및 양자화

### `MODEL_REPO="unsloth/Qwen3.6-27B-GGUF"`

- 이 프로젝트의 기존 모델(Qwen3.6-27B)을 유지. thinking(추론) 모델이라
  챗/작업 위임형 에이전트에 적합 (자동완성 부적합).
- unsloth는 표준화된 GGUF 퀀트 파일셋(Q4_K_M~Q8_0)을 제공.

### `MODEL_FILE="Qwen3.6-27B-Q8_0.gguf"` (기본, ~29GB)

| 근거 | 내용 |
|---|---|
| 품질 | Q8_0은 8비트 양자화 — 원본(f16) 품질에 근접, Q4_K_M 대비 **두 단계 위** |
| 메모리 여유 | 29GB + KV 8GB + MTP 드래프트 KV 8GB ≈ 46~50GB < 64GB ✓ |
| 프로젝트 방침 | 이 프로젝트는 "속도보다 정확도" (품질 우선) |
| 트레이드오프 | ~2 tok/s로 Q4_K_M(3~4 tok/s)보다 느림 — 대역폭 공식 ①의 직접 결과 |

**양자화 선택지 비교** (`MODEL_FILE` 교체로 변경 가능):

| 파일 | 크기 | 상대 품질 | 생성 속도(① 공식) | 언제? |
|---|---|---|---|---|
| Q8_0 | ~29GB | 최고 | ~2 tok/s | 품질 최우선 (**기본**) |
| Q6_K | ~22GB | 준최고 | 2~3 tok/s | 약간의 속도 필요할 때 |
| Q5_K_M | ~21GB | 중상 | 2~3 tok/s | Q4보다 품질 올리고 싶을 때 |
| Q4_K_M | ~17GB | 양호 | 3~4 tok/s | 속도 최우선 |

> 각 양자화 수준의 설명: [HF GGUF 문서](https://huggingface.co/docs/hub/en/gguf)의 양자화 표 참조.

---

## 2. 컨텍스트 및 KV 캐시

### `CTX_SIZE="65536"` (64K)

- 64GB RAM 기준으로 안전한 최대치. KV 캐시 비용 계산:

$$ \text{KV(64K)} \approx \text{레이어} \times 2 \times \text{KV헤드} \times \text{헤드차원} \times 65536 \approx 7 \sim 9 \text{ GB (q8_0)} $$

- 긴 시스템 프롬프트(Cline/Roo 등)와 파일 컨텍스트를 잘 소화.
- 주의: 컨텍스트가 길수록 토큰마다 KV를 더 읽어야 하므로, 풀 64K에서는
  생성 속도가 짧은 대화 대비 약 20~30% 느려집니다 (모든 하드웨어 공통 특성).

### `KV_CACHE_TYPE="q8_0"`

- q8_0은 f16 대비 **품질 차이가 사실상 없으면서**(퍼플렉서티 차이 <0.1% 수준)
  메모리를 절반으로 줄입니다.
- RAM이 남는다고 f16으로 올릴 이유가 없음 — 메모리만 두 배.

---

## 3. 연산 자원

### `THREADS="16"`

- 9700X는 빅코어 8개 + SMT = **16스레드 전부 사용**.
- 생성은 대역폭 지배지만, SMT로 대역폭 포화를 돕고 프리필 연산을 병렬화.
- 구형 VPS(8 vCore)의 2배.

### `BATCH_SIZE="2048"`

- 프리필에서 한 번에 처리하는 토큰 수. 배치가 클수록 AVX-512 연산 효율↑.
- 512(구형) → 2048(현재): 메모리 여유가 있어 가능. 그 이상은 추가 이득이
  미미하고 연산 버퍼 메모리만 증가.

### 빌드: `-DGGML_NATIVE=ON` (init.sh)

- 빌드 머신의 CPU에 맞춰 **AVX-512를 자동 활성화** (Zen 5는 512bit 네이티브).
- 명시적 플래그(`GGML_AVX512=ON`)보다 native가 항상 정확 — 다른 머신에
  복사해 쓸 바이너리를 만들지 않는 한 이 설정이 최선.
- 빌드 옵션 전체 목록: [llama.cpp `docs/build.md`](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).

---

## 4. 샘플링 및 사고(thinking)

### `TEMP=0.6 / TOP_P=0.95 / TOP_K=20`

- Qwen3.6 공식 권장 thinking 샘플링 값 그대로 사용.
- thinking 모델은 이 분포에서 추론 품질이 가장 안정적.

### `REASONING_EFFORT="high"`

- llama.cpp 서버 공식 옵션의 thinking 강도 단계: `minimal / low / medium / high / xhigh / max`
  ([`tools/server/README.md`](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)의
  `--reasoning-effort`).
- **high 선택 근거**: 추론을 더 오래 수행 → 난이도 있는 작업의 논리·정확도↑.
  `xhigh/max`는 thinking 예산이 극단적으로 커져 응답이 수십 분~시간 단위가 될 수
  있어 기본값으로는 비실용적.
- 비용: thinking 토큰이 늘어 응답이 십수 분~수십 분 걸릴 수 있음.
- 단순 질문 위주면 `medium`으로 되돌리는 것이 현실적 (트러블슈팅 표 참고).

### `MAX_TOKENS="-1"`

- 제한 없음: thinking 모델은 필요한 만큼 생각하게 두는 것이 품질에 유리.
  최대 길이 제한은 클라이언트(Continue/Cline)의 maxTokens로 조절.

---

## 5. MTP 추측 디코딩 (`draft-mtp`)

### `SPEC_TYPE="draft-mtp"` + `SPEC_DRAFT_N_MAX="3"`

- **원리**: 모델 내부의 MTP(Multi-Token Prediction) 헤드가 다음 토큰 3개를 미리
  예측 → 메인 모델이 **한 번의 가중치 통과로 3개를 동시에 검증**.
- **품질 손실 없음**: 최종 검증은 메인 모델이 하므로 출력 분포가 동일.
- **CPU에서 특히 유리**: 대역폭 지배 환경(공식 ①)에서 검증 배치화가
  가중치 읽기 횟수를 1/n으로 줄임.
- **별도 모델 파일 불필요**: [공식 `docs/speculative.md`](https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md)가 명시 —
  "MTP heads **from the main model**" (MTP 헤드는 메인 모델 내장).
  `-mtp.gguf` 같은 파일을 받으면 안 됨. `init.sh`가 다운로드 후
  `nextn` 텐서 존재를 검사해 알려줌.
- **기대 효과**: 수락률 60~80% 시 생성 속도 **1.5~2배**. 코드·반복 패턴에서
  수락률이 높고 자유 창작에선 낮음.
- **`n_max=3`**: [`tools/server/README.md`](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)의
  `--spec-draft-n-max` 기본값(3). 4 이상은 수락률 하락으로 오히려 손해인 경우가 많음.
- **메모리 비용**: 드래프트 컨텍스트 KV가 추가됨. 기본 f16 대신 `q8_0` 지정
  (`up.sh`가 `--spec-draft-type-k/v`로 자동 적용) → 64K 기준 약 8GB 절약.
- **호환성**: GGUF에 MTP 헤드가 없는 구형/타 모델이면 시작 실패 →
  `SPEC_TYPE=""` (트러블슈팅 표).

---

## 6. 메모리/OS 정책

### `MLOCK="on"` (`--load-mode mmap+mlock`)

- 모델 가중치를 RAM에 고정해 스왑/메모리 압축을 방지.
- 64GB에서 총 사용량 ~50GB로 여유 있지만, 다른 프로세스가 몰리거나
  커널이 스왑을 시도할 때 생성 속도 폭락을 막는 안전장치.
- **최신 llama.cpp에서는 `--mlock`이 deprecated** — [server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)의
  `--load-mode`가 대체. `up.sh`는 `mmap+mlock` 모드로 자동 적용.
- mlock 실패 시 경고 로그 후 계속 실행되는 것이 일반적 — 문제 시 `off`.

### 스왑 생략 (init.sh, RAM ≥ 32GB일 때)

- 구형 VPS(22GB)는 모델 17GB + KV + 런타임이 한계치여서 스왑 8GB가 필요했음.
- 64GB에서는 사용량의 2배 이상 여유 → 스왑은 오히려 은밀한 성능 저하 요인.

---

## 7. 네트워크/보안

### `HOST="127.0.0.1"` (기본)

- 기본은 루프백 전용 → SSH 포워딩으로만 접속 (가장 안전).
- **LAN 공개가 필요할 때만** `HOST="0.0.0.0"` + `API_KEY` 설정.
  API_KEY 없이 0.0.0.0으로 여는 것은 같은 네트워크 누구나 쓸 수 있어 비권장.
- 포트포워딩으로 외부 직접 노출은 비권장 (SSH 터널이 안전).

---

## 8. 종합: 구체적 예상 성능

> 아래 수치는 공식 ①(실효 대역폭 60GB/s)·②(AVX-512)와 문단별 가정에 기반한
> **추정치**입니다. 실측은 `logs/llama-server.log`의 `print_timings`로 확인하세요.

### 8.1 퀀트별 생성 속도 (공식 ①)

| 퀀트 | 모델 크기 | 상한(60÷크기) | **실측 예상** | MTP 적용 시(1.5~2배) |
|---|---|---|---|---|
| Q8_0 | 29GB | 2.07 tok/s | **~2.0 tok/s** | 3.0~4.0 |
| Q6_K | 22GB | 2.73 tok/s | **~2.5 tok/s** | 3.8~5.0 |
| Q5_K_M | 21GB | 2.86 tok/s | **~2.6 tok/s** | 3.9~5.2 |
| Q4_K_M | 17GB | 3.53 tok/s | **~3.4 tok/s** | 5.1~6.8 |

### 8.2 MTP 효과 (수락률별)

n=3일 때 검증 1회당 기대 수용 토큰: $\frac{1-a^4}{1-a}$

| 수락률 a | 기대 토큰/검증 | 이론 배속 | 실측 보수치 |
|---|---|---|---|
| 0.5 | 1.88 | 1.9x | ~1.4x |
| 0.6 | 2.18 | 2.2x | ~1.6x |
| 0.7 | 2.53 | 2.5x | ~1.9x |
| 0.8 | 2.95 | 3.0x | ~2.2x |

- 코드·설정파일·반복 패턴: 수락률 60~80% → **1.5~2배** 체감.
- 자유 창작·논증 글: 수락률 40~50% → 이득 적음 (그래도 품질 손실은 없음).

### 8.3 프리필과 첫 토큰까지의 시간 (TTFT)

- 프리필 속도: **200~500 tok/s** (AVX-512, 배치 2048). 계산에는 중간값 300 tok/s 사용.
- 프리필 시간은 프롬프트 길이에 정비례.

| 프롬프트 크기 | TTFT (300 tok/s 기준) | 범위 |
|---|---|---|
| 1K 토큰 | ~3초 | 2~5초 |
| 4K | ~13초 | 8~20초 |
| 16K | ~53초 | 32~80초 |
| 32K | ~1분 47초 | 1~3분 |
| 64K | ~3분 33초 | 2~5분 |

→ 에이전트(긴 시스템 프롬프트 + 파일 컨텍스트)는 이 TTFT가 응답 체감의 큰 부분.

### 8.4 응답 시간 시나리오 (Q8_0 + thinking high)

총 응답 시간 = TTFT(위 표) + 생성 시간. MTP 미적용 기준, MTP 적용 시 생성부 약 0.6배.

| 시나리오 | thinking | 답변 | 총 토큰 | 생성 시간 | MTP 시 |
|---|---|---|---|---|---|
| 단순 질문/소소한 수정 | 200 | 150 | 350 | ~3분 | ~2분 |
| 일반 코딩 작업 | 1,200 | 600 | 1,800 | ~15분 | ~8~10분 |
| 어려운 설계/리팩토링 | 4,000 | 1,500 | 5,500 | ~46분 | ~23~30분 |
| 최악(에이전트 멀티턴 누적) | — | — | 10K+ | 1시간 20분+ | — |

### 8.5 메모리 내역 (Q8_0 · 64K · MTP ON)

| 구성요소 | 크기 |
|---|---|
| 모델 가중치 (Q8_0) | ~29GB |
| KV 캐시 (64K, q8_0) | ~8GB |
| MTP 드래프트 컨텍스트 KV (q8_0) | ~8GB |
| 배치·연산 버퍼 (2048) | ~1~2GB |
| **합계** | **~46~48GB / 64GB (여유 16GB+)** |

- MTP OFF: 드래프트 KV 8GB 절약 → ~38~40GB.
- Q4_K_M 전환 시: ~34GB.
- 64K 풀 컨텍스트에서는 생성 속도가 짧은 대화 대비 약 20~30% 감속 (KV 읽기 추가).

### 8.6 로딩 및 기타

- 모델 로딩(NVMe RAID): **1~2분** — `up.sh` 헬스 타임아웃(600초)의 근거.
- mlock은 첫 로딩 시 페이지 접촉 시간을 약간 늘림(수 초~수십 초).
- 프리필·생성 모두 CPU 풀로드 지속 → 9700X(65W급) 쿨러·통풍 상태 확인 필요.

---

## 9. 결정 트리 (상황별 조정)

| 상황 | 조정 (`config.env`) |
|---|---|
| 단순 질문이 많은데 너무 느림 | `REASONING_EFFORT="medium"` |
| 속도가 최우선 | `MODEL_FILE="...-Q4_K_M.gguf"` |
| MTP 미지원 모델/오류 | `SPEC_TYPE=""` |
| 메모리가 부족해짐 | `CTX_SIZE="32768"` |
| LAN에서 직접 접속 | `HOST="0.0.0.0"` + `API_KEY` |
| 더 빠른 긴 컨텍스트 | `LLAMA_EXTRA_ARGS="--flash-attn on"` (기본 auto) |

---

## 10. 공식 문서/사양 참조

| 구분 | 링크 | 이 문서에서 인용한 내용 |
|---|---|---|
| CPU 사양 | [AMD 공식 사양 포털](https://www.amd.com/en/products/specifications/processors.html) | 9700X: 8C/16T, Zen 5, AVX-512, DDR5-5600 지원 |
| CPU 제품 | [AMD 프로세서 제품](https://www.amd.com/en/products/processors.html) | Ryzen 9000 시리즈 개요 |
| MTP 추측 디코딩 | [llama.cpp `docs/speculative.md`](https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md) | `draft-mtp` = "MTP heads from the main model", `--spec-draft-n-max` 기본 3 |
| 서버 옵션 | [llama.cpp `tools/server/README.md`](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) | `--reasoning-effort` 단계, `--spec-type`, `--load-mode`(mlock 대체), `--flash-attn` 기본 auto |
| 빌드 옵션 | [llama.cpp `docs/build.md`](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) | `GGML_NATIVE=ON` |
| GGUF/양자화 | [HF GGUF 문서](https://huggingface.co/docs/hub/en/gguf) | Q8_0 등 양자화 수준 표 |
| thinking 모델 | [Qwen3 블로그](https://qwenlm.github.io/blog/qwen3/) | thinking 샘플링 권장값(temp/top_p/top_k), 추론 강도 개념 |
| DDR5 표준 | [JEDEC JESD79-5](https://www.jedec.org/standards-document/docs/jesd79-5c) (가입 후 다운로드) | 대역폭 계산 근거 (5200MT/s × 8B × 2채널) |

- 메모리 대역폭 상한 공식은 CPU 추론의 보편적 특성 (가중치 전체 재읽기).
- 성능 수치는 하드웨어·llama.cpp 버전에 따라 변동 — `up.sh` 후
  `logs/llama-server.log`의 `print_timings`로 실측 확인 권장.
