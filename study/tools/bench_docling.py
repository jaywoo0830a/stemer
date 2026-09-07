#!/usr/bin/env python
"""docling 수식 변환(FORMULAS) CPU 벤치마크 — 설정 조합 비교.

사용:
    python tools/bench_docling.py <pdf> <start> <end>
    # 예: python tools/bench_docling.py /books/math/공학수학.pdf 17 20

각 설정 조합을 작은 page 범위(기본 3page)로 돌려 시간과 not-decoded 를 비교한다.
첫 실행은 모델 다운로드로 오래 걸리니 실제 측정 전에 warm-up 이 포함된 조합부터.

환경:
  DOCLING_FORMULAS=1 로 parse_source 를 통해 실제 pipeline 을 그대로 쓴다.
  1회 warm + 각 variant 를 measure.
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("DOCLING_FORMULAS", "1")


def bench(path: str, start: int, end: int, *,
          preset: str, fp32: bool, tables: bool) -> dict:
    env = dict(os.environ)
    env["DOCLING_FORMULA_PRESET"] = preset
    env["DOCLING_FORMULA_FP32"] = "1" if fp32 else "0"
    env["DOCLING_TABLES"] = "1" if tables else "0"
    # 자식 프로세스로 격리해 모델 상태 오염 방지
    import subprocess
    code = (
        "import os,time\n"
        f"os.environ['DOCLING_FORMULAS']='1'\n"
        f"os.environ['DOCLING_FORMULA_PRESET']={preset!r}\n"
        f"os.environ['DOCLING_FORMULA_FP32']={('1' if fp32 else '0')!r}\n"
        f"os.environ['DOCLING_TABLES']={('1' if tables else '0')!r}\n"
        "from study_lib.parse import parse_source\n"
        "t0=time.monotonic()\n"
        f"book=parse_source({path!r},profile='docling',book_id='bench',"
        f"page_range='{start}-{end}')\n"
        "print('ELAPSED',round(time.monotonic()-t0,1))\n"
        "print('NOT_DECODED',book.markdown.count('formula-not-decoded'))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True)
    out = r.stdout
    elapsed = not_decoded = None
    for line in out.splitlines():
        if line.startswith("ELAPSED"):
            elapsed = float(line.split()[1])
        if line.startswith("NOT_DECODED"):
            not_decoded = int(line.split()[1])
    return {"preset": preset, "fp32": fp32, "tables": tables,
            "elapsed": elapsed, "not_decoded": not_decoded,
            "rc": r.returncode, "err": r.stderr[-400:]}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    path, start, end = argv
    # 조합: (preset, fp32, tables)
    variants = [
        ("codeformulav2", True, False),
        ("codeformulav2", True, True),
        ("granite_docling", True, False),
    ]
    print(f"benchmark {path} pages {start}-{end} (3 variants each spin warm first)")
    results = []
    for preset, fp32, tables in variants:
        print(f"\n--- variant preset={preset} fp32={fp32} tables={tables} ---")
        res = bench(path, start, end, preset=preset, fp32=fp32, tables=tables)
        print("  ", res)
        results.append(res)
    print("\n=== ranked by elapsed ===")
    for r in sorted(results, key=lambda x: float(x["elapsed"] or 9e9)):
        print(f"  preset={r['preset']:<16} fp32={r['fp32']} tables={r['tables']} "
              f"-> {r['elapsed']}s not_decoded={r['not_decoded']} rc={r['rc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
