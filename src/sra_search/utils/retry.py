"""指数退避重试机制

支持抖动（jitter）避免惊群效应。
HTTP 429 触发限速器退避，其他错误触发指数退避。
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from loguru import logger

from sra_search.config import get_settings
from sra_search.utils.rate_limiter import get_global_limiter

F = TypeVar("F", bound=Callable[..., Any])


class RetryError(Exception):
    """重试耗尽后抛出的异常"""

    def __init__(self, message: str, attempts: int, last_error: Exception | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def calculate_delay(
    attempt: int,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter: float = 0.3,
) -> float:
    """计算带抖动的指数退避延迟

    Args:
        attempt: 当前尝试次数（从 0 开始）
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        jitter: 抖动系数（0-1），实际延迟 = calculated_delay * (1 ± jitter)

    Returns:
        实际等待时间（秒）
    """
    calculated = min(base_delay * (2 ** attempt), max_delay)
    jitter_range = calculated * jitter
    actual = calculated + random.uniform(-jitter_range, jitter_range)
    return max(0.1, actual)


def with_retry(
    max_attempts: int | None = None,
    base_delay: float | None = None,
    max_delay: float | None = None,
    jitter: float | None = None,
    retryable_exceptions: tuple = (Exception,),
    on_429: str = "backoff",  # "backoff" | "raise"
) -> Callable[[F], F]:
    """重试装饰器

    Args:
        max_attempts: 最大重试次数（默认从配置读取）
        base_delay: 基础延迟（默认从配置读取）
        max_delay: 最大延迟（默认从配置读取）
        jitter: 抖动系数（默认从配置读取）
        retryable_exceptions: 可重试的异常类型元组
        on_429: 收到 HTTP 429 时的策略
            - "backoff": 通过限速器退避后重试
            - "raise": 立即抛出
    """
    settings = get_settings()
    _max_attempts = max_attempts or settings.retry_max_attempts
    _base_delay = base_delay or settings.retry_base_delay
    _max_delay = max_delay or settings.retry_max_delay
    _jitter = jitter or settings.retry_jitter

    def decorator(func: F) -> F:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_error: Exception | None = None
            for attempt in range(_max_attempts):
                try:
                    result = await func(*args, **kwargs)
                    get_global_limiter().report_success()
                    return result
                except RetryError:
                    raise
                except Exception as e:
                    last_error = e
                    error_type = type(e).__name__

                    # HTTP 429 特殊处理
                    if "429" in str(e) or "TooManyRequests" in error_type:
                        limiter = get_global_limiter()
                        should_pause = limiter.report_429()
                        if should_pause or on_429 == "raise":
                            raise RetryError(
                                f"HTTP 429 after {attempt + 1} attempts, "
                                f"rate limiter {'paused' if should_pause else 'backing off'}",
                                attempts=attempt + 1,
                                last_error=e,
                            ) from e
                        continue

                    if not isinstance(e, retryable_exceptions):
                        raise

                    if attempt < _max_attempts - 1:
                        delay = calculate_delay(attempt, _base_delay, _max_delay, _jitter)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{_max_attempts} "
                            f"failed ({error_type}: {e}), retrying in {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {attempt + 1} attempts: {e}"
                        )

            raise RetryError(
                f"{func.__name__} failed after {_max_attempts} attempts",
                attempts=_max_attempts,
                last_error=last_error,
            )

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_error: Exception | None = None
            import time as _time

            for attempt in range(_max_attempts):
                try:
                    result = func(*args, **kwargs)
                    return result
                except RetryError:
                    raise
                except Exception as e:
                    last_error = e
                    error_type = type(e).__name__

                    if "429" in str(e) or "TooManyRequests" in error_type:
                        if on_429 == "raise":
                            raise
                        # 同步模式下简单等待
                        delay = calculate_delay(attempt, _base_delay, _max_delay, _jitter) * 2
                        logger.warning(f"HTTP 429, waiting {delay:.1f}s")
                        _time.sleep(delay)
                        continue

                    if not isinstance(e, retryable_exceptions):
                        raise

                    if attempt < _max_attempts - 1:
                        delay = calculate_delay(attempt, _base_delay, _max_delay, _jitter)
                        logger.warning(
                            f"{func.__name__} attempt {attempt + 1}/{_max_attempts} "
                            f"failed ({error_type}: {e}), retrying in {delay:.1f}s"
                        )
                        _time.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {attempt + 1} attempts: {e}"
                        )

            raise RetryError(
                f"{func.__name__} failed after {_max_attempts} attempts",
                attempts=_max_attempts,
                last_error=last_error,
            )

        # 根据函数类型返回对应 wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator


async def async_retry_call(
    func: Callable,
    *args,
    max_attempts: int | None = None,
    **kwargs,
) -> Any:
    """直接调用的异步重试包装（不使用装饰器）

    适用于需要在调用点灵活控制重试的场景。
    """
    decorated = with_retry(max_attempts=max_attempts)(func)
    return await decorated(*args, **kwargs)
