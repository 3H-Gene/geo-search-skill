"""测试 LLM 模块（Client, Ranker, Summarizer, QueryAnalyzer）

使用 Mock 模拟 LLM API 响应，确保：
1. NullLLMClient 正确回退
2. 评分解析逻辑鲁棒
3. 批量评分缓存机制
4. 查询意图 JSON 解析
5. V1 回退路径
"""
from __future__ import annotations

import asyncio
from dataclasses import field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sra_search.llm.client import NullLLMClient, _parse_json_safe
from sra_search.llm.query_analyzer import LLMQueryAnalyzer, QueryIntent
from sra_search.llm.ranker import LLMRanker, _parse_score
from sra_search.llm.summarizer import LLMSummarizer
from sra_search.schema import DatasetSchema, GranularityType


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_dataset(
    gse_id: str,
    title: str = "",
    summary: str = "",
    disease: str = "",
    relevance_score: float = 0.5,
) -> DatasetSchema:
    ds = DatasetSchema(
        gse_id=gse_id,
        title=title,
        summary=summary,
        disease=disease,
    )
    ds.relevance_score = relevance_score
    return ds


def _make_mock_client(responses: list[str | None]) -> Any:
    """创建一个 Mock LLM 客户端，按顺序返回 responses"""
    mock = MagicMock()
    mock.is_available.return_value = True

    # achat：每个 mock 实例独立管理调用计数
    _achat_count = [0]  # list 闭包避免 nonlocal

    async def _chat(prompt, **kwargs):
        idx = _achat_count[0]
        _achat_count[0] = idx + 1
        return responses[idx] if idx < len(responses) else None

    mock.achat = _chat

    # abatch_chat 也走 _chat（保持兼容）
    async def _batch_chat(prompts, **kwargs):
        return [await _chat(p) for p in prompts]

    mock.abatch_chat = _batch_chat
    return mock


# ── NullLLMClient ──────────────────────────────────────────────────────────────

class TestNullLLMClient:
    def test_not_available(self):
        client = NullLLMClient()
        assert client.is_available() is False

    def test_achat_returns_none(self):
        client = NullLLMClient()
        result = asyncio.get_event_loop().run_until_complete(
            client.achat("hello")
        )
        assert result is None

    def test_abatch_chat_returns_nones(self):
        client = NullLLMClient()
        prompts = ["p1", "p2", "p3"]
        results = asyncio.get_event_loop().run_until_complete(
            client.abatch_chat(prompts)
        )
        assert results == [None, None, None]


# ── _parse_json_safe ────────────────────────────────────────────────────────────

