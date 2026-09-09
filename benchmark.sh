#!/usr/bin/env bash
# benchmark.sh — agent 멀티에이전트 로직 예상-동작 벤치마크 (1 Task씩 N건)
#
# 목적: 각 Task가 우리가 설계한 대로 어떤 role/서버로 분배되고, RAG 출처가 붙고,
#       판사(judge)가 어디로 흐르는지·500 없는지 를 한 번에 관찰.
#
# 사용 (서버에서, agent API 8080 가동 후):
#   bash benchmark.sh                    # 기본 케이스 실행
#   BENCH_URL=http://...:8080 bash benchmark.sh
#   BENCH_TASKS=4 bash benchmark.sh      # 앞 4건만
#   BENCH_FILTER=problem bash benchmark.sh   # 'problem' 이 붙은 라벨만
#   BENCH_RAG=store bash benchmark.sh    # 근거 RAG on (기본)
#   BENCH_LOG=/path/to/log bash benchmark.sh  # 로그 파일 경로 지정 (기본: logs/benchmark-YYYYMMDD-HHMMSS.log)
#
# 환경:
#   BENCH_URL    agent API base (기본 http://127.0.0.1:8080)
#   BENCH_RAG    "store"|"" (null) — rag:store 여부 (기본 store)
#   BENCH_TASKS  실행할 케이스 수 (기본 모든 건)
#   BENCH_FILTER 라벨 부분일치로 필터 (선택)
#   BENCH_LOG    로그 파일 경로 (기본 logs/benchmark-YYYYMMDD-HHMMSS.log)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

BENCH_URL="${BENCH_URL:-http://127.0.0.1:8080}"
BENCH_RAG="${BENCH_RAG:-store}"            # 'store' or ''(off)
BENCH_TASKS="${BENCH_TASKS:-}"
BENCH_FILTER="${BENCH_FILTER:-}"
BENCH_LOG="${BENCH_LOG:-$SCRIPT_DIR/logs/benchmark-$(date '+%Y%m%d-%H%M%S').log}"
T_START=$SECONDS                            # 총 소요 시간 측정용

require_cmd curl python3

# 로그 디렉터리 생성 및 출력 리다이렉트
mkdir -p "$(dirname "$BENCH_LOG")"
echo "=== 벤치마크 시작: $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "LOG: $BENCH_LOG"
exec > >(tee -a "$BENCH_LOG") 2>&1

# 각 케이스: "라벨 | plan"
CASES=(
  "explain 개념        | [Task 1: explain] Explain what a vector field is."
  "explain 예제        | [Task 1: explain] Work through the line-integral example in the source the way the book does."
  "derive 증명         | [Task 1: derive] Derive the formula for line integrals from the source step by step."
  "code 함수           | [Task 1: code] Write a small Python function to evaluate a vector field F(x,y)=(P,Q)."
  "problems 생성       | [Task 1: problems] Make 3 concrete problems about vector fields from the source."
  "problems 변형       | [Task 1: problems] Make one variant problem by changing only numbers of an exercise from the source."
  "rag 출처            | [Task 1: explain] What is a conservative vector field?"
  "search 검색         | [Task 1: search] Find the definition of a scalar field."
  "summary 요약        | [Task 1: summary] Summarize the vector-field section in the source."
  "explain 재실행      | [Task 1: explain] Explain what a vector field is, again."
  # ── 허점 검출용 (BENCH_FILTER=hole 로만 실행 가능, 각각 범하기 쉬운 오류 유도) ──
  "hole-과잉증명        | [Task 1: explain] Explain the remainder bound in the source, but also derive a full proof of it so nothing is left to the reader."
  "hole-숫자발명        | [Task 1: explain] Work the line-integral example, and if the source does not print the scalar value, compute it from first principles."
  "hole-없는검색        | [Task 1: search] Find the definition of a scalar field."
  "hole-거짓재현        | [Task 1: quote] Quote exactly, word for word, the source's sentence about path independence, adding no gloss."
  "hole-지어낸API       | [Task 1: code] Write a function using numpy.reshape with axis=-1 to flatten the last dimension."
  "hole-문제숫자만      | [Task 1: problems] Make one variant of a source exercise by changing ONLY its numbers, nothing else."
)

# --- 응답에서 관찰 지표만 추출해 한 줄로 ---
probe() {
  python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("  !!! non-JSON / fail:", sys.stdin.read()[:200])
    sys.exit(0)
if "detail" in d and isinstance(d["detail"], str):
    print("  !!! detail:", d["detail"])
    sys.exit(0)
t = (d.get("tasks") or [{}])[0]
out = t.get("output") or ""
print("  role=%-9s url=%-38s ok=%-6s" % (t.get("role"), t.get("url"), t.get("ok")))
nc = len([s for s in (t.get("sources") or [])])
print("  grounded=%-6s sources=%d judge_role=%s" %
      (t.get("grounded"), nc, t.get("judge_role") or "-"))
note = (t.get("grounding_note") or "").strip()
print("  note=%s" % (note[:110] if note else "(none)"))
err = (t.get("error") or "").strip()
if err:
    print("  error=%s" % err[:110])
'
}

plan_for_rag() {
  local plan="$1"
  if [[ -n "$BENCH_RAG" ]]; then
    python3 -c 'import json,sys; print(json.dumps({"plan":sys.argv[1],"rag":"store"}))' "$plan"
  else
    python3 -c 'import json,sys; print(json.dumps({"plan":sys.argv[1],"rag":None}))' "$plan"
  fi
}

run_case() {
  local label="$1" plan="$2"
  local body
  body="$(plan_for_rag "$plan")"
  echo "===== $label ====="
  local _t0=$SECONDS
  curl -s -X POST "$BENCH_URL/run-plans" \
    -H 'Content-Type: application/json' \
    -d "$body" | probe
  local _el=$((SECONDS - _t0)) _tot=$((SECONDS - T_START))
  printf '  [elapsed %dm %02ds | total %dm %02ds]\n' \
    "$((_el / 60))" "$((_el % 60))" "$((_tot / 60))" "$((_tot % 60))"
  echo
}

step "agent 벤치마크 — $BENCH_URL"
info "rag=${BENCH_RAG:-(off)} tasks=${BENCH_TASKS:-all} filter=${BENCH_FILTER:-(none)}"
echo

req="$BENCH_URL/health"
if curl -sf "$req" >/dev/null 2>&1; then
  info "health OK: agent 응답"
else
  warn "health 응답 없음 — agent API($BENCH_URL) 가 안 떠 있으면 500/연결거부 가능"
fi
echo

n=0
for entry in "${CASES[@]}"; do
  label="${entry%%|*}"
  plan="${entry#*| }"
  if [[ -n "$BENCH_FILTER" ]] && [[ "$label" != *"$BENCH_FILTER"* ]]; then
    continue
  fi
  n=$((n+1))
  if [[ -n "$BENCH_TASKS" ]] && (( n > BENCH_TASKS )); then break; fi
  run_case "$label" "$plan"
done

info "측정 완료. (역할·sources·judge_role·note 를 위 표로 확인)"
T_END=$SECONDS
T_EL=$((T_END - T_START))
info "총 소요: $((T_EL / 60))분 $((T_EL % 60))초 ($T_EL 초) — 실행 case $n 건"
echo "=== 벤치마크 종료: $(date '+%Y-%m-%d %H:%M:%S') | 총 소요: ${T_EL}초 | 케이스: $n건 ==="