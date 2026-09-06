# study — 교재 RAG 공장 (v3, API 생성)

2026-09-06 **전면 재작성**. 계약 우선 TDD: 공개 라이브러리(`study_lib/`)의 동작을
`tests/test_*_contract.py`가 **클라이언트 관점**에서 먼저 고정한다.

설계 문서:
- [`NEW-STRATEGY.md`](NEW-STRATEGY.md) — 아키텍처·실단가
- [`GEN-PROTOCOL.md`](GEN-PROTOCOL.md) — 생성 출력 규약 (100% JSON, 코어+주제 팩)
- [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md) — 실행 계획

이전 구현은 복구 가능하도록 `../study-archive-20260906/` 에 보관했다.

## 테스트

```bash
python3 -m pytest -q        # 계약(contract) 테스트 — 호스트에서 무거운 의존성 없이 실행
```

## 현재 스코프 (TDD 슬라이스)

- `study_lib.registry` — 책/토픽 레지스트리, `todo→draft→review→done` (Slice 0)
- `study_lib.protocol` — GEN-PROTOCOL 스키마·예산·빈 슬롯 검증 (Slice 1)
- `study_lib.chunk` — 마크다운 → 300~500토큰 청크 (헤딩 섹션 경계, max 상한) (Slice 2)
- `study_lib.parse` — TXT/PDF → 마크다운, 파서 프로필(text/fast/docling) (Slice 3)
- `study_lib.store` — RAM 스테이징 → 영속화(JsonDurableSink) → dense/text 검색 (Slice 4)
- `study_lib.embed` — 임베딩 인터페이스 + StubEmbedder(계약) / TransformerEmbedder(bge-m3, 선택 의존성) (Slice 5)
- `study_lib.retrieve` — 토픽(제목·섹션) → 컨텍스트: 섹션 매핑(primary) + RRF 융합·리랭크(crossref) (Slice 6)
- `study_lib.llm` — LLMClient 인터페이스·Usage(실단가 비용)·FlashClient(httpx, 선택 의존성) (Slice 7)
- `study_lib.render` — 검증된 payload → 최종 마크다운 (구조·라벨·번호는 템플릿 소유) (Slice 8)
- `study_lib.figures` — FigureRegistry(교재 원본만)·figures_for(섹션)·attach_figures (Slice 9)
- `study_lib.factory` — generate_one: retrieve→prompt→llm→검증→patch(≤2)→render→저장→draft (Slice 10)
- `study_lib.ingest` — 폴더 배치 인제스트: 등록→파싱→청킹→임베딩→저장, 재개/실패 관리, `--jobs N` 병렬 (Slice 12)
- `study_lib.discover` — 인덱스 섹션 → 자동 todo 토픽 (`topics discover --book`) (Slice 13)
- `study_lib.lint` — KaTeX 린트(금지 env/매크로), factory 생성물에 자동 적용
- `study_lib.profiles` — 청크 프로필 레지스트리(compact/long) → 책 단위 연결
- `study_lib.cli` — 얇은 CLI 래퍼: books/topics/status/index/ingest/generate (Slice 11)
- `study_lib.tokens` — 공용 토큰 추정 척도

## CLI

```bash
python -m study_lib.cli books add --id calc --title Calculus --subject math
python -m study_lib.cli topics add --book calc --title "Limit of a sequence" --section 3.5
python -m study_lib.cli index book.md --book calc --profile text --embedder stub
python -m study_lib.cli status
python -m study_lib.cli generate --book calc

# 사전 준비 — PDF 폴더 일괄 인제스트 (스킵/재시도/병렬)
python -m study_lib.cli ingest books/math --subject math --profile fast --jobs 4
# 인덱스된 책의 섹션에서 토픽 자동 생성 → generate 로 직결
python -m study_lib.cli topics discover --book calc
```

## Docker (서버 배포)

서버에서 전부 도커로 띄우려면 [`DOCKER.md`](DOCKER.md) 를 참고:

```bash
mkdir -p books/math data && cp /path/*.pdf books/math/
EMBED=1 bash docker/build.sh          # 임베딩 포함 빌드
bash docker/run.sh ingest /books/math --subject math --profile fast --jobs 4
```
