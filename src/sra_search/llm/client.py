"""LLM 客户端抽象层

统一接口，屏蔽不同 LLM API 的差异。支持：
- OpenAI（及 OpenAI-compatible API，如 vLLM、Ollama、DeepSeek）
- Anthropic Claude
- 本地模型（通过 OpenAI-compatible 接口，如 Ollama）

设计原则：
- API Key 为空时 is_available() 返回 False，调用方应回退到 V1 模式
- 网络错误/超时时不抛出异常，而是返回 None 并记录日志
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    pass


class LLMClient(ABC):
    """LLM 客户端抽象基类"""

    @abstractmethod
    def is_available(self) -> bool:
        """检查 LLM 是否已正确配置（有 API Key 且 Provider 有效）"""
        ...

    @abstractmethod
    async def achat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str | None:
        """发送对话请求，返回文本响应。失败时返回 None。

        Args:
            prompt: 用户 prompt 内容
            system: 系统提示（可选）
            model: 覆盖默认模型（可选）
            temperature: 采样温度，评分用 0.0
            max_tokens: 最大 token 数

        Returns:
            响应文本，失败时返回 None
        """
        ...

    @abstractmethod
    async def abatch_chat(
        self,
        prompts: list[str],
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        concurrency: int = 5,
    ) -> list[str | None]:
        """批量对话请求（同一系统提示，多个用户消息）。

        Args:
            prompts: 用户 prompt 列表
            system: 统一的系统提示（可选）
            model: 覆盖默认模型（可选）
            temperature: 采样温度
            max_tokens: 每条最大 token 数
            concurrency: 并发请求数（避免 rate limit）

        Returns:
            响应文本列表（与 prompts 一一对应），失败项为 None
        """
        ...

    @classmethod
    def from_config(cls) -> LLMClient:
        """从全局配置创建 LLM 客户端实例。

        根据 settings.llm_provider 选择对应 Provider。
        若未配置（provider 为空或 api_key 为空），返回 NullLLMClient（永远不可用）。
        """
        from sra_search.config import get_settings
        settings = get_settings()

        provider = (settings.llm_provider or "").lower().strip()
        api_key = settings.llm_api_key or ""
        model = settings.llm_model or ""
        base_url = settings.llm_base_url or ""
        timeout = settings.llm_timeout
        max_tokens = settings.llm_max_tokens

        if not provider or not api_key:
            logger.debug("LLM not configured (no provider or api_key). Using V1 keyword mode.")
            return NullLLMClient()

        if provider == "openai":
            from sra_search.llm.providers.openai_provider import OpenAIProvider
            return OpenAIProvider(
                api_key=api_key,
                model=model or "gpt-4o-mini",
                base_url=base_url or None,
                timeout=timeout,
                max_tokens=max_tokens,
            )
        elif provider == "anthropic":
            from sra_search.llm.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(
                api_key=api_key,
                model=model or "claude-3-5-haiku-20241022",
                timeout=timeout,
                max_tokens=max_tokens,
            )
        elif provider in ("local", "ollama"):
            from sra_search.llm.providers.openai_provider import OpenAIProvider
            # Ollama 使用 OpenAI-compatible 接口
            return OpenAIProvider(
                api_key=api_key or "ollama",
                model=model or "llama3.2",
                base_url=base_url or "http://localhost:11434/v1",
                timeout=timeout,
                max_tokens=max_tokens,
            )
        else:
            logger.warning(f"Unknown LLM provider: {provider!r}. Supported: openai/anthropic/local.")
            return NullLLMClient()

    @classmethod
    def from_params(
        cls,
        provider: str,
        api_key: str,
        model: str = "",
        base_url: str = "",
        timeout: float = 60.0,
        max_tokens: int = 2048,
    ) -> LLMClient:
        """从显式参数创建 LLM 客户端（CLI 参数覆盖场景）"""
        provider_lc = provider.lower().strip()

        if provider_lc == "openai":
            from sra_search.llm.providers.openai_provider import OpenAIProvider
            return OpenAIProvider(
                api_key=api_key,
                model=model or "gpt-4o-mini",
                base_url=base_url or None,
                timeout=timeout,
                max_tokens=max_tokens,
            )
        elif provider_lc == "anthropic":
            from sra_search.llm.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(
                api_key=api_key,
                model=model or "claude-3-5-haiku-20241022",
                timeout=timeout,
                max_tokens=max_tokens,
            )
        elif provider_lc in ("local", "ollama"):
            from sra_search.llm.providers.openai_provider import OpenAIProvider
            return OpenAIProvider(
                api_key=api_key or "ollama",
                model=model or "llama3.2",
                base_url=base_url or "http://localhost:11434/v1",
                timeout=timeout,
                max_tokens=max_tokens,
            )
        else:
            logger.warning(f"Unknown LLM provider: {provider!r}")
            return NullLLMClient()


class NullLLMClient(LLMClient):
    """空实现：LLM 未配置时的占位符，所有方法均安全返回 None/空列表"""

    def is_available(self) -> bool:
        return False

    async def achat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str | None:
        return None

    async def abatch_chat(
        self,
        prompts: list[str],
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        concurrency: int = 5,
    ) -> list[str | None]:
        return [None] * len(prompts)


def _parse_json_safe(text: str) -> dict[str, Any] | None:
    """安全解析 LLM 返回的 JSON（处理 markdown 代码块包裹等情况）"""
    if not text:
        return None
    # 去掉 markdown 代码块
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首尾的 ```json / ``` 行
        inner = []
        for line in lines[1:]:
            if line.strip() == "```":
                break
            inner.append(line)
        text = "\n".join(inner).strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return None
    except (json.JSONDecodeError, ValueError):
        return None


async def _gather_with_concurrency(
    tasks: list[Any],
    concurrency: int,
) -> list[Any]:
    """带并发限制的 asyncio.gather"""
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_with_sem(coro: Any) -> Any:
        async with semaphore:
            return await coro

    return list(await asyncio.gather(*[_run_with_sem(t) for t in tasks], return_exceptions=True))
