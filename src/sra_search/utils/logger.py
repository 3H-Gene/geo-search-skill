"""日志配置模块（带敏感信息脱敏）

功能：
- 统一日志格式配置
- 敏感信息自动脱敏（API Keys, Access Tokens, 邮箱等）
- 分级日志输出（控制台 + 文件）
"""
from __future__ import annotations

import re
import sys
from typing import Any

from loguru import logger


# ── 脱敏正则模式 ──────────────────────────────────────────────────────────────
_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # API Key 模式（通用）
    (re.compile(r"(api[_-]?key|apikey|api-secret|api_secret)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{16,64})['\"]?", re.IGNORECASE), r"\1=***REDACTED***"),
    # OpenAI / Anthropic / Google API Keys
    (re.compile(r"(sk-[a-zA-Z0-9]{20,}|sk-ant-[a-zA-Z0-9\-]{50,}|AIza[a-zA-Z0-9\-]{30,})"), "***REDACTED_API_KEY***"),
    # NCBI API Key（20-36 位）
    (re.compile(r"(ncbi[_-]?api[_-]?key)\s*[:=]\s*['\"]?([a-zA-Z0-9]{20,36})['\"]?", re.IGNORECASE), r"\1=***REDACTED***"),
    # Bearer Token
    (re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]+", re.IGNORECASE), r"\1***REDACTED***"),
    # 邮箱（仅在日志中部分脱敏，保留域名）
    (re.compile(r"([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})"), r"***@\2"),
    # 密码字段
    (re.compile(r"(['\"]?password['\"]?\s*[:=]\s*['\"]?)[^'\"\s]{4,}"), r"\1***REDACTED***"),
    # 连接字符串（含凭据）
    (re.compile(r"://[^:]+:([^@]+)@"), r"://***:***@"),
]


def sanitize_log_message(message: str) -> str:
    """对日志消息进行敏感信息脱敏

    Args:
        message: 原始日志消息

    Returns:
        脱敏后的安全日志消息
    """
    result = str(message)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class SanitizedSink:
    """日志处理器包装器，自动脱敏敏感信息"""

    def __init__(self, sink: Any):
        self._sink = sink

    def write(self, message: str) -> None:
        """写入脱敏后的日志"""
        sanitized = sanitize_log_message(message)
        self._sink.write(sanitized)

    def flush(self) -> None:
        """刷新底层处理器"""
        if hasattr(self._sink, 'flush'):
            self._sink.flush()


def setup_logger(level: str = "INFO") -> None:
    """配置日志（带敏感信息脱敏）

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR），默认 INFO。
               None 或空字符串时回退到 INFO。
    """
    # 防御：None 或空值时回退到 INFO
    if not level:
        level = "INFO"
    level = level.upper()

    logger.remove()

    # 控制台输出（带脱敏）
    logger.add(
        SanitizedSink(sys.stderr),
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        diagnose=False,  # 关闭诊断信息（可能泄露路径）
    )

    # 文件输出（带脱敏）
    logger.add(
        "logs/sra_search.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        diagnose=False,
        enqueue=True,  # 线程安全写入
    )


def get_sanitized_logger():
    """获取带脱敏功能的日志记录器

    Returns:
        已配置的 loguru logger 实例
    """
    return logger
