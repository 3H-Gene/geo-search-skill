"""知识图谱核心 - 语义扩展引擎

统一入口，整合缩写映射、MeSH 同义词、疾病本体、器官本体和组学类型映射，
提供关键词扩展、实体识别和搜索词生成功能。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from sra_search.knowledge_graph.abbreviation_map import AbbreviationMapper
from sra_search.knowledge_graph.disease_ontology import DiseaseOntology
from sra_search.knowledge_graph.mesh_mapper import MeshMapper
from sra_search.knowledge_graph.omics_types import OmicsTypeMapper
from sra_search.knowledge_graph.organ_ontology import OrganOntology


@dataclass
class SemanticQuery:
    """语义扩展后的查询结果"""
    original_text: str
    diseases: list[str] = field(default_factory=list)
    organs: list[str] = field(default_factory=list)
    omics_types: list[str] = field(default_factory=list)
    species: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)
    resolved_abbreviations: list[str] = field(default_factory=list)
    confidence: float = 0.0


class KnowledgeGraph:
    """知识图谱统一入口

    整合所有本体数据，提供：
    1. 关键词语义扩展（缩写 -> 全称 -> 关联词）
    2. 实体识别（从文本中提取疾病、器官、组学类型）
    3. 搜索词生成（为搜索引擎准备扩展查询）
    """

    def __init__(self):
        self.abbr = AbbreviationMapper()
        self.mesh = MeshMapper()
        self.disease = DiseaseOntology()
        self.organ = OrganOntology()
        self.omics = OmicsTypeMapper()

    def resolve_abbreviation(self, term: str) -> dict[str, Any] | None:
        """解析缩写"""
        return self.abbr.resolve(term)

    def expand_keyword(self, keyword: str) -> list[str]:
        """将单个关键词扩展为搜索词列表

        优先级：缩写解析 > 疾病本体 > 器官本体 > 组学类型 > MeSH > 原样
        """
        results: list[str] = []
        seen: set = set()

        def _add(terms: list[str]):
            for t in terms:
                t_low = t.lower()
                if t_low not in seen:
                    seen.add(t_low)
                    results.append(t)

        # 1. 缩写解析
        if self.abbr.is_abbreviation(keyword):
            entry = self.abbr.resolve(keyword)
            if entry:
                _add(entry.get("search_terms", []))
                _add(entry.get("related_diseases", []))
                _add(entry.get("related_organs", []))
                full_name = entry.get("full_name", "")
                if full_name:
                    _add([full_name])

        # 2. 疾病本体
        disease_entry = self.disease.resolve(keyword)
        if disease_entry:
            _add(disease_entry.get("search_terms", []))
            _add(disease_entry.get("synonyms", []))
            _add(disease_entry.get("related_organs", []))

        # 3. 器官本体
        organ_entry = self.organ.resolve(keyword)
        if organ_entry:
            _add(organ_entry.get("search_terms", []))
            _add(organ_entry.get("synonyms", []))
            _add([organ_entry.get("adjective", "")] if organ_entry.get("adjective") else [])

        # 4. 组学类型
        omics_entry = self.omics.resolve(keyword)
        if omics_entry:
            _add(omics_entry.get("aliases", []))
            _add(omics_entry.get("search_boost_terms", []))

        # 5. MeSH 同义词
        mesh_entry = self.mesh.resolve(keyword)
        if mesh_entry:
            _add(mesh_entry.get("synonyms", []))

        # 6. 至少保留原始输入
        if not results:
            _add([keyword])

        return results

    def _extract_phrases(self, text: str) -> list[str]:
        """从文本中提取已知实体短语（多词组合）

        先尝试匹配最长的已知短语，避免 "bladder" 和 "cancer" 被分开匹配。
        """
        found: list[str] = []
        seen_spans: list[tuple] = []

        def _mark_span(start: int, end: int):
            """标记已匹配的文本范围"""
            for s, e in seen_spans:
                if start < e and end > s:
                    return  # 重叠，跳过
            seen_spans.append((start, end))

        # 收集所有已知短语并按长度降序排列
        known_phrases: list[str] = []
        for _d_name, d_entry in self.disease.get_all_diseases().items():
            known_phrases.append(d_entry["canonical"])
            known_phrases.extend(d_entry.get("synonyms", []))
        for _o_name, o_entry in self.organ.get_all_organs().items():
            known_phrases.append(o_entry["canonical"])
            known_phrases.extend(o_entry.get("synonyms", []))

        # 去重并按长度降序
        known_phrases = list(dict.fromkeys(known_phrases))
        known_phrases.sort(key=len, reverse=True)

        for phrase in known_phrases:
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            for m in pattern.finditer(text):
                _mark_span(m.start(), m.end())
                if phrase.lower() not in [f.lower() for f in found]:
                    found.append(phrase)

        # 将未匹配的单词也加入
        words = re.findall(r"\b\w+\b", text)
        for word in words:
            is_covered = any(s <= text.lower().index(word.lower()) < e
                           for s, e in seen_spans
                           if word.lower() in text.lower()[s:e])
            if not is_covered and word.lower() not in [f.lower() for f in found]:
                found.append(word)

        return found

    def analyze_text(self, text: str) -> SemanticQuery:
        """从文本中识别实体并进行语义扩展

        Args:
            text: 用户输入的自由文本

        Returns:
            SemanticQuery 包含识别出的疾病、器官、组学类型和扩展词
        """
        query = SemanticQuery(original_text=text)

        # 提取缩写
        abbreviations = self.abbr.extract_abbreviations(text)
        query.resolved_abbreviations = abbreviations

        # 提取组学类型
        omics_matches = self.omics.detect_from_text(text)
        query.omics_types = [name for name, _ in omics_matches]

        # 生成候选词列表：先提取已知的多词短语，再用分词结果
        all_terms: list[str] = list(abbreviations) + self._extract_phrases(text)

        # 识别疾病
        disease_seen: set = set()
        organ_seen: set = set()
        species_seen: set = set()
        for term in all_terms:
            term = term.strip()
            if not term:
                continue
            if self.disease.is_known_disease(term):
                canonical = self.disease.get_canonical(term)
                if canonical not in disease_seen:
                    disease_seen.add(canonical)
                    query.diseases.append(canonical)
                    for org in self.disease.get_related_organs(canonical):
                        if org not in organ_seen:
                            organ_seen.add(org)
                            query.organs.append(org)
                    for sp in self.disease.get_related_species(canonical):
                        if sp not in species_seen:
                            species_seen.add(sp)
                            query.species.append(sp)
            if self.abbr.is_abbreviation(term):
                for d in self.abbr.get_related_diseases(term):
                    if d not in disease_seen:
                        disease_seen.add(d)
                        query.diseases.append(d)

        # 识别器官
        for term in all_terms:
            term = term.strip()
            if not term:
                continue
            if self.organ.is_known_organ(term):
                canonical = self.organ.get_canonical(term)
                if canonical not in organ_seen:
                    organ_seen.add(canonical)
                    query.organs.append(canonical)

        # 生成扩展搜索词
        expanded: set = set()
        for disease in query.diseases:
            for t in self.disease.get_search_terms(disease):
                expanded.add(t)
        for organ in query.organs:
            for t in self.organ.get_search_terms(organ):
                expanded.add(t)
        for omics in query.omics_types:
            for t in self.omics.get_search_terms(omics):
                expanded.add(t)
        query.expanded_terms = sorted(expanded)

        # 计算置信度（基于识别到的实体数量）
        entity_count = len(query.diseases) + len(query.organs) + len(query.omics_types)
        query.confidence = min(1.0, entity_count * 0.25)

        return query

    def generate_search_queries(
        self,
        diseases: list[str] | None = None,
        organs: list[str] | None = None,
        omics_types: list[str] | None = None,
        species: list[str] | None = None,
    ) -> list[str]:
        """生成搜索查询词的笛卡尔积组合

        Args:
            diseases: 疾病列表
            organs: 器官列表
            omics_types: 组学类型列表
            species: 物种列表

        Returns:
            搜索词组合列表
        """
        # 规范化输入
        diseases = diseases or []
        organs = organs or []
        omics_types = omics_types or []
        species = species or []

        # 展开每个维度
        disease_terms: list[str] = []
        for d in diseases:
            disease_terms.extend(self.disease.get_search_terms(d))
        disease_terms = list(dict.fromkeys(disease_terms)) or diseases

        organ_terms: list[str] = []
        for o in organs:
            organ_terms.extend(self.organ.get_search_terms(o))
        organ_terms = list(dict.fromkeys(organ_terms)) or organs

        omics_terms: list[str] = []
        for om in omics_types:
            omics_terms.extend(self.omics.get_search_terms(om))
        omics_terms = list(dict.fromkeys(omics_terms)) or omics_types

        # 生成组合
        queries: list[str] = []

        # 疾病 x 组学类型 (最高优先级)
        for d in disease_terms:
            for om in omics_terms:
                queries.append(f"{d} {om}")

        # 器官 x 组学类型
        for o in organ_terms:
            for om in omics_terms:
                queries.append(f"{o} {om}")

        # 疾病 x 器官
        for d in disease_terms:
            for o in organ_terms:
                if f"{d} {o}" not in queries:
                    queries.append(f"{d} {o}")

        # 仅疾病
        for d in disease_terms:
            if d not in queries:
                queries.append(d)

        # 仅器官 x 组学
        for o in organ_terms:
            if o not in queries:
                for om in omics_terms:
                    queries.append(f"{o} {om}")

        # 仅组学
        for om in omics_terms:
            if om not in queries:
                queries.append(om)

        # 限制组合数量避免过多请求
        if len(queries) > 200:
            logger.warning(
                f"Generated {len(queries)} queries, truncating to 200"
            )
            queries = queries[:200]

        return queries

    def get_organ_disease_associations(self, organ: str) -> list[str]:
        """获取与器官相关的所有疾病"""
        return self.disease.find_diseases_by_organ(organ)

    def get_disease_organ_associations(self, disease: str) -> list[str]:
        """获取与疾病相关的所有器官"""
        return self.disease.get_related_organs(disease)

    def standardize_organ(self, name: str) -> str:
        """标准化器官名"""
        return self.organ.get_canonical(name)

    def standardize_disease(self, name: str) -> str:
        """标准化疾病名"""
        return self.disease.get_canonical(name)

    def standardize_omics(self, name: str) -> str:
        """标准化组学类型"""
        return self.omics.standardize(name)
