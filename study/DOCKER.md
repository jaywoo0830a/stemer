# 서버 도커 기동 (DOCKER)

서버에서 study v3 전체를 Docker 로 띄운다. 데이터는 바인드 마운트로 호스트에 남는다.

## 0. 전제
- Docker Engine (서버) 또는 Docker Desktop + **WSL 배포판 통합 활성화**.
- 이미지 안에서 실행되므로 로컬 파이썬/의존성 불필요.

## 1. 디렉터리 준비
```bash
cd study
mkdir -p books/math data          # PDF 폴더 + 영속 데이터
cp /path/*.pdf books/math/
```

## 2. 이미지 빌드 (서버 실사용 = 임베딩 포함)
```bash
EMBED=1 bash docker/build.sh      # 또는 docker compose build --build-arg EMBED=1
```

## 3. 실행 — 얇은 래퍼
```bash
bash docker/run.sh status                                     # books/topics/pending
bash docker/run.sh ingest /books/math --subject math --profile fast --jobs 4
bash docker/run.sh topics discover --book stewart
bash docker/run.sh generate --book stewart                   # Flash 키 필요
```
- 입력 PDF: 호스트 `study/books/math/` → 컨테이너 `/books/math`.
- 영속 데이터: 호스트 `study/data/` → 컨테이너 `/data` (registry.json / store/ / notes/).
- env: `DEEPSEEK_API_KEY`/`DEEPSEEK_MODEL` 을 shell 에 export 하면 compose 가 넘겨줌.

## 4. 내부 테스트(스모크)
```bash
docker compose run --rm study python -m pytest -q
```

## 5. 운영
- 데이터(registry/store/notes)는 전부 `study/data/` 에 있음 → 백업/이관 용이.
- 코드 변경 시: 이미지에 코드가 포함되므로 `bash docker/build.sh` 재빌드 필요.
  (개발 중 잦은 변경이면 `docker-compose.yml` 에 `- ../study_lib:/app/study_lib` 를 추가해
  bind 로 코드를 마운트하면 재빌드 없이 반영.)
- 임베딩 미포함(EMBED=0) 이미지면 `auto` 임베더가 stub 으로 폴백 → 검색 품질 낮음.
