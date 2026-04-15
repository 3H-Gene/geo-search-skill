"""LLM 兜底推断模块

当规则引擎无法推断时，使用 LLM 作为备选方案。
设计为可选模块，仅在规则引擎返回 unknown 时调用。
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger


# LLM 推断的 prompt 模板
LLM_INFERENCE_PROMPT = """你是一个生物信息学数据管理员。

根据以下数据集信息，提取结构化信息：

标题: {title}
摘要: {summary}
实验设计: {overall_design}
平台: {platform}
样本数: {sample_count}

请提取以下信息（如果无法确定，标记为 "unknown"）：
- disease: 疾病名称（如 gout, cancer, diabetes）
- organ: 组织/器官（如 blood, liver, brain）
- omics_type: 组学类型（如 RNA-Seq, ATAC-Seq, Proteomics）
- granularity: 粒度（bulk / single-cell / spatial）
- sample_groups: 样本分组（从样本数推断，如 {{"control": 3, "treatment": 3}}）

请以 JSON 格式返回：
{{"disease": "...", "organ": "...", "omics_type": "...", "granularity": "...", "sample_groups": {{}}}}"""


def llm_infer(
    client: Any,
    title: str,
    summary: str = "",
    overall_design: str = "",
    platform: str = "",
    sample_count: int = 0,
) -> dict[str, Any]:
    """使用 LLM 进行推断

    Args:
        client: LLM 客户端（支持 chat.completions.create 接口）
        title: 数据集标题
        summary: 摘要
        overall_design: 实验设计
        platform: 平台
        sample_count: 样本数

    Returns:
        推断结果字典
    """
    if client is None:
        logger.debug("LLM client 未提供，跳过 LLM 推断")
        return {}

    prompt = LLM_INFERENCE_PROMPT.format(
        title=title or "N/A",
        summary=summary or "N/A",
        overall_design=overall_design or "N/A",
        platform=platform or "N/A",
        sample_count=sample_count or 0,
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500,
        )

        content = response.choices[0].message.content.strip()

        # 尝试解析 JSON
        # 去掉 markdown 代码块
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        result = json.loads(content)

        # 验证和清理结果
        cleaned: dict[str, Any] = {
            "disease": str(result.get("disease", "unknown")).lower(),
            "organ": str(result.get("organ", "unknown")).lower(),
            "omics_type": str(result.get("omics_type", "unknown")),
            "granularity": str(result.get("granularity", "unknown")).lower(),
        }

        # 验证 granularity
        valid_granularities = {"bulk", "single-cell", "single cell", "spatial", "unknown"}
        if cleaned["granularity"] not in valid_granularities:
            cleaned["granularity"] = "unknown"

        # 样本分组
        if "sample_groups" in result:
            cleaned["sample_groups"] = result["sample_groups"]

        cleaned["_llm_source"] = True

        return cleaned

    except json.JSONDecodeError as e:
        logger.warning(f"LLM 返回 JSON 解析失败: {e}")
        return {}
    except Exception as e:
        logger.warning(f"LLM 推断失败: {e}")
        return {}


def should_use_llm_fallback(rule_result: dict[str, Any]) -> bool:
    """判断是否应该使用 LLM 兜底

    当以下字段都为 unknown 或缺失时，使用 LLM：
    - disease
    - organ
    - omics_type
    - granularity

    Args:
        rule_result: 规则引擎的推断结果

    Returns:
        是否需要 LLM 兜底
    """
    unknown_count = 0

    if not rule_result.get("disease"):
        unknown_count += 1

    if not rule_result.get("organ"):
        unknown_count += 1

    if not rule_result.get("omics_type"):
        unknown_count += 1

    # granularity 为 unknown 仍然值得尝试 LLM
    gran = rule_result.get("omics_granularity", "unknown")
    if gran in ("unknown", ""):
        unknown_count += 1

    # 至少 2 个字段为 unknown 时使用 LLM
    return unknown_count >= 2
