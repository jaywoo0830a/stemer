# ROUTINE — 매일 "공부할 부분만 RAG에 쌓고 → 문제·질문" 용도

> 목적: **하루 공부 범위만 조금씩 RAG store에 누적**하고, 그 섹션으로 문제를
> 만들고(에이전트로) 물어보는 흐름. 기존에 넣은 범위는 안 지워지고 누적된다.
> 예시 책 = `미적분`(PDF: `/books/math/미적분.pdf`). 전부 서버 `~/projects/stemer/study`에서.

---

## 핵심 골격 (매일 반복)

```
페이지 범위 선택 → accumulate(누적 인제스트) → topic add → generate / problembank → 질문
```

---

## 0) 오늘 범위만 누적 인제스트 (가장 중요)

교재를 펼쳐 오늘 페이지 범위(`A-B`)를 정한다 → 한 줄로 store에 추가(기존 유지·누적).
```bash
cd ~/projects/stemer/study
DOCLING_FORMULAS=1 DOCLING_FORMULA_FP32=1 HF_ENDPOINT=https://hf-mirror.com \
  bash docker/run.sh accumulate --book 미적분 --pages 759-772 --source /books/math/미적분.pdf
# 성공 메시지: "ingested 미적분: +N chunks" / "now covered intervals: [...] [759,772]"
# 같은 구간 재실행하면 "skip ... already covered" (멱등)
# 다음 페이지 범위도 같은 방식으로 이어서 한 줄 더 (그러면 두 섹션 모두 누적됨)
```
- `--pages A-B`는 PDF 1-based 실제 페이지. 책에서 본 인쇄쪽과 offset이 있으면 보정.
- 수식 LaTeX(docling FORMULAS)는 이 env로 포함. 임베딩은 `bge-m3`(HF) 필요 → `HF_ENDPOINT`에
  유효 scheme 있는 hub(직결 `https://huggingface.co` 또는 미러 `https://hf-mirror.com`) 고정.

## 1) 그 섹션에 토픽 등록

```bash
bash docker/run.sh topics add --book 미적분 --title "11-1 <섹션명>" --section 11.1 --kind exam
# 섹션 여러 개면 반복 (Kind: exam | note | problems)
bash docker/run.sh topics list --book 미적분      # 등록 확인 (topic id = 미적분-11-1 형태)
```
- `--section`은 저장된 청크의 헤딩 번호(예 `11.1`)와 맞춰야 검색이 정확하다.

## 2) 그 토픽으로 문제 / 강의 개념 생성

```bash
bash docker/run.sh generate   --topic 미적분-11-1     # 개념/강의 노트
bash docker/run.sh problembank --topic 미적분-11-1     # 연습문제 세트(개념노트 선행 권장)
```
- 둘 다 특정 토픽 한 건만 상태 무관 동작. 생성물은 `study/data/notes/`에 저장 + lint.

## 3) 질문할 때 (책 store 를 근거로)

방금 accumulate 로 채운 책 청크 근거를 recall 하려면 **agent gateway** 사용.
```bash
curl -s -X POST http://127.0.0.1:8000/run-plans -H 'Content-Type: application/json' \
  -d '{"plan":"[Task 1: explain] <여기에 질문>","rag":"store"}'
```
- 응답 `sources` / `Source anchor`에 방금 넣은 페이지가 보이면 정상적으로 recall 된 것.
- RAG 근거를 켜지 않으면(`rag:null`) store 를 안 씀 → 반드시 `"rag":"store"`.

---

## 매일 체크 한 줄

> 새 페이지 범위 → `accumulate` → 새 `section` 토픽 → `generate`/`problembank` → 질문.

누적된 걸 다시 하면 안 됩니다: `accumulate`가 이미 커버한 범위는 자동 skip이라,
**하루에 한 조각씩만 범위를 앞으로 나아가며** 사용하면 store가 눈에 띄게 커집니다.
