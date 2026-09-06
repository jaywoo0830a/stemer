# 서버 인덱싱 기동 (DEPLOY)

대상: 9700X(8코어)/64GB Linux 서버. study_lib 를 올려 PDF 교재 수십 권을 인덱싱한다.

> 모든 `python -m study_lib.cli ...` 는 **`study/` 디렉터리에서** 실행(모듈 발견).
> 경로 기본값: registry.json / store/ / notes/ (cwd 기준). env `STUDY_REGISTRY/STUDY_STORE/STUDY_NOTES` 로 변경.

## 0. 전제
- Python 3.10+ (3.12 권장), pip, 네트워크.
- PDF 는 **텍스트 레이어** 보유(현대 수학 교재 대부분). 스캔본은 `docling` 프로필(무겁고 느림).

## 1. 환경 준비
```bash
cd study
python3 -m venv .venv && . .venv/bin/activate
pip install -U pip
pip install -r requirements.txt          # core (pypdf)
pip install -r requirements-embed.txt    # 실제 임베딩 bge-m3 (torch 포함, 수 GB)
python3 -m pytest -q                     # 104 passed 확인
```

## 2. PDF 준비 & 1권 스모크
```bash
mkdir -p books/math && cp /path/*.pdf books/math/
# 파일명 → book id 자동('Stewart Calculus 8e.pdf' → 'stewart-calculus-8e')

python -m study_lib.cli books add --id stewart --title "Stewart Calculus" \
    --subject math --source "books/math/Stewart.pdf"
python -m study_lib.cli index "books/math/Stewart.pdf" --book stewart --profile fast
python -m study_lib.cli status
```
- 스모크는 `--embedder stub` 도 동작(파이프라인 검증용)하지만 검색 품질 낮음 → 실제는 임베딩 설치 후.
- 파서 확인: `python -c "import pypdf"`. 스캔 PDF면 `"use profile=docling"` 안내가 나옴.

## 3. 폴더 배치 인덱싱 (재개·병렬)
```bash
python -m study_lib.cli ingest books/math --subject math --profile fast --jobs 4
```
- `--jobs 4`: 워커마다 임베딩 모델 로드(~1-2GB) → 64GB 에서 `jobs 2~4` 권장.
- **멱등/재개**: 재실행하면 이미 `indexed` 책은 스킵, `failed` 책은 재시도.
- 실패 사유는 stderr(`failed <book>: <사유>`) + `Book.error`에 기록.
- 책별 청크 프로필: `books add --chunk-profile compact` 또는 `ingest --chunk-profile compact|long`.

## 4. 토픽 자동 생성 (섹션 → todo)
```bash
export STUDY_REGISTRY=registry.json STUDY_STORE=store
for b in $(python -m study_lib.cli books list | awk '{print $1}'); do
  python -m study_lib.cli topics discover --book "$b"
done
python -m study_lib.cli status     # pending_topics=N
```

## 5. 생성 (선택 — Flash API 키 필요)
```bash
export DEEPSEEK_API_KEY=sk-...      # 본인 터미널에서 직접
export DEEPSEEK_MODEL=deepseek-flash
python -m study_lib.cli generate
```

## 6. 운영 팁
- **메모리**: 인덱싱 jobs×모델 ~ jobs×1-2GB + Docling 미사용 시 여유 큼. 상시 상주 프로세스 없음.
- **품질 게이트 임계값**: `study_lib/parse.MIN_CHARS_PER_PAGE`(기본 150) — 페이지당 문자 수가 이보다 적으면 스캔 의심.
- **pgvector(선택)**: `store.PgDurableSink` 존재. CLI 기본은 jsonl(책별 파일) — 우선 그대로 운영 후 필요 시 전환.
- 상태 확인/로그: `python -m study_lib.cli status`(books/topics/pending), store 파일 `store/<book>.jsonl`.
