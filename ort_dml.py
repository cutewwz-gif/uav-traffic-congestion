"""DirectML / ONNX Runtime 辅助：强制 Ultralytics 使用 AMD GPU 加速。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Union

import torch

# 优先 DirectML（AMD RX 6600S 等），回退 CPU
ORT_PROVIDERS: List[str] = ["DmlExecutionProvider", "CPUExecutionProvider"]


def get_ort_providers() -> List[str]:
    """返回当前环境可用的 ORT providers（DML 优先）。"""
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    chosen = [p for p in ORT_PROVIDERS if p in available]
    if not chosen:
        chosen = ["CPUExecutionProvider"]
    return chosen


def patch_ultralytics_onnx_for_directml() -> str:
    """Monkey-patch Ultralytics ONNXBackend，使其优先使用 DirectML。

    Returns:
        实际选用的主 provider 名称。
    """
    from ultralytics.nn.backends.onnx import ONNXBackend
    from ultralytics.utils import LOGGER

    providers = get_ort_providers()
    primary = providers[0]

    def load_model(self, weight: Union[str, Path]) -> None:  # type: ignore[no-untyped-def]
        import onnxruntime

        LOGGER.info(f"Loading {weight} for ONNX Runtime inference (DirectML-aware)...")
        available = onnxruntime.get_available_providers()
        use_providers = [p for p in ORT_PROVIDERS if p in available] or ["CPUExecutionProvider"]

        LOGGER.info(
            f"Using ONNX Runtime {onnxruntime.__version__} with "
            f"providers={use_providers}"
        )

        self.session = onnxruntime.InferenceSession(
            str(weight), providers=use_providers
        )
        self.output_names = [x.name for x in self.session.get_outputs()]

        metadata_map = self.session.get_modelmeta().custom_metadata_map
        if metadata_map:
            self.apply_metadata(dict(metadata_map))

        self.dynamic = isinstance(self.session.get_outputs()[0].shape[0], str)
        self.fp16 = "float16" in self.session.get_inputs()[0].type
        # DirectML 下关闭 CUDA IO binding
        self.use_io_binding = False
        self.device = torch.device("cpu")

        # 兼容后续 forward 可能访问的属性
        if not hasattr(self, "net"):
            self.net = None

    ONNXBackend.load_model = load_model  # type: ignore[method-assign]
    return primary
