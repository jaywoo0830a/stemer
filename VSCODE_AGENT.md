# VS Code 에이전트 사용 가이드

VPS에서 서빙 중인 Qwen3.6-27B를 집 PC의 VS Code에서 에이전트로 사용하는 방법입니다.

> VPS: `ubuntu@158.69.212.179` · API: `http://localhost:8000/v1` (터널 기준)

## 0. 사전 조건

VPS에서 서버가 떠 있는지 확인:

```bash
cd ~/projects/stemer
curl http://127.0.0.1:8000/health    # {"status":"ok"} 확인
./up.sh                              # 꺼져 있으면 시작
```

## 1. 집 PC에서 SSH 터널 열기

```bash
ssh -N -L 8000:127.0.0.1:8000 ubuntu@158.69.212.179
```

- 이 창은 켜둔 채로 유지합니다 (터널이 끊기면 연결도 끊김).
- **다른 터미널**에서 연결 확인:

```bash
curl http://localhost:8000/v1/models
```

응답에 `"id": "Qwen3.6-27B"`가 보이면 성공. 이제 집 PC의 `localhost:8000`이 VPS의 모델 서버와 연결됩니다.

> Windows라면 PowerShell에서 동일 명령, 또는 Putty → Connection → SSH → Tunnels에서
> `Source port 8000 / Destination 127.0.0.1:8000` 설정.

## 2. 진짜 에이전트: Cline / Roo Code (추천)

파일 수정·터미널 실행까지 스스로 수행하는 완전한 에이전트입니다.

### 설치

- VS Code 확장 마켓플레이스에서 `Cline` 또는 `Roo Code` 설치

### 설정

| 항목 | 값 |
|---|---|
| API Provider | `OpenAI Compatible` |
| Base URL | `http://localhost:8000/v1` |
| API Key | `local` (아무 값이나) |
| Model ID | `Qwen3.6-27B` |

### 사용 흐름

1. **Plan 모드**로 작업 계획부터 받기 — thinking 모델이라 계획 품질이 좋음
2. 계획 확인 후 **Act 모드**로 실행
3. 작업은 작게 쪼개기 — CPU라 턴당 **수 분** 걸리는 게 정상

## 3. 가벼운 채팅+편집: Continue

### 설치

- VS Code 확장 마켓플레이스에서 `Continue` 설치 후 재로드

### 설정 (최신 버전은 YAML)

최신 Continue는 `config.json` 대신 `~/.continue/config.yaml`을 기본으로 읽습니다.
Continue 사이드바 → ⚙️(톱니) → **"Open config file"** 을 누르면 실제 사용 중인
파일이 열리므로, 열린 파일에 아래 내용을 넣으세요:

```yaml
name: Qwen3.6-27B (VPS)
version: 1.0.0
schema: v1
models:
  - name: Qwen3.6-27B (VPS)
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

저장 후 `Ctrl+Shift+P` → `Developer: Reload Window`.

### VS Code에서 쓰기

1. 좌측 사이드바의 **Continue 아이콘** 클릭 → 채팅 패널
2. 채팅 입력창의 **모델 드롭다운**에서 `Qwen3.6-27B (VPS)` 선택
3. **모드 드롭다운**에서 선택:
   - **Agent**: 스스로 파일 탐색·수정·명령 실행까지 하는 에이전트 모드
   - **Chat**: 일반 질문
4. 첫 응답까지 1~2분 (CPU라 정상 — 끊지 말 것)

**단축키:**

| 키 | 동작 |
|---|---|
| `Ctrl+L` | 코드 선택 후 채팅으로 보내기 |
| `Ctrl+I` | 선택한 코드 인라인 편집 |
| `@` 입력 | `@Files`·`@Codebase` 등 컨텍스트 추가 |

## 4. Remote-SSH로 VPS에 접속하는 경우 (터널 불필요)

집 PC에서 `ssh -L` 터널을 따로 열 필요가 없습니다.
VS Code가 Remote-SSH로 VPS에 붙어 있으면 확장이 VPS 내부에서 실행되므로,
위 설정에서 Base URL만 `http://127.0.0.1:8000/v1`로 쓰면 됩니다.

## 5. 성능 안내 및 팁 (CPU 환경)

| 항목 | 예상 |
|---|---|
| 응답 시작 | 1~2분 (thinking) |
| 코드 작성 | 턴당 수 분 |
| 생성 속도 | 2~4 tok/s |

- thinking이 길어 응답이 늦어도 끊지 말고 기다리세요.
- 대화가 길어져 느려지면 **새 태스크/새 채팅**으로 시작하는 게 빠릅니다.
- 컨텍스트 24K 초과가 필요하면 VPS의 `config.env`에서 `CTX_SIZE`를 올리고 `./down.sh && ./up.sh`.
- 자동완성(FIM) 용도로는 실용성이 낮습니다. 챗/작업 위임형으로 쓰세요.

## 6. 트러블슈팅

| 증상 | 해결 |
|---|---|
| `curl`이 안 됨 | 터널 창이 살아있는지, VPS에서 `./up.sh` 했는지 확인 |
| 모델 목록에 안 보임 | Continue 톱니 → "Open config file"이 `config.yaml`인지 확인 후 저장·재로드 |
| 401/403 에러 | API Key에 `local` 입력 확인 |
| 응답이 극단적으로 느림 | VPS에서 `free -h`로 스왑 사용 확인, 불필요한 프로세스 정리 |
| 터널이 자주 끊김 | `ssh -N -L ... -o ServerAliveInterval=60 -o ServerAliveCountMax=3` 옵션 추가 |
| 서버 재부팅 후 | VPS에서 `./up.sh` 한 번 실행 |