class TestParseJsonSafe:
    def test_valid_json(self):
        result = _parse_json_safe('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_wrapped_json(self):
        text = '```json\n{"key": "value"}\n```'
        result = _parse_json_safe(text)
        assert result == {"key": "value"}

    def test_markdown_no_lang(self):
        text = '```\n{"key": "value"}\n```'
        result = _parse_json_safe(text)
        assert result == {"key": "value"}

    def test_invalid_json_returns_none(self):
        result = _parse_json_safe("this is not json")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _parse_json_safe("")
        assert result is None

    def test_array_returns_none(self):
        # 非 dict 类型返回 None
        result = _parse_json_safe("[1, 2, 3]")
        assert result is None


# ── _parse_score ──────────────────────────────────────────────────────────────

class TestParseScore:
    def test_valid_float(self):
        assert _parse_score("0.850") == pytest.approx(0.850)

    def test_trailing_text(self):
        # LLM 有时会多输出一些文字
        score = _parse_score("0.750 (high relevance)")
        assert score is not None
        assert score == pytest.approx(0.750)

    def test_0_to_100_scale(self):
        # LLM 误给 0-100 分数
        score = _parse_score("85")
        assert score is not None
        assert score == pytest.approx(0.85)

    def test_zero(self):
        assert _parse_score("0") == pytest.approx(0.0)

    def test_one(self):
        assert _parse_score("1.0") == pytest.approx(1.0)

    def test_none_input(self):
        assert _parse_score(None) is None

    def test_empty_string(self):
        assert _parse_score("") is None

    def test_no_number(self):
        assert _parse_score("no score here") is None

    def test_0_to_100_scale_divide(self):
        # 大于 1.0 的数（LLM 误输出 0-100 范围）→ 除以 100 缩放到 0-1 范围
        # 示例："1.5" → 0.015（1.5/100）
        score = _parse_score("1.5")
        assert score == pytest.approx(0.015)


# ── LLMRanker ─────────────────────────────────────────────────────────────────

class TestLLMRanker:
    def test_not_available_returns_empty(self):
        client = NullLLMClient()
        ranker = LLMRanker(client=client)
        assert ranker.is_available() is False

        datasets = [_make_dataset("GSE001")]
        results = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch(datasets, "gout single cell")
        )
        assert results == []

    def test_score_batch_basic(self):
        ds1 = _make_dataset("GSE001", title="Gout scRNA-seq study", relevance_score=0.5)
        ds2 = _make_dataset("GSE002", title="Unrelated dataset", relevance_score=0.3)
        # GSE001 gets high score, GSE002 gets low score
        mock_client = _make_mock_client(["0.900", "0.100"])

        ranker = LLMRanker(client=mock_client)
        results = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch([ds1, ds2], "gout single cell", top_k=2)
        )

        assert len(results) == 2
        # Should be sorted by LLM score descending
        assert results[0][0].gse_id == "GSE001"
        assert results[0][1] == pytest.approx(0.900)
        assert results[1][0].gse_id == "GSE002"
        assert results[1][1] == pytest.approx(0.100)

    def test_score_batch_fallback_on_parse_error(self):
        """当 LLM 返回无法解析的分数时，回退到 V1 分数"""
        ds = _make_dataset("GSE001", title="Test", relevance_score=0.42)
        mock_client = _make_mock_client(["this is not a score"])

        ranker = LLMRanker(client=mock_client)
        results = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch([ds], "query", top_k=1)
        )
        # 回退到 V1 relevance_score
        assert len(results) == 1
        assert results[0][1] == pytest.approx(0.42)

    def test_cache_hit(self):
        ds = _make_dataset("GSE001", title="Test Dataset")
        mock_client = _make_mock_client(["0.750"])

        ranker = LLMRanker(client=mock_client)

        # 第一次调用
        results1 = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch([ds], "gout", top_k=1)
        )
        assert len(results1) == 1

        # 第二次调用（应命中缓存，不再调用 LLM）
        call_count = [0]
        original_batch = mock_client.abatch_chat

        async def _counting_batch(*args, **kwargs):
            call_count[0] += 1
            return await original_batch(*args, **kwargs)

        mock_client.abatch_chat = _counting_batch

        results2 = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch([ds], "gout", top_k=1)
        )
        assert call_count[0] == 0  # 未调用 LLM（缓存命中）
        assert results2[0][1] == results1[0][1]

    def test_remaining_beyond_top_k_use_v1_score(self):
        """超出 top_k 的数据集使用 V1 relevance_score"""
        datasets = [
            _make_dataset(f"GSE{i:03d}", title=f"Dataset {i}", relevance_score=0.3)
            for i in range(5)
        ]
        # top_k=2，只有前两个做 LLM 评分
        mock_client = _make_mock_client(["0.800", "0.600"])
        ranker = LLMRanker(client=mock_client)

        results = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch(datasets, "query", top_k=2)
        )
        assert len(results) == 5
        # 前两个有 LLM 分数（>= 0.6），其余用 V1 分数（0.3）
        high_scored = [s for _, s in results if s >= 0.6]
        assert len(high_scored) >= 2

    def test_min_relevance_skips_zero_score(self):
        """relevance_score == 0 的数据集被 min_relevance=0.01 过滤，跳过 LLM 评分"""
        datasets = [
            _make_dataset("GSE001", relevance_score=0.45),
            _make_dataset("GSE002", relevance_score=0.0),   # 被过滤
            _make_dataset("GSE003", relevance_score=0.12),
        ]
        # 只需要 mock 两个 LLM 调用（GSE001 + GSE003）
        mock_client = _make_mock_client(["0.900", "0.400"])
        ranker = LLMRanker(client=mock_client)

        results = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch(datasets, "query", top_k=10, min_relevance=0.01)
        )
        assert len(results) == 3
        # GSE002 使用 V1 分数 0.0
        gse002_result = next(s for ds, s in results if ds.gse_id == "GSE002")
        assert gse002_result == 0.0

    def test_score_all_bypasses_top_k(self):
        """score_all=True 时，所有通过 min_relevance 的数据集都做 LLM 评分"""
        datasets = [
            _make_dataset(f"GSE{i:03d}", relevance_score=0.5)
            for i in range(10)
        ]
        mock_client = _make_mock_client(["0.950"] * 10)
        ranker = LLMRanker(client=mock_client)

        results = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch(datasets, "query", top_k=3, score_all=True)
        )
        # 所有 10 个数据集都在结果中
        assert len(results) == 10
        # score_all=True 时，remaining 为空，全部用 LLM 分数
        llm_scores = [s for ds, s in results]
        assert all(s > 0.9 for s in llm_scores)

    def test_min_relevance_and_top_k_combined(self):
        """min_relevance + top_k 同时生效：先过滤，再取 top_k"""
        datasets = [
            _make_dataset("GSE001", relevance_score=0.8),
            _make_dataset("GSE002", relevance_score=0.5),
            _make_dataset("GSE003", relevance_score=0.3),  # min_relevance=0.4 过滤掉
            _make_dataset("GSE004", relevance_score=0.9),
        ]
        mock_client = _make_mock_client(["0.800", "0.900"])
        ranker = LLMRanker(client=mock_client)

        results = asyncio.get_event_loop().run_until_complete(
            ranker.score_batch(
                datasets, "query", top_k=2, min_relevance=0.4
            )
        )
        assert len(results) == 4
        # GSE003 被 min_relevance=0.4 过滤，用 V1 分数 0.3
        gse003 = next(s for ds, s in results if ds.gse_id == "GSE003")
        assert gse003 == 0.3


