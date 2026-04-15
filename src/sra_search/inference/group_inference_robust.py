"""健壮的样本分组推断模块

从样本名称中识别分组信息，支持多种模式：
- 前缀模式（Con1/GR2/GS3）
- 分隔符模式（control_1/treated_2）
- 时间序列（before/after/day）
- 兜底fallback

目标：
- 覆盖 GEO 中 90% 常见样本命名模式
- 保持 deterministic（不依赖 LLM）
- 输出带 confidence（可控）
- 严格不做过度语义推断
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


# ========================
# 工具函数
# ========================


def normalize(s: str) -> str:
    """标准化字符串：小写 + 去除首尾空白"""
    return s.strip().lower()


def strip_numeric_suffix(s: str) -> str:
    """移除末尾的数字后缀和分隔符"""
    # 先移除数字，再移除末尾的分隔符
    s = re.sub(r"\d+$", "", s)
    # 移除末尾的分隔符（下划线、横杠、点等）
    s = re.sub(r"[_\-.]+$", "", s)
    return s


def split_by_delimiters(s: str) -> list[str]:
    """按常见分隔符拆分字符串"""
    return re.split(r"[_\-. ]+", s)


# ========================
# Pattern 1: 前缀模式（最常见）
# Con1 / GR2 / GS3
# ========================


def pattern_prefix(samples: list[str]) -> dict[str, Any] | None:
    """识别前缀模式样本分组

    规则：
    - 样本名必须以字母开头，后面跟着数字（如 Con1, GR2, GSM123）
    - 数字后不能有其他有意义的文本部分
    - 统计唯一前缀
    """
    tokens = []
    for s in samples:
        s_norm = normalize(s)
        # 检查是否匹配 prefix+number 模式：字母开头 + 数字结尾
        # 例如：Con1, GR2, GSM123, Patient1
        # 不匹配：control_1 (有下划线), patient1_before (有额外部分)
        match = re.match(r"^([a-z]+)(\d+)$", s_norm)
        if match:
            tokens.append(match.group(1))
        else:
            tokens.append(s_norm)  # 保留原值，counter会计为单独的组

    counter = Counter(tokens)

    # 检查：如果所有token都一样（单组），不认为是 prefix 模式
    if len(counter) == 1:
        return None

    # 如果产生的组数和样本数相同（每个样本都是独立的），不是有效的 prefix 模式
    if len(counter) == len(samples):
        return None

    # 必须至少有2个不同的组
    if len(counter) >= 2:
        valid_tokens = {k: v for k, v in counter.items() if k}
        if len(valid_tokens) >= 2:
            return {
                "groups": valid_tokens,
                "method": "prefix",
                "confidence": 0.95,
            }
    return None


# ========================
# Pattern 2: 分隔符模式
# control_1 / treated_2 / disease_PBMC_1
# ========================


def pattern_delimiter(samples: list[str]) -> dict[str, Any] | None:
    """识别分隔符模式样本分组

    规则：
    - 按 _-. 拆分样本名
    - 取第一个非空部分作为组标识
    - 必须至少有2个样本能被明确分到组
    - 如果大部分样本都无法拆分，不认为是 delimiter 模式
    """
    tokens = []
    split_groups = []  # 记录能拆分出来的组

    for s in samples:
        parts = split_by_delimiters(normalize(s))
        if len(parts) > 1 and parts[0]:
            tokens.append(parts[0])
            split_groups.append(parts[0])
        else:
            # 无法拆分的样本，保留原值
            tokens.append(normalize(s))

    # 如果能拆分的样本比例太低，不认为是 delimiter 模式
    split_ratio = len(split_groups) / len(samples) if samples else 0
    if split_ratio < 0.5:
        return None

    counter = Counter(tokens)

    # 必须至少有2个不同的组
    if len(counter) >= 2:
        valid_tokens = {k: v for k, v in counter.items() if k}
        if len(valid_tokens) >= 2:
            return {
                "groups": valid_tokens,
                "method": "delimiter",
                "confidence": 0.85,
            }
    return None


# ========================
# Pattern 3: 时间序列（弱识别）
# patient1_before / patient1_after / day1 / week2
# ========================

TIME_KEYWORDS = [
    "before",
    "after",
    "pre",
    "post",
    "baseline",
    "day",
    "week",
    "month",
    "hour",
    "minute",
    "0h",
    "1h",
    "2h",
    "3h",
    "6h",
    "12h",
    "24h",
    "48h",
    "72h",
    "0d",
    "1d",
    "2d",
    "3d",
    "7d",
    "14d",
    "21d",
    "28d",
]


def pattern_timecourse(samples: list[str]) -> dict[str, Any] | None:
    """识别时间序列样本分组

    规则：
    - 检测样本名中是否包含时间关键词
    - 所有样本必须都能识别到时间点
    - 返回时间点分组
    """
    tokens = []

    for s in samples:
        s_norm = normalize(s)
        found = None

        # 优先匹配完整时间关键词
        for kw in TIME_KEYWORDS:
            # 边界匹配，避免误匹配
            pattern = r"(^|[_\-.\s])" + re.escape(kw) + r"($|[_\-.\s\d])"
            if re.search(pattern, s_norm):
                found = kw
                break

        # 如果没找到，尝试匹配 t0/t1 等简化格式
        if not found:
            time_match = re.search(r"t(\d+)", s_norm)
            if time_match:
                found = f"t{time_match.group(1)}"

        tokens.append(found or "unknown")

    counter = Counter(tokens)

    # 必须有至少2个不同的时间点，且没有unknown
    if len(counter) >= 2 and "unknown" not in counter:
        return {
            "groups": counter,
            "method": "timecourse",
            "confidence": 0.7,
        }
    return None


# ========================
# Pattern 4: replicate fallback（兜底）
# 无明显分组时使用
# ========================


def pattern_single_group(samples: list[str]) -> dict[str, Any]:
    """兜底模式：所有样本视为一个组"""
    return {
        "groups": Counter({"all": len(samples)}),
        "method": "fallback",
        "confidence": 0.3,
    }


# ========================
# 主入口函数
# ========================


def infer_groups_robust(sample_names: list[str]) -> dict[str, Any]:
    """健壮的样本分组推断主函数

    按优先级尝试多种模式，返回最佳匹配结果。

    Args:
        sample_names: 样本名称列表，如 ["Con1", "Con2", "Treatment1", "Treatment2"]

    Returns:
        分组结果字典，包含：
        - groups: 分组列表 [{"name": str, "n": int}, ...]
        - method: 识别方法（prefix/delimiter/timecourse/fallback）
        - confidence: 置信度 (0.0-1.0)
        - design: 设计类型（single-group/binary/multi-group）
        - contrast_ready: 是否可以做差异分析
    """
    if not sample_names:
        return _format_output(pattern_single_group([]))

    patterns: list[callable] = [
        pattern_prefix,
        pattern_delimiter,
        pattern_timecourse,
    ]

    for fn in patterns:
        result = fn(sample_names)
        if result:
            return _format_output(result)

    return _format_output(pattern_single_group(sample_names))


# ========================
# 输出格式化
# ========================


def _format_output(result: dict[str, Any]) -> dict[str, Any]:
    """格式化分组推断结果"""
    counter: Counter = result["groups"]

    groups = [
        {"name": k, "n": v}
        for k, v in counter.items()
    ]

    # 设计类型判断
    if len(groups) == 1:
        design = "single-group"
    elif len(groups) == 2:
        design = "binary"
    else:
        design = "multi-group"

    # 是否可做差异分析
    # 要求：至少2组，每组至少2个样本
    contrast_ready = (
        len(groups) >= 2 and
        all(g["n"] >= 2 for g in groups)
    )

    return {
        "groups": groups,
        "method": result["method"],
        "confidence": result["confidence"],
        "design": design,
        "contrast_ready": contrast_ready,
    }


# ========================
# 向后兼容：保持原有接口
# ========================


def extract_group_token(name: str) -> str:
    """从样本名称中提取分组标记（兼容旧接口）"""
    if not name:
        return ""

    # 移除 GSE/GSM/SRP 等前缀
    name = re.sub(r"^(GSE|GSM|SRP|SRR|SRX|ERR)\d*_?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(GSM|SRX|ERR)\d+", "", name, flags=re.IGNORECASE)

    # 移除下划线开头的数字
    name = re.sub(r"^[_\-]?\d+", "", name)

    # 移除末尾的数字和 _rep 等后缀
    name = re.sub(r"(_\d+)+$", "", name)
    name = re.sub(r"_\d+$", "", name)
    name = re.sub(r"\d+$", "", name)
    name = re.sub(r"_rep\d+$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"_r\d+$", "", name, flags=re.IGNORECASE)

    # 移除 _GSM 等残留
    name = re.sub(r"^_", "", name)

    return name.lower().strip()


def normalize_group_name(token: str) -> str:
    """将分组标记标准化为规范名称（兼容旧接口）"""
    if not token:
        return ""

    # 分组名称映射
    group_map: dict[str, str] = {
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
        "treated": "treatment",
        # 疾病组
        "case": "case",
        "disease": "case",
        "patient": "case",
        "pt": "case",
        "dis": "case",
        "disorder": "case",
        # 实验条件
        "ko": "knockout",
        "kd": "knockdown",
        "oe": "overexpression",
        " overexpression": "overexpression",
        "mut": "mutant",
        "mutant": "mutant",
        "ki": "knockin",
        "tg": "transgenic",
        "rescue": "rescue",
        "ko": "knockout",
        "crispr": "crispr",
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
        # 分组标识
        "gr": "group",
        "gs": "group",
        "group": "group",
        "grp": "group",
    }

    # 精确匹配
    if token in group_map:
        return group_map[token]

    # 前缀匹配
    for key, value in group_map.items():
        if token.startswith(key):
            return value

    # 返回原值（首字母大写）
    if token:
        return token.title()

    return ""


def infer_groups(sample_names: list[str]) -> list[dict[str, Any]]:
    """从样本名称列表推断分组信息（兼容旧接口）

    Args:
        sample_names: 样本名称列表

    Returns:
        分组列表，如 [{"name": "control", "n": 2}, {"name": "treatment", "n": 2}]
    """
    if not sample_names:
        return []

    # 使用新的健壮推断
    result = infer_groups_robust(sample_names)

    # 标准化分组名称
    groups: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for g in result["groups"]:
        token = extract_group_token(g["name"])
        normalized = normalize_group_name(token)
        if normalized and normalized not in seen_names:
            groups.append({
                "name": normalized,
                "n": g["n"],
                "original_token": token,
            })
            seen_names.add(normalized)

    return groups


def infer_groups_from_text(
    overall_design: str = "",
    title: str = "",
    sample_count: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """从文本描述中推断分组信息（兼容旧接口）"""
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
                break

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
            groups.append({"name": normalized, "n": 0})

    return groups


def is_contrast_ready(groups: list[dict[str, Any]]) -> bool:
    """判断是否满足对比分析条件

    对比分析要求：
    1. 至少 2 个分组
    2. 每个分组至少有 2 个样本
    3. 总样本数 >= 4
    """
    if len(groups) < 2:
        return False

    if not all(g.get("n", 0) >= 2 for g in groups):
        return False

    total = sum(g.get("n", 0) for g in groups)
    return total >= 4
