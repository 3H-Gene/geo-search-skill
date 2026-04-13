"""Anthropic Claude Provider"""
from __future__ import annotations

from typing import Any

from loguru import logger

from sra_search.llm.client import LLMClient, _gather_with_concurrency


class AnthropicProvider(LLMClient):
    """Anthropic Claude API Provider"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-20241022",
        timeout: float = 60.0,
        max_tokens: int = 2048,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client: Any | None = None

    def _get_client(self) -> Any:
        """延迟初始化 anthropic AsyncAnthropic 客户端"""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic  # type: ignore[import-not-found]
                self._client = AsyncAnthropic(
                    api_key=self.api_key,
                    timeout=self.timeout,
                )
            except ImportError:
                logger.error(
                    "anthropic package not installed. Run: pip install 'sra-search[llm]'"
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
            kwargs: dict[str, Any] = {
                "model": model or self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system

            response = await client.messages.create(**kwargs)
            content = response.content
            if content and len(content) > 0:
                block = content[0]
                if hasattr(block, "text"):
                    return block.text.strip()
            return None
        except ImportError:
            return None
        except Exception as e:
            logger.warning(f"Anthropic achat error: {type(e).__name__}: {e}")
            return None

    async def abatch_chat(
        self,
        prompts: list[str],
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        concurrency: int = 3,  # Anthropic 限速更严格，默认小一些
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

        return [
            r if isinstance(r, str) else None
            for r in results
        ]
