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


def _fix_json_literal_newlines(text: str) -> str:
    """将 JSON 字符串值内的 literal 换行符替换为空格。

    LLM 有时在 JSON 字符串值中直接插入换行而不是 \\n 转义序列，
    导致 json.loads() 报 "Unterminated string" 错误。
    此函数用状态机遍历文本，只将字符串值内部的换行替换为空格，
    保留 JSON 结构中的换行（key 之间的换行）不变。
    """
    result = []
    in_string = False
    escape_next = False

    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == '\\' and in_string:
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        # 字符串值内的换行符 → 替换为空格
        if in_string and ch in ('\n', '\r'):
            result.append(' ')
        else:
            result.append(ch)

    return ''.join(result)


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

你的任务是为每个GEO数据集生成简洁的描述摘要，帮助用户快速判断数据集是否符合其研究需求。

## one_sentence_summary 生成要求
生成一个简洁完整的数据集描述（60-100字），包含以下核心要素：
1. 研究主题（疾病/科学问题）
2. 实验类型（scRNA-seq/bulk RNA-seq等）
3. 物种和组织/细胞类型
4. 样本数量和分组
5. 测序平台

描述风格：简洁学术，控制在100字以内。

## 样本分组识别（重要！）
优先从GSM样本属性中提取分组信息：
- 分析source_name, treatment, condition, disease_state, group等字段
- 统计每个分组的样本数量
- 输出格式："病例(n=4)/对照(n=5)"或"发作期(n=3)+缓解期(n=3)"
- 如果GSM属性中没有明确分组信息，从overall_design或summary推断

## 输出要求
- 使用中文输出
- one_sentence_summary控制在100字以内，简洁为主
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
请严格按以下JSON格式输出，不要包含其他内容，不要加任何说明文字：

【重要】样本分组验证规则：
- 该数据集总样本数为 {sample_count} 个
- 分组数量之和必须等于总样本数
- 如果无法确定具体分组，请输出 "NA" 而非猜测

{{
    "one_sentence_summary": "60-100字的数据集描述（研究主题+实验类型+物种+组织+样本数+平台）",
    "sample_grouping": "各分组n值之和须等于{sample_count}，如'病例(n=4)/对照(n=5)'，无法确定输出NA",
    "cell_count": "细胞数如'15K'/'1.2M'，无法确定输出'NA'",
    "relevance_reason": "50字以内，说明与查询相关的核心原因"
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
            for attr in dataset.gsm_attributes[:15]:  # 最多显示15个（减少 token）
                gsm_id = attr.get("gsm_id", attr.get("accession", "?"))
                title = attr.get("title", "")[:50]  # 截短 title 节省 token
                sample_types = attr.get("sample_type", [])
                sample_types_str = "; ".join(str(st) for st in sample_types[:2]) if sample_types else ""
                line = f"- {gsm_id}: {title}"
                if sample_types_str:
                    line += f" [{sample_types_str}]"
                attrs_lines.append(line)
            if len(dataset.gsm_attributes) > 15:
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

    @staticmethod
    def _try_repair_truncated_json(text: str) -> dict | None:
        """尝试修复被截断的 JSON 字符串。

        LLM 有时输出到 max_tokens 上限时，会在字符串值中途截断。
        此方法尝试几种策略将截断的 JSON 补全为可解析状态。

        Returns:
            dict（解析成功）或 None（无法修复）
        """
        import json

        # 策略1：找到最后一个完整的 key-value 对的结束位置，截掉后面内容补上 }
        # 寻找最后一个完整的 "xxx": "yyy" 模式结尾
        # 先找到 { 开头
        start = text.find('{')
        if start == -1:
            return None
        fragment = text[start:]

        # 策略2：逐步向后截，找第一个能 parse 的位置
        # 从末尾向前找最后一个 "," 或 "{" 作为截断点，补上合法结尾
        # 先尝试：截掉最后一个未完整字段，补 }
        # 找到最后一个完整的逗号分隔点（代表上一个字段结束）
        last_complete_comma = -1
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(fragment):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
            if not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                elif ch == ',' and depth == 1:
                    last_complete_comma = i

        if last_complete_comma > 0:
            # 截到最后一个完整字段之后，补 }
            candidate = fragment[:last_complete_comma] + "\n}"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # 策略3：用正则找到所有完整的 "key": "value" 或 "key": number 对，手动构建 dict
        import re
        pairs = re.findall(
            r'"(one_sentence_summary|sample_grouping|cell_count|relevance_reason)"\s*:\s*"((?:[^"\\]|\\.)*)"',
            fragment
        )
        if pairs:
            return {k: v for k, v in pairs}

        return None

    def _parse_response(self, text: str, gse_id: str) -> DatasetAnalysis:
        """解析 LLM 响应"""
        import json
        import re
        from loguru import logger

        analysis = DatasetAnalysis(gse_id=gse_id)

        try:
            # 移除可能的 markdown 代码块
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            data = None

            # 策略1：直接解析
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                pass

            # 策略2：将 JSON 字符串值内的 literal 换行替换为空格，再解析
            # 原因：LLM 有时在字符串值里直接换行而不是输出 \n 转义，导致 Unterminated string
            if data is None:
                try:
                    # 用状态机将字符串值内的换行转为空格（不动 JSON 结构中的换行）
                    fixed = _fix_json_literal_newlines(cleaned)
                    data = json.loads(fixed)
                    logger.debug(f"[LLM Summarizer] {gse_id} 含literal换行，已自动修复")
                except (json.JSONDecodeError, Exception):
                    pass

            # 策略3：用正则提取 JSON 对象块（处理 LLM 在 JSON 前后加了说明文字的情况）
            if data is None:
                json_match = re.search(r'\{[\s\S]*\}', cleaned)
                if json_match:
                    try:
                        fragment = json_match.group(0)
                        data = json.loads(fragment)
                    except json.JSONDecodeError:
                        # 尝试对提取块也做换行修复
                        try:
                            fixed_fragment = _fix_json_literal_newlines(fragment)
                            data = json.loads(fixed_fragment)
                        except (json.JSONDecodeError, Exception):
                            pass

            # 策略4：截断修复（处理 JSON 在 max_tokens 处被截断的情况）
            if data is None:
                repaired = self._try_repair_truncated_json(cleaned)
                if repaired:
                    logger.warning(f"[LLM Summarizer] {gse_id} JSON被截断，已自动修复（截断修复）")
                    data = repaired

            if data is None:
                raise json.JSONDecodeError("无法解析 LLM 响应为合法 JSON", cleaned, 0)

            analysis.one_sentence_summary = data.get("one_sentence_summary", "NA") or "NA"
            analysis.sample_grouping = data.get("sample_grouping", "NA") or "NA"
            analysis.cell_count = data.get("cell_count", "NA") or "NA"
            analysis.relevance_reason = data.get("relevance_reason", "-") or "-"

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # 解析失败，记录原始响应便于诊断
            logger.warning(f"[LLM Summarizer] {gse_id} JSON解析失败: {e}")
            logger.debug(f"[LLM Summarizer] {gse_id} 原始响应: {text[:500]}")
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