# ── LLMSummarizer ──────────────────────────────────────────────────────────────

class TestLLMSummarizer:
    def test_not_available_returns_empty(self):
        summarizer = LLMSummarizer(client=NullLLMClient())
        result = asyncio.get_event_loop().run_until_complete(
            summarizer.summarize("query", [], 0)
        )
        assert result == ""

    def test_summarize_basic(self):
        mock_client = _make_mock_client(["This is a summary of the results."])
        summarizer = LLMSummarizer(client=mock_client)
        datasets = [_make_dataset("GSE001", title="Gout scRNA-seq")]

        result = asyncio.get_event_loop().run_until_complete(
            summarizer.summarize("gout single cell", datasets, total_found=5)
        )
        assert "summary" in result.lower() or len(result) > 0

    def test_summarize_empty_datasets(self):
        mock_client = _make_mock_client(["Some text"])
        summarizer = LLMSummarizer(client=mock_client)

        result = asyncio.get_event_loop().run_until_complete(
            summarizer.summarize("query", [], 0)
        )
        assert result == ""  # 无数据集时返回空


# ── LLMQueryAnalyzer ──────────────────────────────────────────────────────────

class TestLLMQueryAnalyzer:
    def test_not_available_returns_none(self):
        analyzer = LLMQueryAnalyzer(client=NullLLMClient())
        result = asyncio.get_event_loop().run_until_complete(
            analyzer.analyze("gout single cell")
        )
        assert result is None

    def test_analyze_valid_json(self):
        response_json = """{
            "disease": ["gout", "hyperuricemia"],
            "technology": ["scRNA-seq"],
            "organism": ["Homo sapiens"],
            "tissue": ["synovial tissue"],
            "perturbation": [],
            "keywords": ["uric acid", "monosodium urate"],
            "intent_summary": "Find single-cell datasets for gout research"
        }"""
        mock_client = _make_mock_client([response_json])
        analyzer = LLMQueryAnalyzer(client=mock_client)

        result = asyncio.get_event_loop().run_until_complete(
            analyzer.analyze("gout single cell")
        )
        assert result is not None
        assert isinstance(result, QueryIntent)
        assert "gout" in result.disease
        assert "scRNA-seq" in result.technology
        assert "Homo sapiens" in result.organism

    def test_analyze_json_parse_failure_returns_none(self):
        mock_client = _make_mock_client(["this is not json at all"])
        analyzer = LLMQueryAnalyzer(client=mock_client)

        result = asyncio.get_event_loop().run_until_complete(
            analyzer.analyze("gout single cell")
        )
        assert result is None

    def test_analyze_markdown_wrapped_json(self):
        response = '```json\n{"disease": ["cancer"], "technology": [], "organism": [], "tissue": [], "perturbation": [], "keywords": [], "intent_summary": "Cancer datasets"}\n```'
        mock_client = _make_mock_client([response])
        analyzer = LLMQueryAnalyzer(client=mock_client)

        result = asyncio.get_event_loop().run_until_complete(
            analyzer.analyze("cancer datasets")
        )
        assert result is not None
        assert "cancer" in result.disease

    def test_query_intent_all_terms(self):
        intent = QueryIntent(
            disease=["gout"],
            technology=["scRNA-seq"],
            tissue=["blood"],
            keywords=["uric acid"],
        )
        terms = intent.all_terms()
        assert "gout" in terms
        assert "scRNA-seq" in terms
        assert "uric acid" in terms
        # organism 不在 all_terms() 中
        assert len(terms) == 4


