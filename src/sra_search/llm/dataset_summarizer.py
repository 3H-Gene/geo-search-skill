"""LLM 数据集摘要生成器

为每个数据集生成详细的分析报告，包含：
- 一句话总结
- 样本分组
- 细胞数
- 相关性理由
"""
from __future__ import annotations

import re
from dataclasses import dataclass
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

你的任务是为每个GEO数据集生成完整的描述摘要，帮助用户快速判断数据集是否符合其研究需求。

## one_sentence_summary 生成要求
生成一个完整的数据集描述（100-200字），包含以下要素：
1. **研究主题**：疾病名称或科学问题
2. **实验类型**：scRNA-seq/bulk RNA-seq/ATAC-seq等
3. **物种和样本来源**：包括组织类型或细胞类型
4. **样本数量和分组**：总样本数及实验组/对照组分布
5. **测序平台和技术**：如Illumina NovaSeq 6000
6. **数据处理方式**：是否有processed matrix，还是raw data only
7. **关键实验条件**：处理因素、时间点、疾病状态等

描述风格：学术但易懂，像论文引言中的数据集介绍。

## 样本分组识别（重要！）
优先从GSM样本属性中提取分组信息：
- 分析source_name, treatment, condition, disease_state, group等字段
- 统计每个分组的样本数量
- 输出格式："病例(n=4)/对照(n=5)"或"发作期(n=3)+缓解期(n=3)"
- 如果GSM属性中没有明确分组信息，从overall_design或summary推断

## 输出要求
- 使用中文输出
- one_sentence_summary要完整（100-200字），不是简短的一句话
- 样本分组要具体（结构化格式：分组名(n=数量)）
- 如果某项信息不明确，输出"NA"
"""

    USER_PROMPT_TEMPLATE = """## 数据集信息
- GSE ID: {gse_id}
- 标题: {title}
- 摘要: {summary}
- 实验设计: {overall_design}
- 物种: {organism}
- 组织/细胞: {tissue}
- 数据类型: {data_type}
- 实验粒度: {granularity}
- 样本数: {sample_count}
- 测序平台: {platform}
- 发表日期: {publication_date}
- 补充文件格式: {supp_files}
- Series Matrix: {series_matrix}

## GSM样本属性（优先用于分组识别）
{gsm_attributes}

## 用户查询
{query}

## 输出要求
请严格按以下JSON格式输出，不要包含其他内容：

请生成一个完整的数据集描述，包含以下要素：
1. 研究主题/疾病/科学问题
2. 实验类型（scRNA-seq/bulk RNA-seq/ATAC-seq等）
3. 物种和样本来源（组织/细胞类型）
4. 样本数量和分组情况
5. 测序平台和技术
6. 数据处理方式（有processed matrix还是raw data）
7. 关键实验条件或处理因素

示例格式：
"[GSE123456]是一项关于[疾病/研究问题]的[实验类型]研究，采集自[物种]的[组织/细胞类型]（共[样本数]个样本，包含[分组信息]），使用[平台]完成测序。[数据描述/处理方式]。[发表年份]"

{{
    "one_sentence_summary": "完整数据集描述（100-200字，包含研究主题、实验类型、物种、组织、样本数、平台、数据处理方式和关键条件）",
    "sample_grouping": "样本分组描述，使用结构化格式如'病例(n=4)/对照(n=5)'或'发作期(n=3)+缓解期(n=3)'，无法确定则输出'NA'",
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

        # 格式化补充文件列表
        if dataset.supplementary_files:
            file_info = "; ".join(
                f"{f.get('name', '?')[:60]}({f.get('type', 'unknown')})"
                for f in dataset.supplementary_files[:10]
            )
            if len(dataset.supplementary_files) > 10:
                file_info += f" ... 等{len(dataset.supplementary_files)}个文件"
        else:
            file_info = "无"

        # 格式化 overall_design
        overall_design = dataset.overall_design[:300] if dataset.overall_design else "无"

        # 格式化 GSM 样本属性（用于分组识别）
        if dataset.gsm_attributes:
            # 提取关键字段，按分组整理
            attrs_lines = []
            for attr in dataset.gsm_attributes[:20]:  # 最多显示20个
                gsm_id = attr.get("gsm_id", attr.get("accession", "?"))
                title = attr.get("title", "")[:80]
                sample_types = attr.get("sample_type", [])
                sample_types_str = "; ".join(str(st) for st in sample_types[:3]) if sample_types else ""
                line = f"- {gsm_id}: {title}"
                if sample_types_str:
                    line += f" [{sample_types_str}]"
                attrs_lines.append(line)
            if len(dataset.gsm_attributes) > 20:
                attrs_lines.append(f"... 等共{len(dataset.gsm_attributes)}个样本")
            gsm_attrs_formatted = "\n".join(attrs_lines)
        else:
            gsm_attrs_formatted = "无GSM样本属性信息"

        return self.USER_PROMPT_TEMPLATE.format(
            gse_id=dataset.gse_id,
            title=dataset.title,
            summary=dataset.summary[:500] if dataset.summary else "无摘要",
            overall_design=overall_design,
            organism=dataset.organism or "NA",
            tissue=dataset.tissue or "NA",
            data_type=dataset.data_type or "NA",
            granularity=dataset.granularity or "NA",
            sample_count=str(dataset.sample_count) if dataset.sample_count else "NA",
            platform=dataset.platform or "NA",
            publication_date=dataset.publication_date or "NA",
            supp_files=file_info,
            series_matrix="有" if dataset.series_matrix_available else "无",
            gsm_attributes=gsm_attrs_formatted,
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
        sample_grouping = self._extract_sample_grouping(dataset) if dataset.summary else None

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
                max_tokens=2048,  # 摘要 JSON 输出较长，确保不截断
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
