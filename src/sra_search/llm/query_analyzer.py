"""LLM 查询意图分析器

使用 LLM 解析用户查询，提取结构化的生物学意图：
- 疾病名称（多语言）
- 测序技术
- 生物体/物种
- 组织/细胞类型
- 干预类型
- 额外关键词

用途：
- 增强 SmartQueryBuilder 的查询扩展
- 为 compute_relevance_score() 提供疾病/技术关键词补充
- 改进 SRA 过滤条件（如 organism 过滤）

设计：
- 失败时返回 None，调用方使用原始查询词
- JSON 解析鲁棒处理（LLM 可能输出 markdown 代码块）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from sra_search.llm.client import _parse_json_safe

if TYPE_CHECKING:
    from sra_search.llm.client import LLMClient


_ANALYZER_SYSTEM_PROMPT = """You are a bioinformatics expert who specializes in biological query analysis.
Extract structured biological intent from user search queries.
Always respond with valid JSON only, no explanation or markdown.
Use English for all extracted terms."""


_ANALYZER_USER_TEMPLATE = """Extract structured biological intent from this search query:

QUERY: {query}

Return a JSON object with these fields (use empty arrays [] if not applicable):
{{
  "disease": ["list of disease names in English and/or original language"],
  "technology": ["list of sequencing technologies, e.g. scRNA-seq, bulk RNA-seq, ATAC-seq"],
  "organism": ["list of organisms using scientific names, e.g. Homo sapiens"],
  "tissue": ["list of tissues or cell types"],
  "perturbation": ["list of perturbation types, e.g. CRISPR, drug treatment"],
  "keywords": ["additional relevant keywords not in other categories"],
  "intent_summary": "one sentence describing the researcher's intent"
}}

Rules:
- For disease: include both common name and scientific term if known
- For technology: normalize to standard terms (e.g., "single cell" → "scRNA-seq")
- For organism: use scientific names (e.g., "human" → "Homo sapiens")
- Be concise, avoid redundancy across fields
- Return ONLY the JSON object, no other text"""


@dataclass
class QueryIntent:
    """查询意图结构化结果"""
    disease: list[str] = field(default_factory=list)
    technology: list[str] = field(default_factory=list)
    organism: list[str] = field(default_factory=list)
    tissue: list[str] = field(default_factory=list)
    perturbation: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    intent_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "disease": self.disease,
            "technology": self.technology,
            "organism": self.organism,
            "tissue": self.tissue,
            "perturbation": self.perturbation,
            "keywords": self.keywords,
            "intent_summary": self.intent_summary,
        }

    def all_terms(self) -> list[str]:
        """返回所有提取词（用于查询扩展）"""
        terms: list[str] = []
        for lst in [self.disease, self.technology, self.tissue, self.perturbation, self.keywords]:
            terms.extend(lst)
        return list(dict.fromkeys(terms))  # 去重保序

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryIntent:
        return cls(
            disease=data.get("disease", []) or [],
            technology=data.get("technology", []) or [],
            organism=data.get("organism", []) or [],
            tissue=data.get("tissue", []) or [],
            perturbation=data.get("perturbation", []) or [],
            keywords=data.get("keywords", []) or [],
            intent_summary=data.get("intent_summary", "") or "",
        )


class LLMQueryAnalyzer:
    """LLM 查询意图分析器

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

    async def analyze(self, query: str) -> QueryIntent | None:
        """解析查询意图。失败时返回 None。

        Args:
            query: 用户查询词

        Returns:
            QueryIntent 结构化结果，或 None（失败时）
        """
        if not self.is_available():
            return None

        prompt = _ANALYZER_USER_TEMPLATE.format(query=query)

        try:
            response = await self.client.achat(
                prompt=prompt,
                system=_ANALYZER_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=512,
            )
            if not response:
                return None

            parsed = _parse_json_safe(response)
            if parsed is None:
                logger.debug(f"LLM query analyzer: JSON parse failed. Response: {response[:200]!r}")
                return None

            intent = QueryIntent.from_dict(parsed)
            logger.debug(
                f"LLM query analysis: disease={intent.disease}, "
                f"tech={intent.technology}, organism={intent.organism}"
            )
            return intent

        except Exception as e:
            logger.warning(f"LLM query analysis failed: {e}")
            return None