# ── LLMClient.from_config ────────────────────────────────────────────────────

class TestLLMClientFromConfig:
    def test_no_provider_returns_null(self):
        """未配置时返回 NullLLMClient"""
        from sra_search.llm.client import LLMClient
        from sra_search.config import reset_settings
        import os

        # 保存并清除环境变量
        saved = {k: os.environ.pop(k, None) for k in [
            "SRA_SEARCH_LLM_PROVIDER", "SRA_SEARCH_LLM_API_KEY"
        ]}
        reset_settings()
        try:
            client = LLMClient.from_config()
            assert isinstance(client, NullLLMClient)
            assert client.is_available() is False
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
            reset_settings()

    def test_openai_provider_created(self):
        """配置 openai provider 时创建 OpenAIProvider"""
        from sra_search.llm.client import LLMClient
        from sra_search.llm.providers.openai_provider import OpenAIProvider
        from sra_search.config import reset_settings
        import os

        saved = {k: os.environ.pop(k, None) for k in [
            "SRA_SEARCH_LLM_PROVIDER", "SRA_SEARCH_LLM_API_KEY", "SRA_SEARCH_LLM_MODEL"
        ]}
        os.environ["SRA_SEARCH_LLM_PROVIDER"] = "openai"
        os.environ["SRA_SEARCH_LLM_API_KEY"] = "sk-test-key"
        reset_settings()
        try:
            client = LLMClient.from_config()
            assert isinstance(client, OpenAIProvider)
            assert client.is_available() is True
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
                elif k in os.environ:
                    del os.environ[k]
            reset_settings()

    def test_google_provider_created_from_params(self):
        """from_params 支持 google/gemini provider"""
        from sra_search.llm.client import LLMClient
        from sra_search.llm.providers.google_provider import GoogleProvider

        client = LLMClient.from_params(provider="google", api_key="test-key")
        assert isinstance(client, GoogleProvider)
        assert client.is_available() is True

    def test_gemini_alias_supported(self):
        """gemini 是 google 的别名"""
        from sra_search.llm.client import LLMClient
        from sra_search.llm.providers.google_provider import GoogleProvider

        client = LLMClient.from_params(provider="gemini", api_key="test-key", model="gemini-2.5-flash")
        assert isinstance(client, GoogleProvider)
        assert client.is_available() is True

    def test_unknown_provider_returns_null(self):
        """未知 provider 返回 NullLLMClient 而非抛出异常"""
        from sra_search.llm.client import LLMClient

        client = LLMClient.from_params(provider="foobar", api_key="test-key")
        assert isinstance(client, NullLLMClient)
        assert client.is_available() is False

    def test_google_provider_no_key_unavailable(self):
        """空 api_key 时 is_available() = False"""
        from sra_search.llm.providers.google_provider import GoogleProvider

        p = GoogleProvider(api_key="")
        assert p.is_available() is False


