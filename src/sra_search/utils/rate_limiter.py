"""速率限制器模块

基于令牌桶算法的全局速率限制器，用于控制 NCBI API 请求频率。
"""
from __future__ import annotations

import asyncio
import time
from threading import Lock

from loguru import logger


class RateLimiter:
    """全局速率限制器

    基于令牌桶算法，限制每秒请求数。
    """

    def __init__(self, rate: float = 3.0):
        """初始化速率限制器

        Args:
            rate: 每秒允许的请求数
        """
        self.rate = rate
        self.capacity = rate  # 桶容量
        self.tokens = self.capacity
        self.last_refill = time.time()
        self.lock = Lock()

        # HTTP 429 跟踪
        self.consecutive_429 = 0
        self.paused_until: float | None = None

        logger.info(f"Rate limiter initialized: {rate} requests/second")

    def acquire(self, tokens: float = 1.0) -> None:
        """获取令牌（阻塞直到可用）

        Args:
            tokens: 需要获取的令牌数
        """
        with self.lock:
            self._refill()

            while self.tokens < tokens:
                # 计算等待时间
                needed = tokens - self.tokens
                wait_time = needed / self.rate
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                time.sleep(wait_time)
                self._refill()

            self.tokens -= tokens

    async def acquire_async(self, tokens: float = 1.0) -> None:
        """异步获取令牌（阻塞直到可用）

        Args:
            tokens: 需要获取的令牌数
        """
        while True:
            with self.lock:
                self._refill()

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return

                # 计算等待时间
                needed = tokens - self.tokens
                wait_time = needed / self.rate

            logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)

    def _refill(self) -> None:
        """补充令牌"""
        current_time = time.time()
        elapsed = current_time - self.last_refill

        # 添加令牌
        new_tokens = elapsed * self.rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_refill = current_time

    def report_success(self) -> None:
        """报告成功请求"""
        with self.lock:
            self.consecutive_429 = 0
            if self.paused_until and time.time() >= self.paused_until:
                self.paused_until = None
                logger.info("Rate limiter resumed")

    def report_429(self) -> bool:
        """报告收到 HTTP 429

        Returns:
            True if rate limiter is now paused
        """
        with self.lock:
            self.consecutive_429 += 1

            if self.consecutive_429 >= 3:
                # 连续 3 次 429，暂停一段时间
                pause_duration = min(60.0, 5.0 * (2 ** (self.consecutive_429 - 3)))
                self.paused_until = time.time() + pause_duration
                logger.warning(f"Rate limiter paused for {pause_duration:.1f}s after {self.consecutive_429} consecutive 429s")
                return True

            return False


# 全局速率限制器实例
_limiter: RateLimiter | None = None


def get_global_limiter() -> RateLimiter:
    """获取全局速率限制器实例"""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(rate=3.0)
    return _limiter


def set_rate_limit(rate: float) -> None:
    """设置全局速率限制

    Args:
        rate: 每秒请求数
    """
    global _limiter
    _limiter = RateLimiter(rate=rate)
