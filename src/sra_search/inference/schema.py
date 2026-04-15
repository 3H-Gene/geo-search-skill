"""Inference Schema - 统一的推断结果 Schema 定义

提供强约束的数据结构，确保推断结果的一致性和可复用性。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InferenceSchema:
    """数据集推断结果 Schema

    设计说明：
    - 所有推断字段都有默认值，避免 None
    - granularity 必须尽量不为 unknown
    - samples.groups 记录样本分组信息
    - study_design.contrast_ready 标记是否可以做对比分析
    """

    # 基础标识
    dataset_id: str = ""
    title: str = ""

    # 生物上下文
    biological_context: dict[str, str] = field(default_factory=lambda: {
        "disease": "unknown",
        "organ": "unknown",
    })

    # 组学信息
    omics: dict[str, str] = field(default_factory=lambda: {
        "omics_type": "unknown",
        "granularity": "unknown",  # bulk / single-cell / spatial
    })

    # 样本信息
    samples: dict[str, Any] = field(default_factory=lambda: {
        "total": 0,
        "groups": [],  # [{"name": "control", "n": 3}]
    })

    # 研究设计
    study_design: dict[str, Any] = field(default_factory=lambda: {
        "contrast_ready": False,
    })

    # 技术平台
    platform: dict[str, str] = field(default_factory=lambda: {
        "raw": "",      # 原始平台名（如 GPL12345）
        "mapped": "",    # 映射后的技术名（如 Illumina NovaSeq 6000）
        "category": "",  # 平台类别（如 Sequencing, Microarray）
    })

    # 推断置信度
    confidence: dict[str, float] = field(default_factory=lambda: {
        "disease": 0.0,
        "organ": 0.0,
        "omics_type": 0.0,
        "granularity": 0.0,
    })

    # 推断来源
    sources: dict[str, str] = field(default_factory=lambda: {
        "disease": "",
        "organ": "",
        "omics_type": "",
        "granularity": "",
        "platform": "",
    })

    # 自动生成的摘要
    summary_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于 JSON 序列化）"""
        return {
            "dataset_id": self.dataset_id,
            "title": self.title,
            "biological_context": self.biological_context,
            "omics": self.omics,
            "samples": self.samples,
            "study_design": self.study_design,
            "platform": self.platform,
            "confidence": self.confidence,
            "sources": self.sources,
            "summary_text": self.summary_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferenceSchema:
        """从字典创建实例"""
        return cls(
            dataset_id=data.get("dataset_id", ""),
            title=data.get("title", ""),
            biological_context=data.get("biological_context", {}),
            omics=data.get("omics", {}),
            samples=data.get("samples", {}),
            study_design=data.get("study_design", {}),
            platform=data.get("platform", {}),
            confidence=data.get("confidence", {}),
            sources=data.get("sources", {}),
            summary_text=data.get("summary_text", ""),
        )


def init_inference_schema(dataset_id: str, title: str) -> InferenceSchema:
    """初始化一个空的推断 Schema

    Args:
        dataset_id: 数据集 ID（如 GSE123456）
        title: 数据集标题

    Returns:
        带有默认值的新 InferenceSchema 实例
    """
    schema = InferenceSchema()
    schema.dataset_id = dataset_id
    schema.title = title
    return schema


def merge_metadata_with_inference(
    record: dict[str, Any],
    inference: InferenceSchema,
) -> dict[str, Any]:
    """合并元数据记录和推断结果

    优先级：已有元数据 > 推断结果 > 默认值

    Args:
        record: 原始数据集记录（DatasetRecord.to_dict()）
        inference: 推断结果

    Returns:
        合并后的字典
    """
    result = copy.deepcopy(record)

    # 生物上下文
    if not result.get("disease") or result.get("disease") == "unknown":
        result["disease"] = inference.biological_context.get("disease", "unknown")
        result["_inference_disease"] = inference.confidence.get("disease", 0.0)

    if not result.get("organ") or result.get("organ") == "unknown":
        result["organ"] = inference.biological_context.get("organ", "unknown")
        result["_inference_organ"] = inference.confidence.get("organ", 0.0)

    # 组学信息
    if not result.get("omics_type") or result.get("omics_type") == "unknown":
        result["omics_type"] = inference.omics.get("omics_type", "unknown")

    if not result.get("omics_granularity") or result.get("omics_granularity") == "unknown":
        result["omics_granularity"] = inference.omics.get("granularity", "unknown")

    # 平台映射
    if result.get("platform") and not inference.platform.get("mapped"):
        inference.platform["raw"] = result["platform"]

    # 样本分组
    if inference.samples.get("groups"):
        result["_inferred_groups"] = inference.samples["groups"]
        result["_contrast_ready"] = inference.study_design.get("contrast_ready", False)

    # 推断摘要
    if inference.summary_text:
        result["_inference_summary"] = inference.summary_text

    return result
