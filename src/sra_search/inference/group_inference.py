"""样本分组推断模块

从样本名称中识别分组信息。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


# ============ 分组名称标准化映射 ============

_GROUP_NAME_MAP: dict[str, str] = {
    # Control 组
    "con": "control",
    "ctrl": "control",
    "ctl": "control",
    "cnt": "control",
    "cont": "control",
    "contr": "control",
    "normal": "normal",
    "norm": "normal",
    "wt": "wild-type",
    "wild type": "wild-type",
    "wildtype": "wild-type",
    # 处理组
    "treat": "treatment",
    "treats": "treatment",
    "trt": "treatment",
    "tx": "treatment",
    "drug": "treatment",
    "stim": "stimulated",
    "stimulated": "stimulated",
    "stimulus": "stimulated",
    # 疾病组
    "case": "case",
    "disease": "case",
    "patient": "case",
    "pt": "case",
    "dis": "case",
    # 时间点
    "t0": "time_0h",
    "t1": "time_1h",
    "t2": "time_2h",
    "t3": "time_3h",
    "t4": "time_4h",
    "t6": "time_6h",
    "t8": "time_8h",
    "t12": "time_12h",
    "t24": "time_24h",
    "t48": "time_48h",
    "t72": "time_72h",
    "0h": "time_0h",
    "1h": "time_1h",
    "2h": "time_2h",
    "3h": "time_3h",
    "4h": "time_4h",
    "6h": "time_6h",
    "8h": "time_8h",
    "12h": "time_12h",
    "24h": "time_24h",
    "48h": "time_48h",
    "72h": "time_72h",
    "0d": "day_0",
    "1d": "day_1",
    "2d": "day_2",
    "3d": "day_3",
    "7d": "day_7",
    "14d": "day_14",
    "21d": "day_21",
    "28d": "day_28",
    # 剂量组
    "low": "low_dose",
    "high": "high_dose",
    "med": "medium_dose",
    "medium": "medium_dose",
    "ld": "low_dose",
    "hd": "high_dose",
    "md": "medium_dose",
    # 条件组
    "ko": "knockout",
    "kd": "knockdown",
    "oe": "overexpression",
    " overexpression": "overexpression",
    "mut": "mutant",
    "mutant": "mutant",
    "ki": "knockin",
    "tg": "transgenic",
    "rescue": "rescue",
    # 细胞系
    "a549": "A549",
    "hepg2": "HepG2",
    "hek293": "HEK293",
    "hek293t": "HEK293T",
    "hela": "HeLa",
    "mcf7": "MCF-7",
    "raw264.7": "RAW264.7",
}


def extract_group_token(name: str) -> str:
    """从样本名称中提取分组标记

    处理规则：
    1. 移除常见后缀（数字、_数字、_rep等）
    2. 统一大小写
    3. 返回纯分组标记

    Examples:
        "Con1" -> "con"
        "Con_2" -> "con"
        "Treatment_rep1" -> "treatment"
        "GSM123456" -> "" (无法识别)
    """
    if not name:
        return ""

    # 移除 GSE/GSM/SRP 等前缀
    name = re.sub(r"^(GSE|GSM|SRP|SRR|SRX|ERR)\d*_?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(GSM|SRX|ERR)\d+", "", name, flags=re.IGNORECASE)

    # 移除下划线开头的数字
    name = re.sub(r"^[_\-]?\d+", "", name)

    # 移除末尾的数字和 _rep 等后缀
    name = re.sub(r"(_\d+)+$", "", name)  # _1_2 -> 移除
    name = re.sub(r"_\d+$", "", name)  # _1 -> 移除
    name = re.sub(r"\d+$", "", name)  # 末尾数字 -> 移除
    name = re.sub(r"_rep\d+$", "", name, flags=re.IGNORECASE)  # _rep1 -> 移除
    name = re.sub(r"_r\d+$", "", name, flags=re.IGNORECASE)  # _r1 -> 移除

    # 移除 _GSM 等残留
    name = re.sub(r"^_", "", name)

    return name.lower().strip()


def normalize_group_name(token: str) -> str:
    """将分组标记标准化为规范名称

    Args:
        token: 提取的分组标记

    Returns:
        标准化后的分组名称
    """
    if not token:
        return ""

    # 精确匹配
    if token in _GROUP_NAME_MAP:
        return _GROUP_NAME_MAP[token]

    # 前缀匹配
    for key, value in _GROUP_NAME_MAP.items():
        if token.startswith(key):
            return value

    # 返回原值（首字母大写）
    if token:
        return token.title()

    return ""


def infer_groups(sample_names: list[str]) -> list[dict[str, Any]]:
    """从样本名称列表推断分组信息

    Args:
        sample_names: 样本名称列表，如 ["Con1", "Con2", "Treatment1", "Treatment2"]

    Returns:
        分组列表，如 [{"name": "control", "n": 2}, {"name": "treatment", "n": 2}]
    """
    if not sample_names:
        return []

    # 提取所有 token
    tokens: list[str] = []
    for name in sample_names:
        token = extract_group_token(name)
        if token:  # 只添加非空 token
            tokens.append(token)

    # 统计每个分组的样本数
    counter = Counter(tokens)

    # 构建结果
    groups: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for token, count in counter.most_common():
        normalized = normalize_group_name(token)
        if normalized and normalized not in seen_names:
            groups.append({
                "name": normalized,
                "n": count,
                "original_token": token,
            })
            seen_names.add(normalized)

    return groups


def is_contrast_ready(groups: list[dict[str, Any]]) -> bool:
    """判断是否满足对比分析条件

    对比分析要求：
    1. 至少 2 个分组
    2. 每个分组至少有 2 个样本
    3. 总样本数 >= 4

    Args:
        groups: 分组列表

    Returns:
        是否可以做对比分析
    """
    if len(groups) < 2:
        return False

    if not all(g.get("n", 0) >= 2 for g in groups):
        return False

    total = sum(g.get("n", 0) for g in groups)
    return total >= 4


def infer_groups_from_text(
    overall_design: str = "",
    title: str = "",
    sample_count: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """从文本描述中推断分组信息

    当没有样本名称时，可以从 overall_design 或 title 中尝试推断。

    Args:
        overall_design: 实验设计描述
        title: 数据集标题
        sample_count: 总样本数

    Returns:
        (groups, contrast_ready)
    """
    text = f"{title} {overall_design}".lower()
    groups: list[dict[str, Any]] = []

    # 识别常见的分组模式
    patterns = [
        # "n=3 biological replicates"
        (r"(\w+)\s*[=:]\s*(\d+)\s*(biological|technical)?\s*replicate", _parse_group_from_match),
        # "3 replicates of control"
        (r"(\d+)\s*(replicates?|biological|technical)\s+(?:of\s+)?(\w+)", _parse_group_from_match),
        # "control vs treatment" 或 "treated vs untreated"
        (r"(\w+)\s+vs\s+(\w+)", _parse_group_from_match),
        # "control and treatment"
        (r"(control|ctrl|case|patient|treatment|stimulated)\s+and\s+(\w+)", _parse_group_from_match),
    ]

    for pattern, parser in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            parsed = parser(match)
            if parsed:
                groups.extend(parsed)
                break  # 只用第一个匹配的模式

    # 去重
    unique_groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for g in groups:
        name = g.get("name", "")
        if name and name not in seen:
            unique_groups.append(g)
            seen.add(name)

    # 如果无法从文本推断，尝试从 sample_count 推断
    if not unique_groups and sample_count >= 2:
        # 假设是简单的 case vs control
        if sample_count >= 4:
            half = sample_count // 2
            unique_groups = [
                {"name": "case", "n": half},
                {"name": "control", "n": sample_count - half},
            ]
        else:
            unique_groups = [
                {"name": "unknown", "n": sample_count},
            ]

    contrast_ready = is_contrast_ready(unique_groups)

    return unique_groups, contrast_ready


def _parse_group_from_match(match: re.Match) -> list[dict[str, Any]]:
    """从正则匹配中解析分组信息"""
    groups: list[dict[str, Any]] = []
    parts = [g.lower() for g in match.groups() if g]

    for part in parts:
        if part.isdigit():
            continue
        if part in ["and", "vs", "of", "or", "the", "with", "replicate", "replicates"]:
            continue
        normalized = normalize_group_name(part)
        if normalized:
            groups.append({"name": normalized, "n": 0})  # n 待填充

    return groups
