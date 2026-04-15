"""Inference 模块 - 从数据集元数据推断结构化信息

设计原则：
- deterministic 优先（规则引擎）
- LLM 仅兜底（可选）
- 统一 schema（强约束）
- 可缓存、可复现

主要功能：
- 从 title、summary、overall_design 推断 disease、organ、omics_type、granularity
- 样本分组识别
- platform → 技术名映射
- summary_text 自动生成
"""

from sra_search.inference.engine import InferenceEngine, build_dataset_inference
from sra_search.inference.schema import (
    InferenceSchema,
    init_inference_schema,
    merge_metadata_with_inference,
)

__all__ = [
    "InferenceEngine",
    "build_dataset_inference",
    "InferenceSchema",
    "init_inference_schema",
    "merge_metadata_with_inference",
]
