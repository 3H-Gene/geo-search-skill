"""LLM 辅助检索模块

提供 LLM 语义评分、查询意图分析和结果摘要功能。
当用户提供 API Key 时启用，否则完全回退到 V1 关键词模式。

用法示例：
    from sra_search.llm import LLMClient, LLMRanker, LLMSummarizer, LLMQueryAnalyzer

    client = LLMClient.from_config()   # 从 settings 自动初始化
    if client.is_available():
        ranker = LLMRanker(client)
        results = await ranker.score_batch(datasets, query)
"""
from __future__ import annotations

from sra_search.llm.client import LLMClient
from sra_search.llm.query_analyzer import LLMQueryAnalyzer, QueryIntent
from sra_search.llm.ranker import LLMRanker
from sra_search.llm.summarizer import LLMSummarizer

__all__ = [
    "LLMClient",
    "LLMRanker",
    "LLMSummarizer",
    "LLMQueryAnalyzer",
    "QueryIntent",
]
