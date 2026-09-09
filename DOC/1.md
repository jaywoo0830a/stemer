# myLLM Script Runner API

제3자(내부 서비스)가 서버의 셸 스크립트를 **HTTP 로** 실행하게 하는 인터페이스입니다.
Docker Compose 에 FastAPI 로 띄우며, 화이트리스트(allowlist)로 허용된 액션만 실행 가능합니다.

---

## 1. 파일 구성

```
myllm/
├── docker-compose.yml          # 서비스 정의 (루)
└── scripts/api/
    ├── app.py                  # FastAPI 엔드포인트
    ├── allowlist.py            # 화이트리스트 (어떤 스크립트를 어떤 인자로 허용할지)
    ├── runner.py               # subprocess 실행 (+ 타임아웃, 출력 캡처)
    ├── requirements.txt        # fastapi / uvicorn / pydantic
    ├── Dockerfile              # python:3.11-slim + bash
    └── README.md               # 이 문서
```

---

## 2. 실행 (Docker Compose)

```bash
# 이미지 빌드 + 컨테이너 기동
docker compose -f docker-compose.yml up --build -d

# 상태 확인
curl http://localhost:8000/health
```

필요 시 `.env` 또는 환경변수로 토큰 설정:

```bash
# 인증 토큰(권장). 설정하면 모든 /v1 호출에 Authorization: Bearer 필요.
export EXTERNAL_TOKEN='my-secret'
docker compose -f docker-compose.yml up -d
```

---

## 3. 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET  | `/` | 환영 메시지 |
| GET  | `/health` | 헬스체크 (bash 유무, scripts_dir) |
| GET  | `/v1/allowlist` | 실행 가능한 액션/슬러그 목록 |
| POST | `/v1/run` | 화이트리스트 스크립트 실행 |
| GET  | `/openapi.json` | OpenAPI 문서 |

### POST `/v1/run`

요청 본문:

```json
{
  "action": "up",
  "arg": "parser"
}
```

- `action`: `up` · `down` · `start_all` · `start_heavy` · `status` · `restart` · `log`
- `arg`: 모델 slug (`parser, worker1~4, coder1~4, reasoner, setter, judge`) — `action`이 인자 요구 시에만

예:

```bash
# 상태 리포트 생성(조회)
curl -X POST http://localhost:8000/v1/run \
  -H 'Content-Type: application/json' \
  -d '{"action":"status"}'

# 모델 서버 시작
curl -X POST http://localhost:8000/v1/run \
  -H 'Content-Type: application/json' \
  -d '{"action":"up","arg":"parser"}'

# 14B 단독(on-demand, 상호배타)
curl -X POST http://localhost:8000/v1/run \
  -H 'Content-Type: application/json' \
  -d '{"action":"start_heavy","arg":"setter"}'

# 인증 토큰이 설정된 경우
curl -X POST http://localhost:8000/v1/run \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer my-secret' \
  -d '{"action":"status"}'
```

응답:

```json
{
  "action": "status",
  "ok": true,
  "returncode": 0,
  "elapsed_s": 0.1,
  "timed_out": false,
  "stdout": "✅ ...",
  "stderr": ""
}
```

---

## 4. 보안

- **화이트리스트**: `allowlist.py`에 정의된 액션이 아니면 400. slug 도 허용 목록에 있어야 함.
  (예: `{"action":"up","arg":"parser;rm -rf /"}` → `400 arg ... not allowed`)

- **주입 방지**: `shell=True` 를 쓰지 않고 `subprocess.run(argv_list)` 를 사용.
  Python 셸 변조 불가.

- **읽기 전용 마운트**: `server/scripts`, `server/config` 는 `:ro`.
  PID/로그만 쓸 수 있도록 `server/run` 만 `:rw`.

- **토큰(옵션)**: `EXTERNAL_TOKEN` 을 설정하면 모든 `/v1/status·allowlist·run` 에
  `Authorization: Bearer <token>` 이 필요.

---

## 5. 운영 컨텍스트 (중요)

이 API 는 **server/scripts/*.sh를 실행**합니다. 다만 **실행 위치에 따라 동작이 다릅니다.**

### 5a. 컨테이너에서 실행 (기본 docker compose)
- `status` · `allowlist` · `health` · `log`: **완전 동작**.
- `up`/`down`/`start_heavy`: 컨테이너 안에서 실행되므로, **llama-server 바이너리/모델 GGUF가
  컨테이너에 없으면 실패**. 컨테이너는 보통 모델 서버가 아니라 API 프록시로 띄우므로,
  **실제 모델 프로세스 제어는 호스트에서** 수행합니다.

### 5b. 호스트에서 실행 (모델 서버를 직접 제어)
실제 llama-server 를 켜고 끄려면 **호스트**(9700X 서버)에서 이 API 를 실행해야 합니다.

```bash
cd /path/to/myllm
PYTHONPATH=. EXTERNAL_TOKEN=... uvicorn scripts.api.app:app --host 0.0.0.0 --port 8000
```

이렇게 하면 컨테이너가 아니라 **호스트 프로세스**가 되어 up/down 이 실제 PID/로그를 관리합니다.

> 요약: **"제3자에게 상태 조회/구성 확인을 열어주는 API"는 도커로 완성.**
> **"실제 모델 서버(llama-server)를 제어"하는 건 호스트에서 이 API 를 실행**해야 합니다.

---

## 6. 수명·타임아웃

- `MYLLM_CMD_TIMEOUT`(기본 180s) 으로 무한 대기 방지. 초과 시 `timed_out: true`.
- `status_report.sh`는 `--remote` + 출력 `/tmp/myllm_STATUS.md` 로 실행되어
  호스트 STATUS.md 를 건드리지 않습니다.

---

## 7. 예외 처리

- 액션이 허용 목록에 없음 → `400`
- 인증 실패(토큰 설정 시) → `401`
- 스크립트 경로가 없음 → `400`

모든 응답은 JSON 이며, 스크립트의 stdout/stderr 를 그대로 돌려주므로
호출 측에서 로그로 활용할 수 있습니다.