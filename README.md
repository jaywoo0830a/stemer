# Qwen3.6-27B 로컬 LLM 서버 — VS Code 에이전트용

자택/사무실 서버(**Ryzen 7 9700X · 64GB DDR5-5200 On-Die ECC · NVMe 소프트 RAID**)에서
Qwen3.6-27B를 서빙하고, SSH 포워딩 또는 LAN으로 VS Code 에이전트를 연결해 쓰는 구성입니다.

> 💻 VS Code에서 에이전트로 쓰는 방법(Cline/Roo/Continue 설정)은
> [VSCODE_AGENT.md](VSCODE_AGENT.md) 참고
> 📐 각 설정의 근거(왜 이 값인가)는 [SETTINGS_RATIONALE.md](SETTINGS_RATIONALE.md) 참고

## 구성도

```mermaid
graph LR
    A[VS Code<br/>Continue/Cline] -->|ssh -L 8000 또는 LAN| B[서버: llama-server<br/>127.0.0.1:8000/v1]
    B --> C[Qwen3.6-27B<br/>Q8_0 GGUF ~29GB]
    D[Ryzen 7 9700X 16T<br/>AVX-512 · 64GB RAM] -.실행.-> B
```

## 조작은 5개 스크립트뿐

| 스크립트 | 역할 | 시점 |
|---|---|---|
| `./init.sh` | 의존성 설치 · llama.cpp 빌드(AVX-512) · 모델 다운로드(~29GB) | 최초 1회 |
| `./up.sh` | 서버 시작 + 헬스체크 + 접속 정보 출력 | 평상시 |
| `./down.sh` | 서버 종료 | 평상시 |
| `./worker-up.sh` | RAG 파이프라인(인덱싱·노트생성) + 웹 UI 시작 | 평상시 |
| `./worker-down.sh` | RAG 파이프라인 + 웹 UI 종료 | 평상시 |

```bash
./init.sh        # 1회 (빌드 3~6분 + 다운로드 시간. 중단 후 재실행하면 이어받기)
./up.sh          # 시작 (첫 로딩 1~2분)
./worker-up.sh   # RAG 워커 + 웹 UI 시작 (http://<서버_IP>:8080)
./worker-down.sh # RAG 워커 + 웹 UI 종료
./down.sh        # 종료
```

- 로그: `logs/llama-server.log`, PID: `llama-server.pid`
- 튜닝은 `config.env` 하나만 수정하면 됩니다 (재시작 시 반영).
- 설정 프리셋은 저장소의 `config.env.example`입니다. `init.sh`가 첫 실행 시
  이 파일을 `config.env`로 복사하므로 클론 직후 바로 시작할 수 있습니다.
  (`config.env` 자체는 서버별 값이라 git에 올리지 않습니다.)
- RAM이 32GB 이상이면 `init.sh`가 스왑 생성을 자동으로 건너뜁니다.

## 품질 우선 기본값

- Qwen3.6 공식 권장 thinking 샘플링: `temp 0.6 / top_p 0.95 / top_k 20`
- thinking 모드 ON(기본), KV캐시 `q8_0`(f16 대비 품질 차이 미미, 메모리 절반)
- 퀀트 기본 `Q8_0`(8비트, 원본 품질에 근접) — 64GB RAM 덕에 가능. VPS 시절 Q4_K_M보다 두 단계 위
- thinking 강도 기본 `medium` — `high`는 엄밀함이 필요한 요청에 per-request로 재정의(아래 참고)
- 추측 디코딩 기본 `ngram-mod` (모델 불필요, 품질 무손실). `draft-simple`/`draft-mtp`는 선택(아래 섹션)
- 컨텍스트 기본 32K (학습자료 파일 단위 생성 기준). `config.env`의 `CTX_SIZE`로 조절 가능
- 속도보다 정확도: 응답 1회에 십수 분~수십 분 걸릴 수 있음이 정상입니다

## 하드웨어 활용 메모 (9700X · 64GB · NVMe RAID)

- **AVX-512**: Zen 5는 512비트 AVX-512를 지원합니다. 네이티브 빌드(`-DGGML_NATIVE=ON`)가
  자동 활성화하며, 프롬프트 처리 속도가 구형 CPU 대비 2배 이상 빨라집니다.
- **메모리 대역폭**: 토큰 생성 속도는 사실상 RAM 대역폭에 좌우됩니다.
  상한은 대략 `대역폭(GB/s) ÷ 모델크기(GB)` tok/s (DDR5-5200 듀얼채널 ≈ 60GB/s).
- **On-Die ECC**: DDR5 자체 오류정정으로 램 셀 오류를 줄여줍니다.
  (CPU-메모리 구간 전체를 보장하는 서버용 풀 ECC는 아니므로 참고 수준)
- **NVMe 소프트 RAID**: 모델 로딩이 빨라질 뿐, 추론 중에는 성능 영향이 없습니다.
- **전력/발열**: CPU 추론은 지속 풀로드입니다. 서버실 통풍·쿨러 상태를 확인하세요.
- **더 빠른 긴 컨텍스트**: 플래시 어텐션은 최신 버전 기본 `auto`. 효과를 명시하려면 `LLAMA_EXTRA_ARGS="--flash-attn on"` (오류 나면 제거)

