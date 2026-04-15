"""输入校验与配置校验模块

职责：
- NCBI 配置校验（邮箱格式、API Key 格式）
- 用户输入校验（搜索词长度、非法字符过滤）
- 统一校验错误类

设计原则：
- 启动时强制校验关键配置
- 输入过滤防止 API 400 错误
- 校验失败应明确提示解决方案
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 邮箱正则（RFC 5322 简化版）
_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# NCBI API Key 正则（通常为 32 位字母数字）
_API_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9]{20,36}$")

# 搜索词最大长度
MAX_QUERY_LENGTH = 1000

# 非法字符（易导致 NCBI API 400）
_FORBIDDEN_CHARS_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# 医学/生物学常用符号黑名单（NCBI 不友好）
_SUSPICIOUS_CHARS = ["\u2018", "\u2019", "\u201c", "\u201d", "\u2014", "\u2013", "\u00a0"]  # ’ "" — – NBSP


@dataclass
class ValidationResult:
    """校验结果"""
    is_valid: bool
    errors: list[str] = None
    warnings: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def format_message(self) -> str:
        """格式化错误/警告消息"""
        parts = []
        if self.errors:
            parts.append("错误:")
            for e in self.errors:
                parts.append(f"  - {e}")
        if self.warnings:
            parts.append("警告:")
            for w in self.warnings:
                parts.append(f"  - {w}")
        return "\n".join(parts)


def validate_ncbi_config(email: str | None, api_key: str | None) -> ValidationResult:
    """校验 NCBI 配置

    Args:
        email: NCBI 注册邮箱
        api_key: NCBI API Key（可选）

    Returns:
        ValidationResult: 校验结果

    说明：
        - 无邮箱：视为无效（NCBI 要求）
        - 有邮箱但无 API Key：警告（速率限制较低）
        - 有邮箱和 API Key：最佳配置
    """
    result = ValidationResult(is_valid=True)

    # 1. 邮箱必填校验
    if not email:
        result.add_error(
            "NCBI 邮箱未配置！\n"
            "  NCBI 要求所有 E-utilities 请求提供联系邮箱。\n"
            "  解决方案：\n"
            "    1. 设置环境变量: export SRA_SEARCH_NCBI_EMAIL='your@email.com'\n"
            "    2. 或运行: sra-search config set ncbi_email your@email.com"
        )
        return result

    # 2. 邮箱格式校验
    if not _EMAIL_PATTERN.match(email):
        result.add_error(
            f"NCBI 邮箱格式无效: {email!r}\n"
            "  期望格式: user@example.com\n"
            "  解决方案：检查邮箱拼写，设置正确的环境变量或配置"
        )
        return result

    # 3. API Key 可选，但有则校验格式
    if api_key:
        if not _API_KEY_PATTERN.match(api_key):
            result.add_warning(
                f"NCBI API Key 格式可能不正确: {api_key[:8]}...\n"
                "  通常应为 20-36 位字母数字。\n"
                "  如验证失败，请访问 https:// NCBI 官网 检查您的 API Key。"
            )

    # 4. 最佳实践警告
    if not api_key:
        result.add_warning(
            "未配置 NCBI API Key，速率限制为 3 次/秒。\n"
            "  建议申请免费 API Key 以提升到 10 次/秒：\n"
            "  1. 访问 https://www.ncbi.nlm.nih.gov/account/\n"
            "  2. 注册/登录后创建 API Key\n"
            "  3. 设置: export SRA_SEARCH_NCBI_API_KEY='your_key_here'"
        )

    return result


def validate_query_input(query: str) -> ValidationResult:
    """校验用户搜索输入

    Args:
        query: 原始搜索词

    Returns:
        ValidationResult: 校验结果

    说明：
        - 过滤不可见字符（ASCII 控制字符）
        - 替换特殊 Unicode 符号为 ASCII 等价
        - 警告过长查询
    """
    result = ValidationResult(is_valid=True)

    if not query or not query.strip():
        result.add_error("搜索词不能为空")
        return result

    # 1. 长度校验
    if len(query) > MAX_QUERY_LENGTH:
        result.add_error(
            f"搜索词过长 ({len(query)}/{MAX_QUERY_LENGTH} 字符)\n"
            f"  NCBI API 对超长查询支持有限。\n"
            f"  建议：将长查询拆分为多个短词组合。"
        )

    # 2. 不可见字符过滤
    if _FORBIDDEN_CHARS_PATTERN.search(query):
        result.add_warning("检测到不可见字符，已自动过滤")

    # 3. 特殊 Unicode 符号警告
    for char in _SUSPICIOUS_CHARS:
        if char in query:
            result.add_warning(
                f"检测到特殊符号 {char!r}，已自动替换为 ASCII 等价符号\n"
                f"  原始查询可能因编码问题导致 NCBI API 返回 400 错误。"
            )

    return result


def sanitize_query(query: str) -> str:
    """清理搜索词，移除/替换可能导致问题的字符

    Args:
        query: 原始搜索词

    Returns:
        清理后的安全搜索词
    """
    # 移除不可见字符
    text = _FORBIDDEN_CHARS_PATTERN.sub("", query)

    # 替换特殊 Unicode 符号
    replacements = {
        "\u2018": "'",  # '
        "\u2019": "'",  # '
        "\u201c": '"',  # "
        "\u201d": '"',  # "
        "\u2014": "-",  # —
        "\u2013": "-",  # –
        "\u00a0": " ",  # NBSP
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # 规范化空白字符
    text = re.sub(r"\s+", " ", text).strip()

    return text


def validate_api_key_format(key: str) -> bool:
    """快速校验 API Key 格式是否合法

    Args:
        key: API Key 字符串

    Returns:
        True if format looks valid
    """
    if not key:
        return False
    return bool(_API_KEY_PATTERN.match(key))
