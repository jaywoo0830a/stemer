# study/ — 교재 RAG 파이프라인

교재 PDF를 넣어 두면 밤새 **파싱 → 청킹 → 하이브리드 인덱싱 → 노트 생성**까지
자동으로 끝내주는 파이프라인입니다. 생성 엔진은 기존 llama-server
(Qwen3.6-27B, `../up.sh`)을 그대로 사용합니다.

> **아키텍처 (v2)** — RAG 인덱싱(Phase A/B)과 LLM 생성(Phase C)은 **동시에 절대
> 실행되지 않습니다** (같은 머신 64GB). llama-server는 상시 로드하지 않고,
> 생성 단계에서만 `up.sh`로 켰다가 `down.sh`로 내립니다 (~30GB 메모리 절약).
> 단일 스케줄러는 호스트의 `study/pipeline.sh`, 컨테이너는 순수한 phase CLI만
> 노출합니다. 상세: [`ARCHITECTURE.md`](ARCHITECTURE.md).

```mermaid
graph LR
    A[books/inbox/ PDF] -->|Docling 파싱<br/>캐시: books/markdown/| B[목차 기반 청킹<br/>800~1500자]
    B --> C[SQLite BM25 + Chroma bge-m3<br/>hybrid index: index/]
    D[TOPICS.md todo 행] --> E[1순위: topic→section 매핑<br/>+ RRF 융합 검색]
    C --> E
    E -->|bge-reranker 재정렬| F[llama-server 27B<br/>grounded 생성]
    F --> G[notes/ 저장<br/>TOPICS.md → draft]
```

## 1. 설치 (서버에서 1회)

```bash
bash study/setup.sh
```

- Docker가 없으면 설치합니다 (sudo 필요, `docker` 그룹 재로그인 안내).
- 이미지를 빌드하고 임베딩(`bge-m3`)·리랭커(`bge-reranker-v2-m3`) 모델을 미리 받습니다 (수 GB).
- 파이프라인 스케줄러는 호스트에서 `study/pipeline.sh watch`로 실행합니다
  (`./worker-up.sh`가 기본으로 백그라운드 기동).

## 2. 매일 밤 사용법

1. 교재 PDF를 `study/books/inbox/`에 넣습니다 (SCP/SFTP 등).
2. `TOPICS.md`에 만들 주제를 `todo` 상태로 적습니다 — 또는 DB로 관리합니다 (아래 §9).
3. 그냥 잡니다.

호스트 스케줄러 `study/pipeline.sh watch`가 주기적으로 실행하며, **선 RAG → 후
생성** 순서를 강제합니다:

| 단계 | 처리 | LLM |
|---|---|---|
| Phase A·B 인덱싱 | PDF 파싱(캐시) → 청킹 → BM25 + 벡터 인덱스 → `books/processed/` | **off** |
| Phase C 노트 생성 | `todo` 행마다 검색 → 리랭킹 → llama-server → `notes/` → `draft` | **on → off** (up.sh/down.sh) |

- **상호 배제**: 인덱싱이 남아 있으면(`pending_index>0`) 생성 단계로 진입하지
  않습니다. llama-server는 생성 중에만 로드됩니다 (~30GB 메모리 절약).
- **중단돼도 안전**: 파싱 결과는 `books/markdown/`에 캐시되고, 완료된 주제는
  `draft`로 바뀌므로 재시작하면 이어서 처리합니다.
- **진행 상황**: `study/logs/pipeline.log` + `study/logs/pipeline-watch.log`.
  ```bash
  tail -f study/logs/pipeline.log
  bash study/pipeline.sh status        # pending_index / pending_generate
  ```

## 3. 노트 하나만 즉시 생성

```bash
bash study/pipeline.sh note "Normal distribution" --book prob --section 3.5 --update-topics
```

- llama-server를 자동으로 켰다가(`up.sh`) 끝나면 내립니다(`down.sh`).
- 컨테이너를 직접 쓰려면: `docker compose -f docker/docker-compose.yml run --rm pipeline
  python -u tools/study.py note "Normal distribution" --book prob --section 3.5 --update-topics`
  (이 경우 llama-server는 직접 `../up.sh`로 켜두세요).

