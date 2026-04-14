"""LLM 数据集摘要生成器

为每个数据集生成详细的分析报告，包含：
- 一句话总结
- 样本分组
- 细胞数
- 相关性理由
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sra_search.llm.client import LLMClient
    from sra_search.schema import DatasetSchema


# 正则表达式用于从文本中提取细胞数
CELL_PATTERN = re.compile(
    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:cells|cell)",
    re.IGNORECASE,
)
# 更宽松的细胞数匹配
CELL_PATTERN_2 = re.compile(
    r"(\d+)\s*(?:million|m\b|cells|cell)",
    re.IGNORECASE,
)


def format_cell_count(text: str) -> str | None:
    """从文本中提取并格式化细胞数"""
    if not text:
        return None

    # 尝试匹配 "15,230 cells" 或 "15230 cells"
    match = CELL_PATTERN.search(text)
    if match:
        raw = match.group(1).replace(",", "")
        try:
            num = float(raw)
            if num >= 1000:
                return f"{int(num / 1000)}K"
            return str(int(num))
        except ValueError:
            pass

    # 尝试匹配 "X million cells" 或 "Xm cells"
    match2 = re.search(r"(\d+(?:\.\d+)?)\s*(million|m\b)", text, re.IGNORECASE)
    if match2:
        try:
            num = float(match2.group(1))
            return f"{int(num)}M"
        except ValueError:
            pass

    return None


@dataclass
class DatasetAnalysis:
    """单数据集分析结果"""
    gse_id: str
    one_sentence_summary: str = ""           # 一句话总结
    sample_grouping: str = ""                # 样本分组 (如 "6病例+6对照")
    cell_count: str = ""                     # 细胞数 (如 "15K")
    relevance_reason: str = ""               # 相关性理由

    def to_dict(self) -> dict:
        return {
            "gse_id": self.gse_id,
            "one_sentence_summary": self.one_sentence_summary,
            "sample_grouping": self.sample_grouping,
            "cell_count": self.cell_count,
            "relevance_reason": self.relevance_reason,
        }


class LLMDatasetSummarizer:
    """LLM 数据集摘要生成器

    为每个数据集生成分析报告，帮助用户快速判断数据集是否符合预期。
    """

    SYSTEM_PROMPT = """你是一个专业的生物信息学数据评审专家。

你的任务是分析给定的数据集，帮助用户判断该数据集是否符合其研究需求。

输出要求：
- 使用中文输出
- 一句话总结要简洁（不超过60字），突出核心发现
- 样本分组要具体（如"12病例vs8对照"、"4处理组+4基线组"）
- 相关性理由要说明为什么该数据集与查询相关
- 如果某项信息不明确，输出"NA"
"""

    USER_PROMPT_TEMPLATE = """## 数据集信息
- GSE ID: {gse_id}
- 标题: {title}
- 摘要: {summary}
- 物种: {organism}
- 组织: {tissue}
- 数据类型: {data_type}
- 样本数: {sample_count}
- 测序平台: {platform}

## 用户查询
{query}

