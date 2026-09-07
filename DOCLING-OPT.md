Docling의 VLM 수식 인식을 CPU에서 처리할 때 속도를 극적으로 높이려면 **추론 엔진 최적화**, **파이프라인 설정 조정**, **스마트한 라우팅 전략**을 함께 적용해야 합니다.

아래는 실제 효과가 검증된 주요 최적화 방법들입니다.

### 1. 🔧 멀티스레딩 설정 (가장 기본적이고 효과적)
Docling이 CPU 코어를 충분히 활용하지 못하는 경우가 많으므로, 사용 가능한 모든 코어를 할당해야 합니다.

```python
from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice

# CPU 코어 수에 맞게 조정 (예: 8코어)
accel_options = AcceleratorOptions(
    num_threads=8,                # CPU 스레드 수 증가
    device=AcceleratorDevice.CPU  # CPU 사용 명시
)
```

### 2. ⚡ 추론 엔진 교체 (가장 극적인 효과)
기본 PyTorch 대신 **ONNX Runtime**을 사용하면 CPU 성능이 **2~5배** 향상되고 메모리 사용량은 **60~80%** 줄어듭니다.

*   **ONNX 모델 사용**: `lamco-development/granite-docling-258M-onnx` 모델을 사용하세요.
*   **벤치마크**: 동일 CPU에서 PyTorch(2.5초) → ONNX(0.8초)로 **3.1배 빨라졌습니다**.

### 3. 🎯 파이프라인 경량화 (선택적 활성화)
VLM은 파이프라인 전체 시간의 **약 58%**를 차지하는 주요 병목입니다. 꼭 필요한 기능만 켜야 합니다.

```python
from docling.datamodel.pipeline_options import PdfPipelineOptions

pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = False           # 텍스트 PDF면 OCR 끄기 (가장 큰 효과)
pipeline_options.do_formula_enrichment = True  # 수식만 활성화
pipeline_options.do_table_structure = False    # 테이블 불필요시 비활성화
pipeline_options.do_code_enrichment = False    # 코드 불필요시 비활성화
```

### 4. 🤖 VLM 라우팅 전략 (고급)
모든 페이지에 VLM을 적용하지 말고, **일반 파이프라인으로 먼저 처리한 후 문제가 있는 페이지만 VLM으로 재처리**하는 전략입니다.

*   **효과**: VLM 호출 횟수를 최소화하여 전체 처리 시간을 대폭 단축합니다.
*   **적용 대상**: 그림이 많거나 OCR 신뢰도가 낮은 페이지 등에만 선택적으로 사용.

### 5. ⚙️ 기타 고급 최적화
*   **메모리 관리**: `page_batch_size=1`로 설정해 한 번에 한 페이지씩 처리하면 메모리 부하를 줄일 수 있습니다.
*   **배치 크기 조절**: `elements_batch_size` 기본값은 5입니다. 무작정 증가시켜도 속도 향상은 미미하고 메모리만 더 사용하니 주의하세요.
*   **토큰 생성 루프 방지**: `code_formula_predictor.py`에 루프 감지 로직을 추가해 불필요한 지연을 방지할 수 있습니다.

---

### 💎 종합 정리: CPU 최적화 로드맵

1.  **1단계 (기본)**: `num_threads`를 CPU 코어 수에 맞게 증가시킵니다.
2.  **2단계 (효율)**: OCR이 필요 없는 텍스트 PDF라면 `do_ocr=False`로 끕니다.
3.  **3단계 (성능)**: PyTorch 대신 **ONNX Runtime 모델**로 교체합니다 (가장 큰 성능 향상).
4.  **4단계 (고급)**: 모든 페이지가 아닌 **선별적인 VLM 라우팅**을 도입합니다.

이 방법들을 순서대로 적용하면 CPU 환경에서도 VLM 수식 인식 속도를 극적으로 개선할 수 있습니다.