- `--section 3.5, 3.6`처럼 여러 섹션 가능 (1순위 후보로 항상 포함).
- `--effort high`: 엄밀함이 필요한 주제는 thinking 강도를 올립니다.
- `--crossref 5`: 교차참조 청크 수 조절 (기본 3).
- `TOPICS.md`에 있는 주제면 `--book/--section` 생략 가능합니다.
- `--problems`: 10+10 문제 세트 + 별도 풀이 파일 생성.

## 4. 동작 모드

호스트 오케스트레이터 `study/pipeline.sh`가 llama-server 생명주기까지 관리합니다:

```bash
bash study/pipeline.sh watch            # index → (pending 없으면) generate, 반복
bash study/pipeline.sh once             # index → (pending 없으면) generate
bash study/pipeline.sh index [--force] [--jobs N]   # Phase A+B만 (LLM off)
bash study/pipeline.sh generate [--book prob]   # Phase C만 (LLM up/down 자동)
bash study/pipeline.sh prefetch         # 모델 다운로드
bash study/pipeline.sh status           # pending_index / pending_generate
bash study/pipeline.sh stop             # watch 중단
```

- **`index --jobs N` (멀티코어)**: 책 여러 권을 동시에 파싱합니다 (PDF 1권당
  docling 모델 ~4-6GB RAM). 64GB에서 LLM off일 때 `--jobs 2` 정도가 안전합니다.
  파싱만 병렬이고, DB 쓰기/임베딩은 단일 프로세스로 직렬 처리됩니다.
  (컨테이너 직접 호출: `python tools/study.py index --jobs 2`)

`LLM_MANAGED=off`로 두면 `pipeline.sh`는 llama-server를 건드리지 않으므로
`./up.sh` / `./down.sh`로 직접 관리합니다.

## 5. 설정 (`study/docker/.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLAMA_BASE_URL` | `http://127.0.0.1:8000/v1` | llama-server 주소 (host 네트워크라 그대로 동작) |
| `LLAMA_API_KEY` | (빈 값) | 서버 `config.env`에 API_KEY 설정 시 필수 |
| `REASONING_EFFORT` | `low` | 근거가 컨텍스트에 있으므로 낮춰도 품질 유지. `medium`/`high`로 조정 가능 |
| `MAX_TOKENS` | `4096` | 노트 1편당 토큰 상한 (thinking 포함). 잘리면 올리세요 |
| `WATCH_INTERVAL_S` | `300` | 스케줄러 반복 주기(초) |
| `LLM_MANAGED` | `on` | `pipeline.sh`가 up.sh/down.sh로 llama-server 생명주기 관리 |
| `WATCHER_ENABLE` | `on` | `worker-up.sh`가 `pipeline.sh watch`를 백그라운드로 기동 |
| `LLM_START_TIMEOUT_S` | `180` | 생성 단계에서 llama-server 기동 대기 최대 시간(초) |
| `DATABASE_URL` | `postgresql://study:study@127.0.0.1:5432/study` | **Dockerized Postgres+pgvector** 백엔드. 비우면 기존 SQLite+Chroma |
| `PG_USER/PG_PASSWORD/PG_DB` | `study` | db 서비스 자격증명 |
| `OMP_NUM_THREADS` | `14` | 파싱/임베딩용 스레드 (llama-server에 여유를 남김) |

- **DB 백엔드 전환**: `DATABASE_URL`을 비우면(기존 SQLite+Chroma) 그대로,
  설정하면 Dockerized Postgres 17 + pgvector(pg_trgm 어휘 검색 + HNSW 벡터)를
  씁니다. `worker-up.sh`가 `db` 서비스를 자동 기동하고, 데이터는 `pgdata`
  볼륨에 유지됩니다. 백엔드를 바꾸면 **재인덱싱이 필요**합니다
  (`study.py reindex --all` 또는 `pipeline.sh index`).
- **이미지/런타임**: Python **3.14** 이미지, 의존성은 `requirements.lock`으로
  고정 (재생성: `uv pip compile requirements.txt -o requirements.lock
  --python-version 3.14`). requirements 변경 시 `./worker-up.sh --build`.

고급: `EMBED_MODEL`, `RERANK_MODEL`, `CHUNK_MAX_CHARS`, `N_CROSSREF` 등은
`tools/rag/config.py`에서 환경변수로 재정의할 수 있습니다.

