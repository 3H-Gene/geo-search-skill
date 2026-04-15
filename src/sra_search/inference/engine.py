"""Inference 主引擎 - 整合规则引擎、样本分组和 LLM 兜底

核心功能：
1. 规则引擎优先（deterministic）
2. LLM 兜底（可选）
3. 样本分组识别
4. summary_text 自动生成
5. 与 DatasetRecord 合并
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from sra_search.inference.group_inference_robust import (
    infer_groups,
    infer_groups_robust,
    infer_groups_from_text,
    is_contrast_ready,
)
from sra_search.inference.llm_fallback import llm_infer, should_use_llm_fallback
from sra_search.inference.rule_engine import (
    infer_platform,
    query_gpl_platform,
    rule_infer,
)
from sra_search.inference.schema import InferenceSchema, init_inference_schema


class InferenceEngine:
    """推断引擎

    使用方式：
    ```python
    engine = InferenceEngine()
    result = engine.infer(
        dataset_id="GSE123456",
        title="Single-Cell RNA-Seq of PBMC in Gout Patients",
        summary="...",
        overall_design="...",
        platform="Illumina NovaSeq",
        sample_names=["Con1", "Con2", "Treat1", "Treat2"],
    )
    ```
    """

    def __init__(self, llm_client: Any = None, use_llm_fallback: bool = False):
        """初始化推断引擎

        Args:
            llm_client: LLM 客户端（可选）
            use_llm_fallback: 是否使用 LLM 兜底（默认 False，保守策略）
        """
        self.llm_client = llm_client
        self.use_llm_fallback = use_llm_fallback

    def infer(
        self,
        dataset_id: str,
        title: str,
        summary: str = "",
        overall_design: str = "",
        platform: str = "",
        sample_names: list[str] | None = None,
        sample_count: int = 0,
    ) -> InferenceSchema:
        """执行推断

        Args:
            dataset_id: 数据集 ID
            title: 数据集标题
            summary: 摘要
            overall_design: 实验设计描述
            platform: 平台信息
            sample_names: 样本名称列表
            sample_count: 样本总数（当没有 sample_names 时使用）

        Returns:
            InferenceSchema 推断结果
        """
        schema = init_inference_schema(dataset_id, title)

        # ============ 1. 规则引擎推断 ============
        rule_result = rule_infer(
            title=title,
            summary=summary,
            overall_design=overall_design,
            platform=platform,
        )

        # 填充推断结果
        if rule_result.get("disease"):
            schema.biological_context["disease"] = rule_result["disease"]
            schema.confidence["disease"] = rule_result.get("disease_confidence", 0.0)
            schema.sources["disease"] = "rule_engine"

        if rule_result.get("organ"):
            schema.biological_context["organ"] = rule_result["organ"]
            schema.confidence["organ"] = rule_result.get("organ_confidence", 0.0)
            schema.sources["organ"] = "rule_engine"

        if rule_result.get("omics_type"):
            schema.omics["omics_type"] = rule_result["omics_type"]
            schema.confidence["omics_type"] = rule_result.get("omics_confidence", 0.0)
            schema.sources["omics_type"] = "rule_engine"

        if rule_result.get("omics_granularity"):
            schema.omics["granularity"] = rule_result["omics_granularity"]
            schema.confidence["granularity"] = rule_result.get("granularity_confidence", 0.0)
            schema.sources["granularity"] = "rule_engine"

        # 平台映射
        mapped_name = rule_result.get("platform_mapped", "")
        platform_category = rule_result.get("platform_category", "")

        # 如果规则引擎无法映射，尝试查询 GPL ID
        if not mapped_name or mapped_name == platform:
            gpl_name = query_gpl_platform(platform)
            if gpl_name and gpl_name != platform:
                mapped_name = gpl_name
                platform_category = "Sequencing"  # 假设大多数 GPL 是测序平台
                # 尝试进一步标准化
                inferred_name, inferred_cat = infer_platform(mapped_name)
                if inferred_name != platform:
                    mapped_name = inferred_name
                    platform_category = inferred_cat

        if mapped_name:
            schema.platform = {
                "raw": platform,
                "mapped": mapped_name,
                "category": platform_category,
            }
            schema.sources["platform"] = "gpl_query" if "gpl" in str(schema.sources) else "rule_engine"

        # ============ 2. LLM 兜底（可选）============
        if self.use_llm_fallback and self.llm_client:
            if should_use_llm_fallback(rule_result):
                logger.debug(f"[Inference] 使用 LLM 兜底推断 {dataset_id}")
                llm_result = llm_infer(
                    client=self.llm_client,
                    title=title,
                    summary=summary,
                    overall_design=overall_design,
                    platform=platform,
                    sample_count=sample_count or len(sample_names or []),
                )

                if llm_result:
                    # 填充 LLM 推断结果（仅当规则引擎未命中时）
                    if not rule_result.get("disease") and llm_result.get("disease"):
                        schema.biological_context["disease"] = llm_result["disease"]
                        schema.confidence["disease"] = 0.5  # LLM 置信度降低
                        schema.sources["disease"] = "llm_fallback"

                    if not rule_result.get("organ") and llm_result.get("organ"):
                        schema.biological_context["organ"] = llm_result["organ"]
                        schema.confidence["organ"] = 0.5
                        schema.sources["organ"] = "llm_fallback"

                    if not rule_result.get("omics_type") and llm_result.get("omics_type"):
                        schema.omics["omics_type"] = llm_result["omics_type"]
                        schema.confidence["omics_type"] = 0.5
                        schema.sources["omics_type"] = "llm_fallback"

                    if rule_result.get("omics_granularity") == "unknown" and llm_result.get("granularity"):
                        schema.omics["granularity"] = llm_result["granularity"]
                        schema.confidence["granularity"] = 0.5
                        schema.sources["granularity"] = "llm_fallback"

        # ============ 3. 样本分组推断（使用健壮模块）============
        group_result: dict[str, Any] = {
            "groups": [],
            "method": "none",
            "confidence": 0.0,
            "design": "unknown",
            "contrast_ready": False,
        }

        if sample_names:
            # 使用健壮推断（支持 prefix/delimiter/timecourse 模式）
            group_result = infer_groups_robust(sample_names)
        else:
            # 从文本推断
            groups, _ = infer_groups_from_text(
                overall_design=overall_design,
                title=title,
                sample_count=sample_count,
            )
            group_result["groups"] = groups
            group_result["method"] = "text_fallback"
            group_result["contrast_ready"] = is_contrast_ready(groups)

        schema.samples = {
            "total": len(sample_names) if sample_names else sample_count,
            "groups": group_result["groups"],
            "group_method": group_result["method"],
            "group_confidence": group_result["confidence"],
        }

        # ============ 4. 对比分析准备 ============
        schema.study_design["contrast_ready"] = group_result["contrast_ready"]
        schema.study_design["design"] = group_result["design"]

        # ============ 5. 推断摘要生成 ============
        schema.summary_text = _generate_summary_text(schema)

        return schema


def build_dataset_inference(
    dataset_id: str,
    title: str,
    summary: str = "",
    overall_design: str = "",
    platform: str = "",
    sample_names: list[str] | None = None,
    sample_count: int = 0,
    llm_client: Any = None,
    use_llm_fallback: bool = False,
) -> InferenceSchema:
    """便捷函数：从数据集信息构建推断结果

    Args:
        dataset_id: 数据集 ID
        title: 标题
        summary: 摘要
        overall_design: 实验设计
        platform: 平台
        sample_names: 样本名称
        sample_count: 样本数
        llm_client: LLM 客户端
        use_llm_fallback: 是否使用 LLM 兜底

    Returns:
        InferenceSchema

    Example:
        ```python
        from sra_search.inference import build_dataset_inference

        result = build_dataset_inference(
            dataset_id="GSE217561",
            title="Single-Cell RNA sequencing reveals blood immune features of gout remission patients",
            summary="We performed scRNA-seq on PBMC from gout patients...",
            sample_names=["Con1", "Con2", "Con3", "GR1", "GR2", "GR3"],
        )
        print(result.omics["granularity"])  # "single-cell"
        print(result.biological_context["disease"])  # "Gout"
        ```
    """
    engine = InferenceEngine(
        llm_client=llm_client,
        use_llm_fallback=use_llm_fallback,
    )
    return engine.infer(
        dataset_id=dataset_id,
        title=title,
        summary=summary,
        overall_design=overall_design,
        platform=platform,
        sample_names=sample_names,
        sample_count=sample_count,
    )


def _generate_summary_text(schema: InferenceSchema) -> str:
    """自动生成推断摘要

    基于推断的结构化信息生成人类可读的摘要文本。

    Args:
        schema: 推断结果

    Returns:
        摘要文本
    """
    parts: list[str] = []

    # 疾病和器官
    disease = schema.biological_context.get("disease", "unknown")
    organ = schema.biological_context.get("organ", "unknown")

    if disease != "unknown" and organ != "unknown":
        parts.append(f"{disease} {organ} dataset")
    elif disease != "unknown":
        parts.append(f"{disease} dataset")
    elif organ != "unknown":
        parts.append(f"{organ} dataset")

    # 组学类型和粒度
    omics_type = schema.omics.get("omics_type", "unknown")
    granularity = schema.omics.get("granularity", "unknown")

    if omics_type != "unknown":
        if granularity != "unknown" and granularity != "bulk":
            parts.append(f"{granularity} {omics_type}")
        else:
            parts.append(omics_type)

    # 平台
    mapped_platform = schema.platform.get("mapped", "")
    if mapped_platform:
        parts.append(f"using {mapped_platform}")

    # 样本信息
    total = schema.samples.get("total", 0)
    groups = schema.samples.get("groups", [])

    if total > 0:
        if groups:
            group_desc = ", ".join([f"{g['name']} (n={g['n']})" for g in groups[:3]])
            if len(groups) > 3:
                group_desc += f" +{len(groups) - 3} more"
            parts.append(f"{total} samples: {group_desc}")
        else:
            parts.append(f"{total} samples")

    # 对比分析
    if schema.study_design.get("contrast_ready"):
        parts.append("suitable for comparative analysis")

    return "; ".join(parts) if parts else "Dataset with limited metadata"
