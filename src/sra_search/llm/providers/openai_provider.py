"""OpenAI Provider（及 OpenAI-compatible API）

支持：
- OpenAI（GPT-4o, GPT-4o-mini 等）
- DeepSeek（兼容 OpenAI API）
- Ollama（通过 OpenAI-compatible 接口）
- vLLM / LM Studio / 其他本地部署
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from sra_search.llm.client import LLMClient, _gather_with_concurrency


class OpenAIProvider(LLMClient):
    """OpenAI API Provider（及 OpenAI-compatible API）"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        timeout: float = 60.0,
        max_tokens: int = 2048,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client: Any | None = None

    def _get_client(self) -> Any:
        """延迟初始化 openai AsyncOpenAI 客户端"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI  # type: ignore[import-not-found]
                kwargs: dict[str, Any] = {
                    "api_key": self.api_key,
                    "timeout": self.timeout,
                }
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                self._client = AsyncOpenAI(**kwargs)
            except ImportError:
                logger.error(
                    "openai package not installed. Run: pip install 'sra-search[llm]'"
                )
                raise
        return self._client

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def achat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str | None:
        try:
            client = self._get_client()
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = await client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            return content.strip() if content else None
        except ImportError:
            return None
        except Exception as e:
            exc_type = type(e).__name__
            # 分层异常处理：不同类型不同日志级别和处理策略
            if "RateLimitError" in exc_type or "429" in str(e):
                logger.warning(f"OpenAI rate limited (429): {e}. Consider reducing concurrency.")
            elif "AuthenticationError" in exc_type or "401" in str(e) or "BadAPIKey" in exc_type:
                logger.error(f"OpenAI auth failed (401/BadAPIKey): {e}. Check API key.")
            elif "Timeout" in exc_type:
                logger.warning(f"OpenAI timeout: {e}")
            elif "APIConnectionError" in exc_type:
                logger.warning(f"OpenAI connection error: {e}")
            else:
                logger.warning(f"OpenAI achat error: {exc_type}: {e}")
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
        if not prompts:
            return []

        tasks = [
            self.achat(
                prompt=p,
                system=system,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            for p in prompts
        ]

        results = await _gather_with_concurrency(tasks, concurrency)

        # 将异常转为 None
        return [
            r if isinstance(r, str) else None
            for r in results
        ]