## 6. 예상 시간 (실측 권장)

| 작업 | 예상 |
|---|---|
| Docling 파싱 | 텍스트 레이어 있는 PDF 기준 페이지당 1~3초 → 800쪽 ≈ 20~40분 |
| 임베딩 | bge-m3 CPU로 1만 청크 ≈ 5~15분 |
| 노트 생성 | 27B Q8_0 약 2 tok/s, effort `low` 기준 편당 약 20~50분 |

8시간 밤이면 **인덱싱 + 노트 10편 내외**가 현실적입니다. 노트가 많으면
다음 날 `bash study/pipeline.sh generate`로 이어서 돌리면 됩니다 (완료분은 자동 건너뜀).

## 7. 품질 원칙

- `study/AGENTS.md` 규칙이 시스템 프롬프트로 주입됩니다 (영어만, 1페이지,
  교재 우선, KaTeX 규칙, draft→review→done).
- 생성 직후 금지 KaTeX 환경(`align`, `equation`, `\bm` 등)을 자동 린트하고
  경고를 남깁니다. `study/tools/check_katex.py`가 있다면 최종 검증에 사용하세요.
- RAG는 오류를 줄이지 제거하지는 않습니다. **`done`은 교재 대조 후에만** 찍으세요.

## 8. 문제 해결

- **`docker: permission denied`** → 로그아웃/로그인 또는 `newgrp docker`.
- **노트가 안 만들어짐** → llama-server가 떠 있는지 확인
  (`curl http://127.0.0.1:8000/health`). 자동 관리는 `bash study/pipeline.sh generate`.
  인덱싱은 서버 없이도 진행됩니다 (`bash study/pipeline.sh index`).
- **PDF가 inbox에 남아 있음** → `logs/pipeline.log`에서 해당 파일 오류 확인.
  스캔본(이미지 PDF)이면 OCR 품질이 낮을 수 있습니다.
- **재색인** → 해당 PDF를 inbox에 다시 넣고
  `bash study/pipeline.sh index --force` 실행.
- **인덱스 초기화** → `rm -rf study/index` 후 `bash study/pipeline.sh index` 재실행
  (markdown 캐시가 있어 재파싱 없이 빠릅니다).
- **전체 초기화** → `study.py reset-all --yes`: registry·인덱스·업로드 PDF·
  노트·로그를 모두 삭제해 최초 설치 상태로 되돌립니다. `AGENTS.md`와
  템플릿, HF 모델 캐시는 유지됩니다. 실행 전 워커 중지 권장: `./worker-down.sh`
- **업로드 PDF만 남기고 초기화** → `bash ../factory-reset.sh`:
  watcher를 정지하고 인덱스·파싱 캐시·그림·노트·로그·registry를 전부 지운 뒤,
  `books/inbox/`(처리됐던 PDF는 다시 inbox로)만 남깁니다. 확인 후
  `bash study/pipeline.sh index`로 처음부터 다시 인덱싱하세요.
- **청킹 규칙 적용** → `python tools/test_chunk.py` +
  `docker compose -f study/docker/docker-compose.yml run --rm pipeline python tools/test_rag.py`로
  검증 후 `python tools/study.py reindex --all`로 전체 재인덱싱 (책당 임베딩 약 1시간).

## 9. TOPICS.md / AGENTS.md를 DB로 관리하기

`TOPICS.md`(구조 데이터)와 `AGENTS.md`·`templates/warmup.md`(프롬프트 문서)는
SQLite 레지스트리 **`study/registry.db`가 원본**이고, 마크다운 파일은 자동으로
내보내는 스냅샷(git에 커밋됨)입니다.