# ── compute_relevance_score 排序回归测试 ─────────────────────────────────────

class TestRelevanceScoreRanking:
    """验证修改后的排序逻辑正确性（gout single cell 场景）"""

    def _make_ds(
        self,
        gse_id: str,
        title: str,
        summary: str = "",
        single_cell: bool = False,
    ) -> DatasetSchema:
        ds = DatasetSchema(gse_id=gse_id, title=title, summary=summary)
        ds.single_cell = single_cell
        return ds

    def test_sc_gout_beats_bulk_gout(self):
        """scRNA-seq + gout 数据集应比 bulk RNA-seq + gout 排名高"""
        from sra_search.converter import compute_relevance_score

        # scRNA-seq + 痛风
        sc_gout = self._make_ds(
            "GSE217561",
            "Single-Cell RNA sequencing reveals blood cell landscape in gout patients",
            single_cell=True,
        )
        # bulk RNA-seq + 痛风（有摘要但不是 scRNA）
        bulk_gout = self._make_ds(
            "GSE160308",
            "In-depth transcriptomic analyses of uric acid metabolism in hyperuricemia",
            single_cell=False,
        )

        sc_score = compute_relevance_score("gout single cell", sc_gout)
        bulk_score = compute_relevance_score("gout single cell", bulk_gout)

        assert sc_score > bulk_score, (
            f"scRNA+gout ({sc_score:.3f}) should rank above bulk+gout ({bulk_score:.3f})"
        )

    def test_no_gout_no_sc_scores_very_low(self):
        """既无痛风词也无 scRNA 的数据集应得分极低"""
        from sra_search.converter import compute_relevance_score

        unrelated = self._make_ds(
            "GSE18002",
            "Paramecium tetraurelia autogamy series",
            single_cell=False,
        )
        score = compute_relevance_score("gout single cell", unrelated)
        # 无任何关键词匹配，分数应接近 0
        assert score < 0.05, f"Unrelated dataset should have low score, got {score:.3f}"

    def test_sc_gout_scores_high(self):
        """scRNA + gout 数据集得分应 >= 0.6"""
        from sra_search.converter import compute_relevance_score

        sc_gout = self._make_ds(
            "GSE217561",
            "Single-Cell RNA sequencing reveals blood cell landscape in gout patients",
            single_cell=True,
        )
        score = compute_relevance_score("gout single cell", sc_gout)
        assert score >= 0.6, f"scRNA+gout should score >= 0.6, got {score:.3f}"

    def test_bulk_gout_penalized(self):
        """bulk RNA-seq + 痛风词，因为查询含 single cell，分数应被惩罚"""
        from sra_search.converter import compute_relevance_score

        bulk_gout = self._make_ds(
            "GSE160308",
            "In-depth transcriptomic analyses of hyperuricemia and uric acid gout",
            single_cell=False,
        )
        score = compute_relevance_score("gout single cell", bulk_gout)
        # 有痛风词，但没有 scRNA → 应惩罚到 0.45 * 0.25 ≈ 0.11
        assert score < 0.20, f"Bulk+gout with scRNA query should be penalized, got {score:.3f}"

    def test_sc_without_disease_penalized(self):
        """scRNA-seq 但无痛风词，因为查询含痛风词，应被轻度惩罚"""
        from sra_search.converter import compute_relevance_score

        sc_no_gout = self._make_ds(
            "GSE158055",
            "Large-scale single-cell analysis reveals critical immune cells in COVID-19",
            single_cell=True,
        )
        score = compute_relevance_score("gout single cell", sc_no_gout)
        # 有 scRNA 但无痛风词 → 中度惩罚
        assert score < 0.10, f"scRNA without gout should be penalized, got {score:.3f}"
