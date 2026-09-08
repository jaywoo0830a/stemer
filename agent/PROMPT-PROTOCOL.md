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

## 4. 판사 스키마 (verify)
판사 응답은 반드시 JSON:
```json
{"ok": bool, "grounded": bool, "errors": [str], "exceptions": [str], "reason": str}
```
- `ok=false` 가 되는 조건: (a) 근거 drift/창작, (b) **source-conflict** — reference 의
  인쇄 사실/숫자와 답이 모순/오버라이드 (test#2), (c) 답이 안 물어본 예외/숫자/증명 추가.
- 판사 서버가 응답 못 하면 `Verdict(ok=true, grounded=true, reason='judge unreachable …')`
  로 **deferred**(하드 차단 대신 명시) — 오경보보다 availability 우선이지만 로그로 확인.

## 5. 튜닝 방법(언제든 개선)
1. `agent/prompts/config.yaml` 편집 (프롬프트 문구만).
2. 테스트: `cd /home/rlawjddn/projects/stemer && study/.venv/bin/python -m pytest agent/tests -q`
3. 서버 반영: `docker build -f docker/agent-gateway.Dockerfile -t agent-gateway:latest . &&
   bash server-down.sh && bash server-up.sh`
4. 그 밖 로직(role 매핑, 숫자 앵커 하드차단, 재시도 횟수)은 코드가 담당 — 문서 하단 참조.

## 6. 알려진 한계/다음 개선 후보(결정 사항 아님)
- **숫자 self-check 를 판사 단독에 의존**: 현재는 판사 프롬프트에 "인쇄 숫자와 대조" 규칙.
  더 단단히 하려면 `numeric_anchor` 하드 gate(청크에 "need 32 terms" 류가 있을 때 최종
  숫자 불일치 자동 reject) 후보.
- 공식/Theorem 요청은 worker(짧·인용) vs 진짜 proof 는 reasoner — 라벨 기반으로 잘 되나,
  본문만 있고 라벨 없을 때 분류 미세 조정 가능.
- 판사 지연/CPU: grounding_retries·삽입 판사 빈도를 조절.
