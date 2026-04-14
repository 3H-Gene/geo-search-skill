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
        """懒加载 Gemini 客户端（优先新版 google-genai SDK）"""
        if self._client is not None:
            return self._client

        # ① 优先：新版 google-genai SDK（google.genai，官方推荐）
        try:
            import google.genai as genai  # type: ignore[import-untyped]
            self._client = genai.Client(api_key=self._api_key)
            self._use_openai_compat = False
            logger.info(
                f"[LLM] GoogleProvider connected: google-genai SDK | "
                f"model={self._model} | API key OK"
            )
            return self._client
        except ImportError:
            pass

        # ② 降级：旧版 google-generativeai SDK（已废弃，触发 FutureWarning）
        # 必须在 import 前压制，否则 import 本身的 warning 不会被 with 捕获
        try:
            import warnings  # noqa: PLC0415

            warnings.filterwarnings("ignore", category=FutureWarning)
            import google.generativeai as genai  # type: ignore[import-untyped]
            genai.configure(api_key=self._api_key)
            self._client = genai
            self._use_openai_compat = False
            logger.warning(
                "[LLM] GoogleProvider: using deprecated google-generativeai SDK. "
                "Please upgrade: pip install 'google-genai>=1.0'"
            )
            logger.info(
                f"[LLM] GoogleProvider connected: google-generativeai SDK (deprecated) | "
                f"model={self._model} | API key OK"
            )
            return self._client
        except ImportError:
            pass

        # ③ 最终降级：openai 库 + Google OpenAI-compatible 端点
        try:
            from openai import AsyncOpenAI  # type: ignore[import-untyped]
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            self._use_openai_compat = True
            logger.info(
                f"[LLM] GoogleProvider connected: OpenAI-compat endpoint | "
                f"model={self._model} | API key OK "
                f"(install google-genai for native SDK support)"
            )
            return self._client
        except ImportError:
            pass

        raise RuntimeError(
            "GoogleProvider requires 'google-genai' or 'openai' package. "
            "Install with: pip install google-genai  OR  pip install openai"
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
        """通过 google-genai / google-generativeai SDK 发送请求"""
        import asyncio as _asyncio
        import warnings

        def _sync_call() -> str | None:
            # google SDK 是同步接口，在线程池中运行
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
                    resp = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config,
                    )
                    # 检查是否有有效内容（避免 finish_reason=MAX_TOKENS/RECITATION 等导致空响应）
                    if resp.candidates:
                        cand = resp.candidates[0]
                        if cand.finish_reason and cand.finish_reason.name in (
                            "STOP",
                            "MAX_TOKENS",
                        ):
                            # 即使是 MAX_TOKENS，只要 content 不为空就有部分结果
                            if cand.content and cand.content.parts:
                                return cand.content.parts[0].text
                        # 其他 finish_reason（安全、版权等）：返回空字符串由上层解析
                        logger.warning(
                            f"[LLM] finish_reason={cand.finish_reason.name} "
                            f"for model={model}, returning None"
                        )
                    return None
                else:
                    # 旧版 google.generativeai（已废弃，压制 FutureWarning）
                    generation_config = {
                        "temperature": temperature,
                        "max_output_tokens": max_tokens,
                    }
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", FutureWarning)
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
