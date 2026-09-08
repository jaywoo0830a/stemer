# Prompt Protocol — 결정론적 검증 프롬프트 규약(versioned, 코드 분리)

## 왜 이 문서가 있는가
프롬프트는 "부탁"이 아니라 **검증 파이프라인의 계약**으로 취급한다. LLM 이 source 를
무시하거나/자기 산술로 이기려 하면 오답이 통과되기 때문에, 여기선 **"source 는 무조건
정본(source-as-authority)"** 을 결정론적 원칙으로 세운다. 그리고 이 텍스트를 **코드에서
빼서** `agent/prompts/config.yaml`에 둬서, 시스템을 재빌드 없이(=파일 수정만으로) 튜닝할
수 있게 한다(코드 변경 → 이미지 재빌드 하는 흐름은 같지만 "프롬프트만" 고치는 노력은 감소).

## 버전
- `config.yaml` 의 `version:` 이 프롬프트-스키마 버전.
- 코드(`prompts.py`, `verify.py`, `planner.py`)는 버전에 무관하게 각 키를 읽는다.

---

## 1. 원칙(Precedence) — 우선순위 계층

1. **[SOURCE AUTHORITY]** 검색된 REFERENCE 청크는 그 진술/조건/공식/경계/**인쇄된 숫자·워크드
   결과** 에 대해 **무조건 정본**. worker/coder/reasoner 는 절대 source 를 "틀렸다"고
   교정/덮어쓰지 않는다. (`config.source_is_authority.preamble`)
2. **[Verbatim 재현]** source 가 명시한 것은 기호·한계·조건·값을 **그대로** 재현.
3. **[숫자 정본]** 질문이 구체 수치(예: "몇 개 terms / within 0.0005")를 물으면, source 의
   인쇄 값(예: "need 32 terms")을 답으로. 자체 계산이 다르면 source 값을 답으로, 자체
   계산은 `my calc gave …` 단독 주석으로만.
4. **[Scope]** 라벨(AUTO): python `planner._classify` + `prompts.scope.*` — explain/quote/
   state/apply 일 땐 **과잉 증명 금지**(test#1 회귀), proof/derive 일 때만 유도 허용.
5. **[라벨 Authority]** `[Task N: explain]` 과 같은 사용자 action 은 role 을 결정(explain→
   worker). 본문에 "theorem/series" 가 있어도 explain→reasoner 승격 안 함.
6. **Tier 규칙**: Tier-1(lexical, `grounding.py`) → Tier-2(판사 `verify.py`). 실패 시 교정
   재시도(`grounding.correction_prompt`) ≤ `orchestrator.grounding_retries`. 소진 → UNGROUNDED 표시.

## 2. 누가 무엇을 본다
| 모듈 | 사용 텍스트 키(config) | 역할 |
|---|---|---|
| `prompts.system_prompt` | roles.{worker\|coder\|reasoner}, scope.{proof\|non_proof}, common.absolute | 조립된 system |
| `prompts.user_prompt` | (함수) 질문 verbatim+조건+context | user |
| `verify.LlmVerifier` | judge.system (`prompts.JUDGE_SYSTEM`) | 판사 판정 |
| `planner.PlanParser` | parser.system (`prompts.PARSER_SYSTEM`) | 티켓화 |
| `grounding.lexical_ok` | (함수, 도메인 아님) | Tier1 |
| `grounding.correction_prompt` | 일반 | 교정 |

## 3. config.yaml 키와 의미
```yaml
version: 1
source_is_authority: { preamble: ... }
roles:   {worker: ..., coder: ..., reasoner: ...}
common:  { absolute: ... }      # 모든 역할 공통 "ABSOLUTE RULES"
scope:   { proof: {proof_or_derive: ...}, non_proof: ... }
judge:   { system: ... }
parser:  { system: ... }
```

## 4. 판사 스키마 (verify, v2 — 단일 소스에 정의)
판사 응답은 반드시 JSON:
```json
{"ok": bool, "grounded": bool, "errors": [string], "error_codes": [string],
 "exceptions": [string], "reason": string}
```
- `error_codes` 열거: `missing_source_value`, `mismatch_source_value`,
  `extra_claim`, `ungrounded_statement`, `scope_violation`, `other`.
- `ok=false` 조건: (a) drift/창작, (b) **source-conflict**(reference 의 인쇄 숫자/조건과
  답이 모순/오버라이드 — test#2), (c) 안 물어본 예외/숫자/증명 추가,
  (d) 요구된 source 값을 답이 빠뜨림.
- 판사가 응답 못 하면 `Verdict(ok=true, grounded=true, judge_role, source='deferred')`
  **deferred(명시 통과)** — 오경보 대신 availability 우선.
- 응답 필드/Tier 산출: 각 worker 지시 결과 JSON의 `tasks[].judge_role`, `error_codes[]`,
  `grounded`, `grounding_note` 로 판사가 정말 거부했는지/어느 code 인지 추적 가능(청구 게이트).

## 5. 튜닝 방법(언제든 개선)
1. `agent/prompts/config.yaml` 편집 (프롬프트 문구만 — 프로토콜 데이터를 개선).
2. 테스트: `cd /home/rlawjddn/projects/stemer && study/.venv/bin/python -m pytest agent/tests -q`
3. 서버 반영: `docker build -f docker/agent-gateway.Dockerfile -t agent-gateway:latest . &&
   bash server-down.sh && bash server-up.sh`
4. 그 밖 로직(role 매핑/개수 등)은 코드 담당. 숫자/근거 하드게이트는 `grounding.numeric_anchor_check`
   및 orchestrator의 Tier1+Tier2 재시도(소진 시 UNGROUNDED) 가 책임.

## 6. 구현된 하드 게이트 + 남은 후보
- ✅ **numeric_anchor(구현)**: 질문이 수치/개수 인트이고 source 에 워크드 숫자(예
  'need 32 terms')가 있으면, 답 숫자가 그 source 값과 하나라도 일치하지 않으면
  **결정론적 reject** → 교정 재시도(판사와 무관하게, 판사 꺼도 동작). test#2용 방어 레이어.
- ✅ **판사 평결 관측성**: `TaskOut.judge_role/error_codes/grounded/grounding_note`
  됨 (deferred면 `notewith 'judge unreachable'` 표기 가능).
- 후보: theorem/quote는 worker, 진짜 proof는 reasoner 세분(라벨 기반은 되나, 라벨 없는
  본문 분류만 미세조정 여지). 판사 지연/CPU 는 grounding_retries·디버깅 빈도로 조절.