## 输出要求
请严格按以下JSON格式输出，不要包含其他内容：
{{
    "one_sentence_summary": "一句话总结（不超过60字，突出核心发现）",
    "sample_grouping": "样本分组描述，如'6病例+6对照'或'4处理+4基线'，无法确定则输出'NA'",
    "cell_count": "细胞数，如'15K'、'28.5K'、'1.2M'，无法确定则输出'NA'",
    "relevance_reason": "相关性理由，说明该数据集为何与查询相关"
}}
"""

    def __init__(self, client: LLMClient):
        """初始化摘要生成器

        Args:
            client: LLM 客户端
        """
        self.client = client

    def _extract_cell_count(self, dataset: DatasetSchema) -> str | None:
        """从数据集元数据或摘要中提取细胞数"""
        # 先尝试从 title 提取
        cell_count = format_cell_count(dataset.title)
        if cell_count:
            return cell_count

        # 从 summary 提取
        cell_count = format_cell_count(dataset.summary)
        if cell_count:
            return cell_count

        return None

    def _extract_sample_grouping(self, dataset: DatasetSchema) -> str | None:
        """从摘要中提取样本分组信息"""
        summary = dataset.summary.lower()

        # 常见模式匹配
        patterns = [
            # 病例 vs 对照
            (r"(\d+)\s*(?:patient|subject|donor|participant|case)s?\s*(?:vs|versus|vs\.?)\s*(\d+)\s*(?:healthy|control|normal)s?", "病例vs对照"),
            (r"(\d+)\s*(?:healthy|control|normal)\s*(?:patient|subject|donor|participant|case)s?\s*(?:vs|versus)\s*(\d+)", "对照vs病例"),
            # 处理 vs 基线
            (r"(\d+)\s*(?:treatment|treated|intervention|stimulated)\s*(?:vs|vs\.?|versus)\s*(\d+)\s*(?:control|baseline|untreated|vehicle)", "处理vs对照"),
            # 时间点
            (r"(\d+)\s*(?:time point|visit|sample)s?", "时间点样本"),
            # 样本数
            (r"(\d+)\s*(?:sample)s?", "样本数"),
        ]

        for pattern, label in patterns:
            match = re.search(pattern, summary)
            if match:
                groups = match.groups()
                if len(groups) >= 2 and groups[0] and groups[1]:
                    return f"{groups[0]} vs {groups[1]} ({label})"
                elif len(groups) == 1:
                    return f"{groups[0]} 样本"

        return None

    def _build_prompt(self, dataset: DatasetSchema, query: str) -> str:
        """构建 LLM prompt"""
        return self.USER_PROMPT_TEMPLATE.format(
            gse_id=dataset.gse_id,
            title=dataset.title,
            summary=dataset.summary[:500] if dataset.summary else "无摘要",
            organism=dataset.organism or "NA",
            tissue=dataset.tissue or "NA",
            data_type=dataset.data_type or "NA",
            sample_count=str(dataset.sample_count) if dataset.sample_count else "NA",
            platform=dataset.platform or "NA",
            query=query,
        )

    def _parse_response(self, text: str, gse_id: str) -> DatasetAnalysis:
        """解析 LLM 响应"""
        import json

        analysis = DatasetAnalysis(gse_id=gse_id)

        try:
            # 尝试解析 JSON
            # 移除可能的 markdown 代码块
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]

            data = json.loads(text.strip())

            analysis.one_sentence_summary = data.get("one_sentence_summary", "NA") or "NA"
            analysis.sample_grouping = data.get("sample_grouping", "NA") or "NA"
            analysis.cell_count = data.get("cell_count", "NA") or "NA"
            analysis.relevance_reason = data.get("relevance_reason", "-") or "-"

        except (json.JSONDecodeError, KeyError):
            # 解析失败，使用默认值
            analysis.one_sentence_summary = "NA"
            analysis.sample_grouping = "NA"
            analysis.cell_count = "NA"
            analysis.relevance_reason = "解析失败"

        return analysis

    async def summarize_async(
        self,
        dataset: DatasetSchema,
        query: str,
    ) -> DatasetAnalysis:
        """异步生成单个数据集的分析报告

        Args:
            dataset: 数据集
            query: 用户查询词

        Returns:
            DatasetAnalysis: 数据集分析结果
        """
        from loguru import logger

        # 快速提取：细胞数和样本分组（优先用正则，减少 LLM 调用）
        cell_count = self._extract_cell_count(dataset)
        sample_grouping = self._extract_sample_grouping(dataset.summary) if dataset.summary else None

        # 如果元数据已提供细胞数，直接使用
        # 注意：现有元数据可能没有细胞数字段，这里做扩展字段的兼容

        # 构建 prompt
        prompt = self._build_prompt(dataset, query)
        # 合并 system + user 为单个 prompt
        full_prompt = f"{self.SYSTEM_PROMPT}\n\n{prompt}"

        try:
            response = await self.client.achat(
                prompt=full_prompt,
                system=None,
                temperature=0.3,
                max_tokens=512,
            )

            analysis = self._parse_response(response, dataset.gse_id)

            # 如果正则提取到了信息，优先使用
            if cell_count and (analysis.cell_count == "NA" or analysis.cell_count == "解析失败"):
                analysis.cell_count = cell_count
            if sample_grouping and (analysis.sample_grouping == "NA" or analysis.sample_grouping == "解析失败"):
                analysis.sample_grouping = sample_grouping

            logger.debug(f"[LLM Summarizer] {dataset.gse_id}: {analysis.one_sentence_summary[:30]}...")

        except Exception as e:
            logger.warning(f"[LLM Summarizer] {dataset.gse_id} 失败: {e}")
            analysis = DatasetAnalysis(gse_id=dataset.gse_id)
            analysis.one_sentence_summary = f"LLM 分析失败: {str(e)[:30]}"
            analysis.sample_grouping = "NA" if not sample_grouping else sample_grouping
            analysis.cell_count = "NA" if not cell_count else cell_count
            analysis.relevance_reason = "-"

        return analysis

    def summarize(
        self,
        dataset: DatasetSchema,
        query: str,
    ) -> DatasetAnalysis:
        """同步生成单个数据集的分析报告

        Args:
            dataset: 数据集
            query: 用户查询词

        Returns:
            DatasetAnalysis: 数据集分析结果
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 在已运行的 loop 中，创建 task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.summarize_async(dataset, query)
                )
                return future.result()
        else:
            return asyncio.run(self.summarize_async(dataset, query))

    async def summarize_batch_async(
        self,
        datasets: list[DatasetSchema],
        query: str,
        concurrency: int = 5,
    ) -> list[DatasetAnalysis]:
        """异步批量生成数据集分析报告

        Args:
            datasets: 数据集列表
            query: 用户查询词
            concurrency: 并发数

        Returns:
            list[DatasetAnalysis]: 分析结果列表
        """
        import asyncio
        from loguru import logger

        logger.info(f"[LLM Summarizer] 批量生成 {len(datasets)} 条数据集分析...")

        semaphore = asyncio.Semaphore(concurrency)

        async def _summarize_with_semaphore(dataset: DatasetSchema) -> DatasetAnalysis:
            async with semaphore:
                return await self.summarize_async(dataset, query)

        tasks = [_summarize_with_semaphore(d) for d in datasets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常结果
        analyses = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"[LLM Summarizer] {datasets[i].gse_id} 异常: {result}")
                analyses.append(DatasetAnalysis(
                    gse_id=datasets[i].gse_id,
                    one_sentence_summary=f"分析异常: {str(result)[:30]}",
                    sample_grouping="NA",
                    cell_count="NA",
                    relevance_reason="-",
                ))
            else:
                analyses.append(result)

        logger.info(f"[LLM Summarizer] 批量生成完成: {len(analyses)} 条")

        return analyses

    def summarize_batch(
        self,
        datasets: list[DatasetSchema],
        query: str,
        concurrency: int = 5,
    ) -> list[DatasetAnalysis]:
        """同步批量生成数据集分析报告

        Args:
            datasets: 数据集列表
            query: 用户查询词
            concurrency: 并发数

        Returns:
            list[DatasetAnalysis]: 分析结果列表
        """
        import asyncio
        return asyncio.run(self.summarize_batch_async(datasets, query, concurrency))
