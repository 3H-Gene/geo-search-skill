"""Google Gemini LLM Provider

支持通过 google-generativeai SDK 访问 Gemini 系列模型。
也支持通过 OpenAI-compatible 接口（如 Vertex AI Gateway）访问。
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from sra_search.llm.client import LLMClient, _gather_with_concurrency


class GoogleProvider(LLMClient):
    """Google Gemini Provider

    优先使用 google-generativeai SDK（pip install google-generativeai）。
    若未安装，尝试使用 openai 库的 OpenAI-compatible 接口
    (https://ai.google.dev/gemini-api/docs/openai).
    """

    # 默认模型
    DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(
        self,
        api_key: str,
        model: str = "",
        timeout: float = 60.0,
        max_tokens: int = 2048,
    ) -> None:
        self._api_key = api_key
        self._model = model or self.DEFAULT_MODEL
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._client: Any = None       # google.genai.Client (lazy)
        self._use_openai_compat = False  # fallback flag

    # ── 初始化 ───────────────────────────────────────────────────────────

    def _get_client(self) -> Any:
        """懒加载 google.generativeai 客户端"""
        if self._client is not None:
            return self._client

        # 尝试 google-generativeai（新版 google-genai SDK）
        try:
            import google.generativeai as genai  # type: ignore[import-untyped]
            genai.configure(api_key=self._api_key)
            self._client = genai
            self._use_openai_compat = False
            logger.debug(f"GoogleProvider: using google-generativeai SDK, model={self._model}")
            return self._client
        except ImportError:
            pass

        # 尝试新版 google-genai（google.genai）
        try:
            import google.genai as genai  # type: ignore[import-untyped]
            self._client = genai.Client(api_key=self._api_key)
            self._use_openai_compat = False
            logger.debug(f"GoogleProvider: using google-genai SDK, model={self._model}")
            return self._client
        except ImportError:
            pass

        # 回退：openai 库 + Google OpenAI-compatible 端点
        try:
            from openai import AsyncOpenAI  # type: ignore[import-untyped]
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            self._use_openai_compat = True
            logger.info(
                "GoogleProvider: google-generativeai not installed, "
                "falling back to OpenAI-compatible endpoint for Gemini."
            )
            return self._client
        except ImportError:
            pass

        raise RuntimeError(
            "GoogleProvider requires either 'google-generativeai' or 'openai' package. "
            "Install with: pip install google-generativeai  OR  pip install openai"
        )

    def is_available(self) -> bool:
        return bool(self._api_key)

    # ── 接口实现 ─────────────────────────────────────────────────────────

    async def achat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str | None:
        """发送单条对话请求"""
        effective_model = model or self._model
        try:
            client = self._get_client()
        except RuntimeError as e:
            logger.error(f"GoogleProvider init error: {e}")
            return None

        try:
            if self._use_openai_compat:
                return await self._achat_openai_compat(
                    client, prompt, system, effective_model, temperature, max_tokens
                )
            else:
                return await self._achat_genai(
                    client, prompt, system, effective_model, temperature, max_tokens
                )
        except Exception as e:
            logger.warning(f"GoogleProvider.achat error ({effective_model}): {e}")
            return None

    async def _achat_genai(
        self,
        client: Any,
        prompt: str,
        system: str | None,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        """通过 google-generativeai SDK 发送请求"""
        import asyncio as _asyncio

        def _sync_call() -> str | None:
            # google-generativeai 是同步 SDK，在线程池中运行
            try:
                # 检查是新版 google-genai（有 aio 属性）还是旧版
                if hasattr(client, "aio"):
                    # 新版 google.genai.Client
                    import google.genai.types as gtypes  # type: ignore[import-untyped]
                    config = gtypes.GenerateContentConfig(
                        system_instruction=system or "",
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                    )
                    # 同步调用
                    resp = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config,
                    )
                    return resp.text
                else:
                    # 旧版 google.generativeai
                    generation_config = {
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                    }
                    m = client.GenerativeModel(
                        model_name=model,
                        system_instruction=system,
                        generation_config=generation_config,  # type: ignore[arg-type]
                    )
                    resp = m.generate_content(prompt)
                    return resp.text
            except Exception as e:
                logger.warning(f"GoogleProvider genai call error: {e}")
                return None

        loop = _asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)

    async def _achat_openai_compat(
        self,
        client: Any,
        prompt: str,
        system: str | None,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str | None:
        """通过 OpenAI-compatible 接口发送请求"""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content
        return content if isinstance(content, str) else None

    async def abatch_chat(
        self,
        prompts: list[str],
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        concurrency: int = 5,
    ) -> list[str | None]:
        """批量对话（并发受控）"""
        tasks = [
            self.achat(p, system=system, model=model, temperature=temperature, max_tokens=max_tokens)
            for p in prompts
        ]
        results = await _gather_with_concurrency(tasks, concurrency)
        return [
            r if isinstance(r, str) else None
            for r in results
        ]