```bash
cd study
python tools/study.py --help              # 전체 명령 보기
python tools/study.py status              # registry + 인덱스 + 진행 상태 한눈에
python tools/study.py reindex <book_id>   # 청킹 로직 변경 후 한 권만 재인덱싱
python tools/study.py reindex --all       # 전부 재인덱싱 (책당 약 1시간, 재파싱 없음)
python tools/study.py reset-all --yes     # DB·인덱스·업로드 PDF·노트·로그 전체 초기화
python tools/test_chunk.py                # 청킹 휴리스틱 테스트 37개
python tools/test_rag.py                  # 핵심 로직 테스트 (store/registry/generate/retrieve/figures)

# 초기화 (첫 실행 시 기존 TOPICS.md/AGENTS.md를 자동으로 DB에 올림)
python tools/study.py init

# 교재 등록
python tools/study.py books add prob --title "Introduction to Probability" --author "Blitzstein"

# 주제 등록 (자동으로 todo 상태 + TOPICS.md 재생성)
python tools/study.py topics add "Normal distribution" --book prob --section 3.5
python tools/study.py topics add "ODE practice" --book prob --section 1.5 --kind problems   # 기초10+중급10 + 풀이 별도

# 조회 / 상태 변경
python tools/study.py topics list --status todo
python tools/study.py topics set "Normal distribution" --status done

# 규칙 문서도 DB에서 관리
python tools/study.py docs set agents --file AGENTS.md   # 파일 -> DB
python tools/study.py docs get agents                    # DB -> 출력
```

- DB가 비어 있으면 파이프라인·CLI 첫 실행 시 마크다운 파일을 자동으로
  가져옵니다 (마이그레이션 불필요).
- 마크다운을 손으로 고쳤다면 `python tools/study.py import`로 DB에 반영하세요.
- 상태 변경(생성 완료 포함) 시 `TOPICS.md`가 자동 재생성되어 git diff로
  진행 상황을 볼 수 있습니다.
- `registry.db`는 gitignore 대상 — 삭제해도 `import`로 복원됩니다.

> **웹 UI/API는 제거되었습니다** (2026-08). PDF는 SCP로 `books/inbox/`에 직접
> 넣고, 에이전트·토픽·상태 관리는 위 `study.py` CLI로 합니다. 노트 조회는
> `notes/` 폴더를 열거나 `study.py status`로 확인합니다.


## 10. 그림·도표 처리 (figure extraction + VLM)

파싱 시 Docling이 그림을 잘라 `books/figures/<book>/fig-*.png` + `figures.json`
(캡션·페이지)로 저장합니다. `VLM_BASE_URL`이 설정되어 있으면 로컬 멀티모달
llama-server에 그림 설명을 요청하고, **캡션이 있는 청크에 `Figure description:`으로
병합**해 텍스트 전용 27B도 그림 내용을 근거로 쓸 수 있게 합니다.

- VLM 없이도 동작: 캡션만으로 인덱싱 (기본).
- VLM 활성화: `.env`에 `VLM_BASE_URL=http://127.0.0.1:<vlm-port>/v1` 설정 후
  파이프라인 재시작. (예: qwen2.5-vl-3b GGUF + mmproj를 두 번째 llama-server로)
- 그림 설명은 그림마다 저장되어 중단돼도 이어서 처리됩니다.
- 해상도는 `DOCLING_IMAGES_SCALE` (기본 2.0, 72 DPI × 배율).

## 11. 수식 LaTeX 변환 + OCR (docling 2.123.1)

docling을 최신(2.123.1)으로 고정하고 **수식 인식(LaTeX 변환)**과 **OCR**을 기본으로 켭니다.

- **수식**: `do_formula_enrichment`로 디스플레이 수식을 LaTeX로 변환합니다.
  기본 모델은 로컬 VLM `CodeFormulaV2`(`codeformulav2` 프리셋) — 첫 파싱 때
  모델을 다운로드합니다 (HF 캐시 볼륨에 저장, 재빌드에도 유지).
  더 가벼운 대안: `DOCLING_FORMULA_PRESET=granite_docling`.
- **OCR**: 텍스트 레이어의 깨진 수식 글리프(예: `f s 2 x d`)를 복구합니다.
  `DOCLING_OCR_MODE=default`(PDF 인지 레이아웃 영역, 빠름) /
  `full_page`(전체 페이지 OCR, 인라인 수식까지 최대 복구, 느림).
  언어는 `DOCLING_OCR_LANG=en` (PP-OCRv6 코드).
- **헤딩 구조**: `DOCLING_HEADING_HIERARCHY=on`이면 PDF 북마크/번호/폰트로
  `#`/`##`/`###` 계층을 추론해 마크다운을 계층적으로 만듭니다. 청킹은
  내용 기반이라 영향 없습니다.
