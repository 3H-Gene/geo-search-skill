"""LLM 结果摘要生成器

对搜索结果 Top-N 生成自然语言总结，帮助用户快速了解结果概况。

设计：
- 只生成一次（不是每个数据集）
- 失败时返回空字符串，不影响主流程
- 使用 temperature=0.1 保持一定多样性
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from sra_search.llm.client import LLMClient
    from sra_search.schema import DatasetSchema


_SUMMARIZER_SYSTEM_PROMPT = """You are a bioinformatics expert who specializes in genomics data discovery.
You help researchers understand search results from NCBI GEO/SRA databases.
Write clear, concise, and informative summaries in the SAME LANGUAGE as the user's query.
If the query is in English, write in English. If in Chinese, write in Chinese."""


def _build_summary_prompt(
    query: str,
    datasets: list[DatasetSchema],
    total_found: int,
) -> str:
    """构建摘要 prompt"""
    lines: list[str] = [
        f'Search query: "{query}"',
        f"Total datasets found: {total_found} (showing top {len(datasets)})",
        "",
        "Top datasets:",
    ]

    for i, ds in enumerate(datasets, 1):
        summary_snippet = ""
        if ds.summary:
            summary_snippet = ds.summary[:200].replace("\n", " ")
            if len(ds.summary) > 200:
                summary_snippet += "..."

        lines.append(
            f"\n{i}. {ds.gse_id} - {ds.title}"
            f"\n   Organism: {ds.organism or 'unknown'}"
            f"\n   Data type: {ds.data_type} | Granularity: {ds.granularity}"
            f"\n   Samples: {ds.sample_count}"
            f"\n   Disease: {ds.disease or 'not specified'}"
            f"\n   Tissue: {ds.tissue or ds.organ or 'not specified'}"
            f"\n   Perturbation: {', '.join(ds.perturbation_types) if ds.perturbation_types else 'none'}"
            f"\n   Published: {ds.publication_date or 'unknown'}"
            + (f"\n   Summary: {summary_snippet}" if summary_snippet else "")
        )

    lines.extend([
        "",
        "Please write a 3-5 paragraph natural language summary that includes:",
        "1. Overview of the results (how many relevant, what technology types dominate)",
        "2. Highlights of the 2-3 most relevant datasets",
        "3. Research trends or patterns observed (if any)",
        "4. Suggested next steps for the researcher",
        "",
        "Important: Write in the SAME LANGUAGE as the search query.",
        "Do NOT use Markdown formatting. Write plain text paragraphs.",
    ])

    return "\n".join(lines)


class LLMSummarizer:
    """LLM 结果摘要生成器

    Args:
        client: LLM 客户端，None 则从配置初始化
    """

    def __init__(self, client: LLMClient | None = None) -> None:
        if client is None:
            from sra_search.llm.client import LLMClient as _LLMClient
            client = _LLMClient.from_config()
        self.client = client

    def is_available(self) -> bool:
        return self.client.is_available()

    async def summarize(
        self,
        query: str,
        datasets: list[DatasetSchema],
        total_found: int,
        top_n: int = 5,
        max_tokens: int = 1024,
    ) -> str:
        """生成搜索结果摘要。

        Args:
            query: 用户查询词
            datasets: 数据集列表（已排好序）
            total_found: 总发现数量
            top_n: 用于生成摘要的 top N 数据集
            max_tokens: 最大输出 token 数

        Returns:
            自然语言摘要文本，失败时返回空字符串
        """
        if not self.is_available() or not datasets:
            return ""

        top_datasets = datasets[:top_n]
        prompt = _build_summary_prompt(query, top_datasets, total_found)

        try:
            result = await self.client.achat(
                prompt=prompt,
                system=_SUMMARIZER_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=max_tokens,
            )
            return result or ""
        except Exception as e:
            exc_type = type(e).__name__
            if "RateLimitError" in exc_type:
                logger.warning(f"LLM summarize rate limited: {e}")
            elif "AuthenticationError" in exc_type or "401" in str(e):
                logger.error(f"LLM summarize auth failed: {e}. Check API key.")
            elif "Timeout" in exc_type:
                logger.warning(f"LLM summarize timeout: {e}")
            else:
                logger.warning(f"LLM summarize failed: {exc_type}: {e}")
            return ""
