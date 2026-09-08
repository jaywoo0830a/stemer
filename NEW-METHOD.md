## 최적화된 역할별 모델 배치 및 출력 토큰 정책

> **설계 원칙:**  
> - **큰 모델(14B)** = **판정/검증 전용** (출력 토큰 억제, 결정적 판단만)  
> - **작은 모델(7B)** = **생성/분석 전용** (출력 품질을 높이기 위한 프롬프트 강화)  
> - **모든 출력은 JSON/GBNF로 구조화** → 불필요한 장문 생성을 차단

---

## 1. 역할별 모델 배치표 (64GB RAM, On-Demand)

| 역할 | 모델 | 파라미터 | 메모리(예상) | 출력 토큰 상한 | 출력 형식 |
|------|------|----------|--------------|----------------|-----------|
| **파서 (Parser)** | Qwen2.5-7B-Instruct (Q4_K_M) | 7B | ~5GB | `max_tokens=200` | JSON 스키마 강제 |
| **워커 (Worker)** × 4 | Qwen2.5-7B-Instruct (Q4_K_M) | 7B | 각 ~5GB | `max_tokens=500` | JSON (summary, source) |
| **코더 (Coder)** × 4 | Qwen2.5-Coder-7B-Instruct (Q4_K_M) | 7B | 각 ~5GB | `max_tokens=1200` | JSON + 코드 블록 |
| **문제 생성기 (Setter)** | Qwen2.5-14B-Instruct (Q5_K_M) | 14B | ~11GB | `max_tokens=1800` | 구조화된 문제 + Sympy 코드 |
| **판사 (Judge)** | DeepSeek-R1-Distill-Qwen-14B (Q5_K_M) | 14B | ~11GB | `max_tokens=20` | `{ok, code}` 초경량 JSON |
| **추론가 (Reasoner)** | DeepSeek-R1-Distill-Qwen-7B (Q4_K_M) | 7B | ~5GB | `max_tokens=2000` (증명은 자유 텍스트) | 최종 결론은 `{result: ...}` |
| **임베더 (Embedder)** | bge-small-en-v1.5 | 0.1B | ~0.5GB | 출력 없음 | 벡터만 반환 |

**동시 로딩 전략:**
- **평상시:** Parser(7B) 1 + Worker(7B) 2 → 약 15GB
- **문제 생성 시:** Setter(14B) 1 → 약 11GB (다른 모델 내리고 단독 사용)
- **검증 시:** Judge(14B) 1 → 약 11GB (단독 사용)
- **딥 다이브 시:** Reasoner(7B) 1 + Coder(7B) 2 → 약 15GB

이렇게 하면 **14B 모델 2개를 동시에 띄우지 않아도** 되므로 메모리 압박 없이 운용할 수 있습니다.

---

## 2. 출력 토큰 억제를 위한 GBNF 문법 정의

### 2.1 판사(Judge) 전용 초경량 GBNF

```gbnf
root   ::= "{\"ok\":" bool ",\"code\":\"" code "\"}"
bool   ::= "true" | "false"
code   ::= "OK" | "ERR_SOURCE" | "ERR_LOGIC" | "ERR_SYMPY" | "ERR_STRUCT" | "ERR_META"
```

**적용:** `llama.cpp` 서버 호출 시 `grammar` 필드에 위 문자열을 전달.  
**효과:** 판사는 어떤 경우에도 **20토큰 이내**로 출력이 제한됩니다.

### 2.2 파서(Parser) 전용 GBNF

```gbnf
root   ::= "{\"tasks\":[" task ("," task)* "]}"
task   ::= "{\"id\":" number ",\"action\":\"" action "\",\"target\":\"" string "\",\"input\":\"" string "\",\"role\":\"" role "\"}"
action ::= "explain" | "proof" | "code" | "fix" | "review" | "search" | "summary" | "deep"
role   ::= "worker" | "coder" | "reasoner"
number ::= [0-9]+
string ::= [^"]*
```

**효과:** 파서가 엉뚱한 장문 대신 정확한 JSON 배열만 반환합니다.

### 2.3 워커(Worker) 전용 GBNF

```gbnf
root ::= "{\"summary\":\"" string "\",\"source\":\"" string "\"}"
string ::= [^"]*
```

