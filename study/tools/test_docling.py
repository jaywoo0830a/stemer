#!/usr/bin/env python3
"""Smoke test for the docling formula VLM — the create_causal_mask failure.

Validates that the installed transformers can LOAD and RUN the CodeFormulaV2
(Idefics3-based) formula model — the exact component that failed with:

    TypeError: create_causal_mask() got an unexpected keyword argument
    'cache_position'

on an outdated transformers.

Run inside the pipeline container (first run downloads the model into the
HF cache volume, a few minutes):
    docker compose -f study/docker/docker-compose.yml run --rm pipeline \
        python -u tools/test_docling.py

Exit code 0 = the formula VLM loads and runs; non-zero = version mismatch.
"""
from __future__ import annotations

import sys


def main() -> int:
    import docling
    import torch
    import transformers

    print(f"docling      {docling.__version__}")
    print(f"transformers {transformers.__version__}")
    print(f"torch        {torch.__version__}")

    # 1) Structural check — the exact signature the bug hit.
    import inspect
    from transformers.models.idefics3 import modeling_idefics3 as m

    has_cache_position = "cache_position" in inspect.signature(
        m.Idefics3Model.create_causal_mask
    ).parameters
    print(f"idefics3 create_causal_mask(cache_position=...): "
          f"{'OK' if has_cache_position else 'MISSING (too old)'}")
    if not has_cache_position:
        print("FAIL: transformers too old for the CodeFormulaV2 (Idefics3) model.")
        return 1

    # 2) Load CodeFormulaV2 through docling's VLM engine and run one prediction
    #    on a synthetic formula-like image — exercises load + forward pass.
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