- 수식 모델 + OCR 때문에 파싱이 이전보다 느립니다 (1200쪽 교재 기준 수 시간
  예상 — 밤새 워커 전제). `requirements.txt`가 바뀌었으므로 이미지 재빌드 필요
  (`worker-up.sh --build`).

추가로 RAG 인덱싱 품질을 위해 다음 옵션들도 켭니다:

- **그림 분류** (`do_picture_classification`): 모든 그림을 사진/다이어그램/차트/
  표 등으로 분류 — 저비용, 다운스트림 필터링에 유용 (항상 ON).
- **차트 추출** (`do_chart_extraction`, `DOCLING_CHART_EXTRACTION=on`): bar/pie/
  line 차트를 구조화 데이터로 추출 (그림 분류 자동 활성화). 문제가 되면 `off`.
- **코드 인식** (`do_code_enrichment`, `DOCLING_CODE_ENRICHMENT=on`): 코드 블록
  처리 (수식 모델과 동일 모델 재사용).
- **레이아웃 모델** (`DOCLING_LAYOUT_PRESET`): 기본은 docling 기본(layout-heron,
  균형). 조밀한 교재는 `layout_egret_large`가 더 정확하지만 CPU에서 훨씬 느림.
- **docling 내장 그림 설명** (`do_picture_description`): `DOCLING_PICTURE_DESCRIPTION=on`
  으로 시도 가능. 내장 모델(SmolVLM-256M 기본, `DOCLING_PICTURE_PRESET`로
  `granite_vision`/`pixtral` 선택)이 그림을 설명하고, **파싱 중 `descriptions.json`으로
  수집**돼 기존 `attach_descriptions`로 청크에 "Figure description:"으로 붙습니다.
  **ON이면 자체 VLM(`figures.py`/`vlm.py`)은 건너뜁니다** (중복 방지).
  모드 전환 시 캐시 키가 바뀌므로 `--force` 재파싱이 자동으로 필요합니다.
  `DOCLING_PICTURE_AREA_THRESHOLD`(기본 0.05)로 설명할 그림의 최소 면적(페이지 대비)을
  조절할 수 있습니다.
- 테스트: `python tools/test_rag.py`의 figures/vlm 그룹 (fakes 기반, 네트워크 불필요).

## 12. 청킹 프로파일 (YAML로 책별 조정)

청킹 휴리스틱(노이즈/섹션/챕터/백매터/크기)은 코드에 고정되어 있지 않고
**YAML 프로파일**로 분리되어 있습니다. 코드 수정 없이 YAML만 바꿔 새 교재에
맞출 수 있습니다.

- **기본 프로파일**: `study/config/chunking.yaml` (모든 책에 적용)
- **책별 오버라이드**: `study/config/chunking.<book_id>.yaml`
  (기본 위에 딥머지, 책별 값이 우선)

| 항목 | 설명 |
|---|---|
| `chunk.*` | min/max_chars, overlap, front matter 제거, 작은 청크 병합, 자동 챕터 |
| `sections.*` | 번호 섹션 4형태 (`... 1.1`, `12.3 Title`, `3.5`, `Section 3.5`) |
| `chapters.*` | `CHAPTER 1` / `1 Functions and Models` |
| `backmatter.*` | 답지/해답/부록/색인 → 자체 검색 가능 챕터로 유지 |
| `noise.patterns` | 본문으로 흡수할 노이즈 헤딩 (책/출판사별 퍼니처는 여기에 추가) |
| `noise.short_title_max_chars` 등 | 짧은 제목 / 한 글자 / 기호 전용 처리 |
| `headings.*` | 레벨 폴백 (챕터/섹션) |

- 정규식은 **작은따옴표**로 감싸야 합니다 (`'\s+'`) — 큰따옴표는 YAML이
  `\s`를 처리해 패턴이 깨집니다.
- 모든 패턴은 `re.match`(제목 시작에 고정) + 대소문자 무시로 컴파일됩니다.
- 프로파일을 바꾼 뒤 적용: `python tools/study.py reindex <book_id>` 또는
  `python tools/study.py reindex --all` (markdown 캐시 재사용, 재파싱 없음).
  새 책 인덱싱은 `bash study/pipeline.sh index`.
