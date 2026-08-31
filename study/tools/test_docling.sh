#!/usr/bin/env bash
# ============================================================
# test_docling.sh — docling 수식 VLM(CodeFormulaV2) 스모크 테스트
#
# 최신 transformers 스택이 CodeFormulaV2(Idefics3 기반)를 로드하고
# 1회 forward pass까지 도는지 컨테이너에서 검증합니다.
# (파이썬은 내장 — 별도 .py 파일 없음)
#
# 사용:  bash study/tools/test_docling.sh
# 종료:  0 = 성공 / 1 = 실패 (버전 불일치 → requirements 조정 후 재빌드)
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # study/
cd "$ROOT/.."                                             # repo root

command -v docker >/dev/null 2>&1 || {
  echo "❌ docker 없음 — 서버에서 실행하세요 (bash study/tools/test_docling.sh)" >&2
  exit 1
}

# 파이썬 스모크 코드를 임시 파일로 만들고 컨테이너에 read-only 마운트해 실행.
TMP="$(mktemp /tmp/test_docling.XXXXXX.py)"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<'PY'
"""docling formula-VLM smoke test (inlined into test_docling.sh)."""
import sys


def main() -> int:
    import docling
    import torch
    import transformers

    print(f"docling      {docling.__version__}")
    print(f"transformers {transformers.__version__}")
    print(f"torch        {torch.__version__}")

    # Diagnostic: where does create_causal_mask live in this transformers?
    # 4.x: Idefics3Model.create_causal_mask (class method)
    # 5.x: transformers.cache_utils.create_causal_mask (module-level)
    # Informational only — the authoritative test is the real model run below.
    import inspect

    found = "?"
    try:
        from transformers.models.idefics3 import modeling_idefics3 as _m

        if hasattr(_m.Idefics3Model, "create_causal_mask"):
            has_cp = "cache_position" in inspect.signature(
                _m.Idefics3Model.create_causal_mask
            ).parameters
            found = f"Idefics3Model.create_causal_mask(cache_position={has_cp})"
    except Exception:
        pass
    if found == "?":
        try:
            import transformers.cache_utils as _cu

            if hasattr(_cu, "create_causal_mask"):
                found = "transformers.cache_utils.create_causal_mask (5.x module-level)"
        except Exception:
            pass
    print(f"create_causal_mask location: {found}")

    from PIL import Image

    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import CodeFormulaVlmOptions
    from docling.models.inference_engines.vlm import VlmEngineInput
    from docling.models.stages.code_formula.code_formula_vlm_model import CodeFormulaVlmModel

    print("Loading CodeFormulaV2 (first run downloads ~0.6GB into HF cache) ...")
    model = CodeFormulaVlmModel(
        enabled=True,
        enable_remote_services=False,
        artifacts_path=None,
        options=CodeFormulaVlmOptions.from_preset("codeformulav2"),
        accelerator_options=AcceleratorOptions(),
    )
    if model.engine is None:
        print("FAIL: CodeFormulaVlmModel did not create a VLM engine.")
        return 1

    img = Image.new("RGB", (320, 48), "white")
    for x in range(0, 320, 8):
        img.putpixel((x, 24), (0, 0, 0))  # a "formula-ish" horizontal line

    try:
        out = model.engine.predict(
            VlmEngineInput(
                image=img,
                prompt="<formula>",
                temperature=0.0,
                max_new_tokens=64,
                stop_strings=list(model.options.model_spec.stop_strings),
                extra_generation_config={"skip_special_tokens": False},
            )
        )
    except Exception:
        print("FAIL: model load or forward pass raised an exception:")
        import traceback

        traceback.print_exc()
        return 1

    print(f"prediction: {out.text!r}")
    print("OK: CodeFormulaV2 loaded and ran a forward pass (formula VLM works).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

echo "== docling formula VLM smoke test =="
docker compose -f "$ROOT/docker/docker-compose.yml" run --rm -T \
  -v "$TMP:/tmp/test_docling.py:ro" \
  pipeline python -u /tmp/test_docling.py
rc=$?

if [[ $rc -eq 0 ]]; then
  echo
  echo "✅ PASS — CodeFormulaV2 (transformers) 모델 정상 동작"
else
  echo
  echo "❌ FAIL — transformers 버전이 CodeFormulaV2와 호환되지 않음 (exit $rc)"
  echo "   requirements.txt 조정 → uv pip compile → ./worker-up.sh --build"
fi
exit "$rc"