## 퀀트 선택 (`config.env`의 `MODEL_FILE`)

| 파일 | 크기 | 품질 | 생성 속도 | 비고 |
|---|---|---|---|---|
| `Qwen3.6-27B-Q8_0.gguf` | ~29GB | 최고 | ~2 tok/s | **기본값** |
| `Qwen3.6-27B-Q6_K.gguf` | ~22GB | 준최고 | 2~3 tok/s | 속도 절충안 |
| `Qwen3.6-27B-Q5_K_M.gguf` | ~21GB | 중상 | 2~3 tok/s | Q4보다 품질↑ |
| `Qwen3.6-27B-Q4_K_M.gguf` | ~17GB | 양호 | 3~4 tok/s | VPS 시절 기본값 |

## 추측 디코딩 (속도 개선, 품질 무손실)

생성 속도를 품질 손실 없이 올리는 기능입니다. 원리: 작은 모델 또는 n-gram이 다음
토큰들을 미리 예측하고, 메인 모델이 한 번의 가중치 통과로 한꺼번에 검증합니다.
검증은 메인 모델이 하므로 **출력 품질은 동일**하며, 메모리 대역폭에 묶인 CPU
환경에서 특히 효과가 큽니다. `config.env`의 `SPEC_TYPE`으로 선택합니다.

| `SPEC_TYPE` | 설명 | 필요 파일 | 효과 |
|---|---|---|---|
| `ngram-mod` (**기본**) | n-gram 해시 풀 — 코드/템플릿/반복 텍스트↑ | 없음 | 1.2~1.5x |
| `ngram-simple` | n-gram 패턴 매칭 | 없음 | 1.2~1.5x |
| `draft-simple` | 소형 드래프트 모델 | `DRAFT_MODEL_FILE` (~1.7GB) | 1.5~2.5x |
| `draft-mtp` | 메인 GGUF의 MTP 헤드 | 메인 모델 내장 | 1.5~2배 |

- **기본 `ngram-mod`**: 모델 추가 없이 즉시 적용. 수락률은 텍스트 반복성에 따라
  달라지며, KaTeX 템플릿·문제 나열·코드 블록이 많은 학습자료 생성에 잘 맞습니다.
- **`draft-simple`**: 같은 토크나이저(Qwen3 계열)의 소형 모델을 드래프트로 사용.
  `init.sh`가 자동 다운로드하며, 시작 시 토크나이저 불일치로 실패하면 다른
  Qwen3 계열 드래프트로 바꾸세요.
- **`draft-mtp`**: 이 프로젝트 기본 unsloth GGUF에는 MTP 헤드(`nextn`)가 없어
  사용 불가. `init.sh`가 "MTP 헤드 포함"이라고 알려준 GGUF에서만 켜세요.
- 적용: `SPEC_TYPE` 변경 후 `./down.sh && ./up.sh`. 시작 로그의
  `draft acceptance rate`로 실제 수락률을 확인할 수 있습니다.

## 원격 접속

### A. SSH 포워딩 (기본, 권장)

터널 생성 (창을 하나 띄워 두기):

```bash
ssh -N -L 8000:127.0.0.1:8000 <사용자>@<서버_IP>
```

- 이후 클라이언트 PC에서 `http://localhost:8000/v1` 이 서버로 연결됩니다.
- 서버가 VS Code **Remote-SSH** 대상이면 포워딩 없이 `http://127.0.0.1:8000/v1` 그대로 사용합니다.
- Windows라면 PowerShell에 위 명령을 그대로 쓰거나, Putty의 Connection → SSH → Tunnels에서 동일 설정.

### B. LAN 직접 연결 (같은 네트워크일 때)

`config.env`에서:

```bash
HOST="0.0.0.0"
API_KEY="적당히-긴-랜덤-문자열"   # 비워두지 말 것!
```

- `./down.sh && ./up.sh` 후 `http://<서버_IP>:8000/v1` 로 접속.
- API Key는 에이전트 설정의 API Key 항목에 동일하게 입력합니다.
- 외부 노출: 공유기 포트포워딩 + 방화벽. `FIREWALL_ENABLE="on"`이면
  `up.sh`가 ufw 포트를 자동 개방, `down.sh`가 폐쇄합니다.
- **외부/LAN 노출 시 API_KEY 필수** — 비어 있으면 `up.sh`가 시작을 거부합니다.

## VS Code 에이전트 연결

상세 가이드는 [VSCODE_AGENT.md](VSCODE_AGENT.md) 참고. 아래 표의 Base URL/API Key만
접속 방식에 맞게 바꿉니다.

| 접속 방식 | Base URL | API Key |
|---|---|---|
| SSH 터널 | `http://localhost:8000/v1` | `local` |
| Remote-SSH (서버 안에서) | `http://127.0.0.1:8000/v1` | `local` |
| LAN/외부 직접 HTTP | `http://<서버_IP>:8000/v1` | `config.env`의 `API_KEY` 값 |

