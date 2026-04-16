"""关键词组合生成器

基于主题维度（疾病 x 器官 x 组学类型 x 物种）生成搜索关键词组合，
支持：
1. 分层权重策略（高精度疾病词 > 扩展词 > 低精度词）
2. 智能过滤无效组合和权重排序
"""

from __future__ import annotations

import itertools

from loguru import logger

from sra_search.knowledge_graph import KnowledgeGraph
from sra_search.topic_manager.topic import TopicDefinition


class KeywordGenerator:
    """关键词组合生成器"""

    def __init__(self, kg: KnowledgeGraph | None = None):
        self.kg = kg or KnowledgeGraph()

        # 默认组学类型（如果主题未指定）
        self.default_omics = [
            "scRNA-seq", "bulk RNA-seq", "spatial transcriptomics",
            "ATAC-seq", "ChIP-seq", "proteomics", "GWAS",
            "WGS", "WES", "Ribo-seq", "single-cell multi-omics",
        ]

        # 疾病术语分层权重（Cursor review 建议 P2-5）
        # 高精度词（如 gout）权重高于扩展词（如 hyperuricemia）和低精度词（如 uric acid）
        self.disease_tier_weights: dict[str, float] = {
            "tier1_high": 1.0,   # 高精度：gout, gouty arthritis
            "tier2_expand": 0.8,  # 扩展：hyperuricemia, hyperuricemic
            "tier3_broad": 0.5,   # 低精度：uric acid, urate, monosodium urate
        }

        # 组合权重（影响搜索优先级）
        self.weights: dict[str, float] = {
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
    ) -> list[tuple[str, float]]:
        """生成关键词组合

        Args:
            topic: 主题定义
            max_queries: 最大组合数量

        Returns:
            (搜索词, 权重) 列表，按权重降序排列
        """
        queries: list[tuple[str, float]] = []

        # 展开维度中的同义词（疾病带层级权重）
        disease_terms = self._expand_disease_terms_with_tier(topic.diseases)
        organ_terms = self._expand_terms(topic.organs, "organ")
        omics_terms = self._expand_terms(topic.omics_types, "omics")

        # 1. 疾病 x 组学类型 (最高优先级，按疾病层级加权)
        for disease_term, disease_weight in disease_terms:
            for om in omics_terms:
                combined_weight = disease_weight * self.weights["disease_omics"]
                queries.append((f"{disease_term} {om}", combined_weight))

        # 2. 器官 x 组学类型
        queries.extend(self._combine_weighted(
            organ_terms, omics_terms, self.weights["organ_omics"]
        ))

        # 3. 疾病 x 器官（按疾病层级加权）
        for disease_term, disease_weight in disease_terms:
            for organ in organ_terms:
                combined_weight = disease_weight * self.weights["disease_organ"]
                queries.append((f"{disease_term} {organ}", combined_weight))

        # 4. 仅疾病（按层级权重）
        for disease_term, disease_weight in disease_terms:
            queries.append((disease_term, disease_weight * self.weights["disease_only"]))

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
        unique_queries: list[tuple[str, float]] = []
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

    def _expand_disease_terms_with_tier(
        self, diseases: list[str]
    ) -> list[tuple[str, float]]:
        """展开疾病同义词并分配层级权重。

        根据疾病的精准程度分配不同权重（Cursor review P2-5）：
        - Tier 1 (高权重): gout, gouty arthritis - 最精确
        - Tier 2 (中权重): hyperuricemia, hyperuricemic - 扩展概念
        - Tier 3 (低权重): uric acid, urate, monosodium urate - 相关但宽泛

        Args:
            diseases: 疾病名称列表

        Returns:
            [(疾病词, 权重), ...] 列表
        """
        # 高精度疾病词（Tier 1）
        tier1_keywords = {"gout", "gouty arthritis", "gouty", "podagra"}
        # 扩展概念（Tier 2）
        tier2_keywords = {"hyperuricemia", "hyperuricemic", "hyperuricemic state"}
        # 低精度相关词（Tier 3）
        tier3_keywords = {"uric acid", "urate", "monosodium urate", "msu", "urates", "tophus"}

        results: list[tuple[str, float]] = []
        seen: set = set()

        for disease in diseases:
            # 获取标准名和所有同义词
            canonical = self.kg.disease.get_canonical(disease)
            synonyms = list(self.kg.disease.get_synonyms(disease))
            all_names = [canonical] + synonyms if canonical else synonyms

            for name in all_names:
                if name in seen:
                    continue
                seen.add(name)

                # 确定层级权重
                name_lower = name.lower()
                if name_lower in tier1_keywords:
                    weight = self.disease_tier_weights["tier1_high"]
                    tier = 1
                elif name_lower in tier2_keywords:
                    weight = self.disease_tier_weights["tier2_expand"]
                    tier = 2
                elif name_lower in tier3_keywords:
                    weight = self.disease_tier_weights["tier3_broad"]
                    tier = 3
                else:
                    # 对于知识图谱中新增的疾病词，默认 Tier 2
                    weight = self.disease_tier_weights["tier2_expand"]
                    tier = 2

                results.append((name, weight))
                logger.debug(f"[Keyword tier] '{name}' -> Tier {tier} (weight={weight})")

        return results

    def _expand_terms(self, terms: list[str], dimension: str) -> list[str]:
        """展开维度中的同义词"""
        expanded: list[str] = []
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
        self, terms_a: list[str], terms_b: list[str], weight: float
    ) -> list[tuple[str, float]]:
        """生成两个维度的笛卡尔积组合"""
        results = []
        for a, b in itertools.product(terms_a, terms_b):
            results.append((f"{a} {b}", weight))
        return results