**효과:** 워커가 요약과 출처만 반환하도록 강제.  
(필요에 따라 `source`를 `null` 허용하려면 `string`에 `null`을 추가 가능)

---

## 3. 출력 정확도 향상을 위한 프롬프트 전략

### 3.1 작은 모델(7B)의 출력 정확도 높이기
작은 모델은 생성 능력이 부족할 수 있으므로, **프롬프트에 상세한 지침과 예시를 추가**하여 출력 품질을 보정합니다.

**예: 워커(Worker) 프롬프트 추가 규칙**
```yaml
worker: >
  ...
  OUTPUT FORMAT: Return a JSON object with keys "summary" and "source".
  "summary" must be 2-4 sentences, no more than 80 words, in English.
  "source" must be the exact filename/section from the reference chunks used.
  Do not include any other text outside the JSON.
  Example:
  {"summary": "The integral vanishes because sin is odd over symmetric limits.",
   "source": "advanced_eng_math_ch11.pdf, p.482"}
```

**예: 코더(Coder) 프롬프트 추가 규칙**
```yaml
coder: >
  ...
  OUTPUT FORMAT: JSON object with keys "root_cause" (string, max 50 words),
  and "patch" (string, containing exactly one fenced code block).
  Do not add explanations outside the JSON.
  Example:
  {"root_cause": "IndexError caused by accessing arr[i+1] when i is last index.",
   "patch": "```python\nfor i in range(len(arr)-1):\n    ...\n```"}
```

### 3.2 큰 모델(14B)의 출력 정확도 높이기
큰 모델은 판정만 하므로, **입력(근거)을 정확히 주고 출력을 제한**하는 방식으로 정확도를 확보합니다.

**판사(Judge) 프롬프트 예시**
```yaml
judge_light: >
  You are a deterministic verifier. Given the QUESTION, REFERENCE, and CANDIDATE,
  output ONLY: {"ok": true/false, "code": "..."}.
  Codes: OK, ERR_SOURCE, ERR_LOGIC, ERR_SYMPY, ERR_STRUCT, ERR_META.
  No explanation. No prose.
```

---

## 4. 이 배치의 기대 효과

| 항목 | 기존 | 개선 후 |
|------|------|---------|
| 판사 출력 토큰 | 200~300 | **10 이하** |
| 판사 응답 시간 | 5~10초 | **1초 이내** |
| Setter 정확도 | 7B 한계 | **14B로 향상** |
| 병렬 처리 부하 | 10개 모델 상시 로드 | **필요할 때만 로드** |
| 메모리 여유 | 30GB+ | **40GB+ 여유** |

---

## 5. 실제 설정 방법

### 5.1 Ollama 사용 시
```bash
# 모델 다운로드
ollama pull qwen2.5:14b-instruct-q5_K_M
ollama pull deepseek-r1:14b-distill-qwen-14b-q5_K_M

# GBNF 문법 적용은 API 호출 시에
curl http://localhost:11434/api/chat -d '{
  "model": "deepseek-r1:14b",
  "messages": [...],
  "format": "json",
  "options": {"num_predict": 20, "grammar": "..."}
}'
```

### 5.2 llama.cpp 사용 시
```bash
# 서버 실행 (판사용)
./llama-server -m deepseek-r1-14b-q5_k_m.gguf \
  --threads 16 --ctx-size 2048 --mlock \
  --grammar-file judge.gbnf \
  --port 8082
```

---

## 결론

이 배치는 **"큰 모델은 판정자, 작은 모델은 일꾼"**이라는 원칙을 극단적으로 적용한 것입니다.  
- 14B 모델은 논리적 일관성 검사만 수행하고 출력 토큰을 10개 이하로 유지  
- 7B 모델은 실질적인 콘텐츠 생성(문제, 코드, 요약)을 담당하되, 엄격한 출력 형식과 프롬프트로 품질 보정  

이렇게 하면 **CPU 환경에서도 1~2초 내 검증, 30초 내 문제 생성**이 가능하며, 정확도는 14B의 논리력으로 보장됩니다.

원하시면 이 배치를 적용한 **`config.yaml` 전체 수정본**이나, **FastAPI 오케스트레이터 코드 예시**를 제공해 드리겠습니다.