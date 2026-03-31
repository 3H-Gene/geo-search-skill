"""关键词组合生成器

基于主题维度（疾病 x 器官 x 组学类型 x 物种）生成搜索关键词组合，
支持智能过滤无效组合和权重排序。
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Optional, Tuple

from loguru import logger

from sra_search.knowledge_graph import KnowledgeGraph
from sra_search.topic_manager.topic import TopicDefinition


class KeywordGenerator:
    """关键词组合生成器"""

    def __init__(self, kg: Optional[KnowledgeGraph] = None):
        self.kg = kg or KnowledgeGraph()

        # 默认组学类型（如果主题未指定）
        self.default_omics = [
            "scRNA-seq", "bulk RNA-seq", "spatial transcriptomics",
            "ATAC-seq", "ChIP-seq", "proteomics", "GWAS",
            "WGS", "WES", "Ribo-seq", "single-cell multi-omics",
        ]

        # 组合权重（影响搜索优先级）
        self.weights: Dict[str, float] = {
            "disease_omics": 1.0,      # 疾病 x 组学
            "organ_omics": 0.8,        # 器官 x 组学
            "disease_organ": 0.6,      # 疾病 x 器官
            "disease_only": 0.5,       # 仅疾病
            "organ_only": 0.4,         # 仅器官
            "omics_only": 0.3,         # 仅组学
        }

    def generate(
        self,
        topic: TopicDefinition,
        max_queries: int = 100,
    ) -> List[Tuple[str, float]]:
        """生成关键词组合

        Args:
            topic: 主题定义
            max_queries: 最大组合数量

        Returns:
            (搜索词, 权重) 列表，按权重降序排列
        """
        queries: List[Tuple[str, float]] = []

        # 展开维度中的同义词
        disease_terms = self._expand_terms(topic.diseases, "disease")
        organ_terms = self._expand_terms(topic.organs, "organ")
        omics_terms = self._expand_terms(topic.omics_types, "omics")
        species_terms = topic.species if topic.species else ["Homo sapiens"]

        # 1. 疾病 x 组学类型 (最高优先级)
        queries.extend(self._combine_weighted(
            disease_terms, omics_terms, self.weights["disease_omics"]
        ))

        # 2. 器官 x 组学类型
        queries.extend(self._combine_weighted(
            organ_terms, omics_terms, self.weights["organ_omics"]
        ))

        # 3. 疾病 x 器官
        queries.extend(self._combine_weighted(
            disease_terms, organ_terms, self.weights["disease_organ"]
        ))

        # 4. 仅疾病
        for term in disease_terms:
            queries.append((term, self.weights["disease_only"]))

        # 5. 仅器官 x 组学
        for term in organ_terms:
            for om in omics_terms:
                queries.append((f"{term} {om}", self.weights["organ_only"]))

        # 6. 仅组学
        for om in omics_terms:
            queries.append((om, self.weights["omics_only"]))

        # 7. 额外关键词
        for kw in topic.extra_keywords:
            queries.append((kw, 0.2))

        # 去重
        seen: set = set()
        unique_queries: List[Tuple[str, float]] = []
        for q, w in queries:
            q_norm = " ".join(q.lower().split())
            if q_norm not in seen:
                seen.add(q_norm)
                unique_queries.append((q, w))

        # 按权重降序
        unique_queries.sort(key=lambda x: x[1], reverse=True)

        # 限制数量
        if len(unique_queries) > max_queries:
            logger.info(
                f"Generated {len(unique_queries)} queries, truncating to {max_queries}"
            )
            unique_queries = unique_queries[:max_queries]

        logger.info(f"Generated {len(unique_queries)} search queries for topic: {topic.name}")
        return unique_queries

    def _expand_terms(self, terms: List[str], dimension: str) -> List[str]:
        """展开维度中的同义词"""
        expanded: List[str] = []
        seen: set = set()

        for term in terms:
            # 标准化
            if dimension == "disease":
                canonical = self.kg.disease.get_canonical(term)
                if canonical and canonical not in seen:
                    seen.add(canonical)
                    expanded.append(canonical)
                for syn in self.kg.disease.get_synonyms(term):
                    if syn not in seen:
                        seen.add(syn)
                        expanded.append(syn)
            elif dimension == "organ":
                canonical = self.kg.organ.get_canonical(term)
                if canonical and canonical not in seen:
                    seen.add(canonical)
                    expanded.append(canonical)
                for syn in self.kg.organ.get_synonyms(term):
                    if syn not in seen:
                        seen.add(syn)
                        expanded.append(syn)
            elif dimension == "omics":
                canonical = self.kg.omics.standardize(term)
                if canonical and canonical not in seen:
                    seen.add(canonical)
                    expanded.append(canonical)
                for alias in self.kg.omics.get_aliases(term):
                    if alias not in seen:
                        seen.add(alias)
                        expanded.append(alias)

        return expanded

    def _combine_weighted(
        self, terms_a: List[str], terms_b: List[str], weight: float
    ) -> List[Tuple[str, float]]:
        """生成两个维度的笛卡尔积组合"""
        results = []
        for a, b in itertools.product(terms_a, terms_b):
            results.append((f"{a} {b}", weight))
        return results