### A. Continue (추천, 최신 버전은 YAML)

최신 Continue는 `config.json` 대신 `~/.continue/config.yaml`을 기본으로 읽습니다.
Continue 사이드바 → ⚙️ → **"Open config file"** 로 실제 사용 중인 파일을 연 뒤
아래 내용을 넣으세요:

```yaml
name: Qwen3.6-27B (Server)
version: 1.0.0
schema: v1
models:
  - name: Qwen3.6-27B (Server)
    provider: openai
    model: Qwen3.6-27B
    apiBase: http://localhost:8000/v1
    apiKey: local
    roles:
      - chat
      - edit
      - apply
    defaultCompletionOptions:
      maxTokens: 4096
```

저장 후 `Ctrl+Shift+P` → `Developer: Reload Window`. 이후 모델 드롭다운에서
`Qwen3.6-27B (Server)`를 선택하고 **Agent 모드**로 쓰면 됩니다.
(LAN/외부 접속이면 `apiBase`/`apiKey`를 위 표 값으로 변경)

### B. Cline / Roo Code

| 항목 | 값 |
|---|---|
| API Provider | `OpenAI Compatible` |
| Base URL | 위 표 (기본 `http://localhost:8000/v1`) |
| API Key | 터널이면 `local`, LAN/외부면 `config.env`의 `API_KEY` |
| Model ID | `Qwen3.6-27B` |

- Plan 모드로 계획을 먼저 받고 → Act 모드로 실행하는 흐름이 thinking 모델에 잘 맞습니다.
- Cline은 시스템 프롬프트가 커서 CPU 환경에서는 턴당 수 분 걸립니다. Continue가 더 가볍습니다.

## 예상 성능 (정직한 기준)

| 항목 | 예상 |
|---|---|
| 생성 속도 | Q8_0 ~2 tok/s · Q6_K 2~3 tok/s · Q4_K_M 3~4 tok/s (ngram-mod 기본 적용, draft-simple 시 1.5~2.5배) |
| 프롬프트 처리 | 200~500 tok/s (AVX-512, 배치 2048) |
| 응답 1회 | thinking(medium) 포함 3~8K 토큰 → **십수 분~수십 분** |
| 메모리 | Q8_0 29GB + KV 8GB + 런타임 ≈ 38~40GB / 64GB (draft-simple이면 드래프트 +2GB 내외) |

자동완성(FIM) 용도로는 부적합하고, **챗/작업 위임형 에이전트**에 맞습니다.

## 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| `up.sh` 직후 프로세스 종료 | `logs/llama-server.log` 확인. 메모리 부족 시 `CTX_SIZE`를 32768로 낮추고 `./down.sh && ./up.sh` |
| 옛 VPS 설정 그대로임 | `config.env`는 이미 있으면 덮어쓰지 않습니다. 새 기본값을 쓰려면 `./down.sh` 후 `config.env` 삭제 → `./init.sh` |
| 퀀트 변경하고 싶음 | `config.env`의 `MODEL_FILE` 교체 → `./down.sh` → `./init.sh`(새 모델 다운로드) → `./up.sh` |
| 응답이 너무 오래 걸림 | thinking이 긴 것. 엄밀함이 필요 없는 요청은 per-request로 `reasoning_effort`를 낮추거나 `REASONING_EFFORT="low"`로 변경 후 재시작 |
| `draft-mtp` 오류로 시작 실패 | 이 GGUF에 MTP 헤드가 없음(기본 unsloth 모델). `SPEC_TYPE`를 `ngram-mod`(기본)나 `draft-simple`로 변경 후 `./up.sh` |
| `draft-simple` 시작 실패 | 드래프트 모델이 메인과 토크나이저 불일치. Qwen3 계열 드래프트로 교체하거나 `SPEC_TYPE="ngram-mod"`로 복귀 |
| 외부/LAN에서 접속 안 됨 | 서버에서 `./up.sh`로 방화벽 개방 확인, 공유기 포트포워딩, `API_KEY` 일치 여부 확인 |
| 응답이 매우 느림 | 스왑 사용 중일 가능성 → `free -h` 확인. 불필요한 프로세스 정리 |
| 다운로드 중단 | `./init.sh` 재실행 → 이어받기 |
| 터널이 자주 끊김 | `ssh -N -L ... -o ServerAliveInterval=60 -o ServerAliveCountMax=3` 추가. 또는 LAN/외부 직접 HTTP로 전환 |
| 서버 재부팅 후 | `./up.sh` 한 번만 다시 실행 (방화벽도 함께 개방) |

## 보안

- `HOST=127.0.0.1` 기본값: 외부에서 직접 접근 불가, SSH 터널로만 진입.
- `HOST=0.0.0.0`(LAN/외부): **API_KEY가 비어 있으면 `up.sh`가 시작을 거부**합니다.
- 방화벽 개방/폐쇄는 `up.sh`/`down.sh`가 자동 처리 (`FIREWALL_ENABLE="on"`,
  ufw 기준, sudo 필요 시 안내만 출력). 외부 노출 시 공유기 포트포워딩과
  **강한 API_KEY 유지**를 권장합니다.
