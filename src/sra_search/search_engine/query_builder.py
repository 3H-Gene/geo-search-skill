"""Smart Query Builder - Intelligent search query generator

Strategy:
- Same entity type (disease, omics, organ) use OR logic
- Different entity types use AND logic
- Expand keywords using knowledge graph
"""
from __future__ import annotations

from typing import Any

from sra_search.knowledge_graph.graph import KnowledgeGraph


# 疾病相关的通用关键词（不在 DOID 本体中但需要识别为疾病）
_DISEASE_FALLBACK_KEYWORDS: set[str] = {
    "fibrosis", "fibrotic", "scarring",
    "cirrhosis", "cirrhotic",
    "steatosis", "fatty",
    "nash", "nafld",
    "cholangitis", "sclerosing", "biliary",
    "pancreatitis", "nephritis", "encephalitis",
    "atherosclerosis", "arteriosclerosis",
    "hypertension", "hypertensive",
    "fibroblast", "myofibroblast",
}


class SmartQueryBuilder:
    """Intelligent query builder using knowledge graph

    Transforms simple keyword lists into optimized NCBI queries:
    - (liver fibrosis OR hepatic fibrosis) AND (single cell OR scRNA-Seq OR scRNA)
    """

    def __init__(self):
        self.kg = KnowledgeGraph()

    def _is_disease_keyword(self, term: str) -> bool:
        """检查是否是已知的疾病相关关键词（fallback 检测）"""
        return term.lower() in _DISEASE_FALLBACK_KEYWORDS

    def _try_match_disease(self, tokens: list[str], start: int) -> tuple[str | None, int]:
        """尝试匹配多词疾病短语（如 "liver fibrosis", "fatty liver"）。

        从 start 位置开始，向右贪婪匹配最长的已知疾病。
        Returns: (matched_disease_term, next_token_index)
        """
        # 构造从当前位置开始的所有可能长度的短语
        for length in range(min(4, len(tokens) - start), 0, -1):
            phrase = " ".join(tokens[start:start + length])
            entry = self.kg.disease.resolve(phrase)
            if entry:
                return phrase, start + length
            # 也尝试小写
            entry_lower = self.kg.disease.resolve(phrase.lower())
            if entry_lower:
                return phrase.lower(), start + length
        return None, start

    def build_query(self, keywords: str) -> tuple[str, dict[str, list[str]]]:
        """Build optimized search query from keywords

        Args:
            keywords: Space/comma separated keywords

        Returns:
            Tuple of (optimized_query, classification_info)
        """
        # Tokenize
        terms = keywords.replace(',', ' ').replace('，', ' ').split()

        disease_terms: list[str] = []
        organ_terms: list[str] = []     # 器官扩展词（新增）
        omics_terms: list[str] = []
        other_terms: list[str] = []

        # 首先检查整个关键词是否匹配组学类型（处理 "single cell" 这种情况）
        full_omics = self.kg.omics.resolve(keywords)
        if full_omics:
            omics_terms.extend(full_omics.get('search_terms', []))
            omics_terms.extend(full_omics.get('aliases', []))

        # 改造主循环：支持多词短语匹配 + 器官扩展收集
        i = 0
        while i < len(terms):
            term = terms[i].strip()
            if not term:
                i += 1
                continue

            # ── Step 1: 尝试多词疾病短语匹配（最长优先）────
            matched_disease, next_i = self._try_match_disease(terms, i)
            if matched_disease:
                disease_entry = self.kg.disease.resolve(matched_disease)
                if disease_entry:
                    disease_terms.extend(disease_entry.get('search_terms', []))
                    disease_terms.extend(disease_entry.get('synonyms', []))
                    # 疾病关联的器官也加入 organ_terms
                    for org in disease_entry.get('related_organs', []):
                        organ_terms.extend(self.kg.organ.get_search_terms(org))
                else:
                    # fallback 关键词命中的多词短语
                    disease_terms.append(matched_disease)
                i = next_i
                continue

            # ── Step 2: 检查是否是已知疾病（单 token）────
            disease_entry = self.kg.disease.resolve(term)
            if disease_entry:
                disease_terms.extend(disease_entry.get('search_terms', []))
                disease_terms.extend(disease_entry.get('synonyms', []))
                for org in disease_entry.get('related_organs', []):
                    organ_terms.extend(self.kg.organ.get_search_terms(org))
                i += 1
                continue

            # ── Step 3: 检查是否是组学类型（完整词）────
            is_omics = bool(self.kg.omics.resolve(term))
            if is_omics:
                omics_entry = self.kg.omics.resolve(term)
                if omics_entry:
                    omics_terms.extend(omics_entry.get('search_terms', []))
                    omics_terms.extend(omics_entry.get('aliases', []))
                i += 1
                continue

            # ── Step 4: 尝试部分匹配（如 "single" → "single cell"）────
            matched_omics = False
            for omics_name, omics_data in self.kg.omics._data.items():
                keywords_list = omics_data.get('keywords', [])
                aliases = omics_data.get('aliases', [])
                all_terms = keywords_list + aliases + [omics_name]
                if any(term.lower() in t.lower() for t in all_terms):
                    omics_terms.extend(omics_data.get('search_terms', []))
                    omics_terms.extend(omics_data.get('aliases', []))
                    matched_omics = True
                    break

            if matched_omics:
                i += 1
                continue

            # ── Step 5: 器官词 or 疾病 fallback or 其他────
            expanded = self.kg.expand_keyword(term)
            if not expanded:
                expanded = [term]

            if self.kg.organ.resolve(term):
                # 器官词：加入 organ_terms（器官扩展已在 expanded 中）
                organ_terms.extend(expanded)
            elif self._is_disease_keyword(term):
                # 疾病 fallback 关键词
                disease_terms.extend(expanded)
            else:
                other_terms.extend(expanded)

            i += 1

        # Deduplicate while preserving order
        disease_terms = list(dict.fromkeys(disease_terms))
        organ_terms = list(dict.fromkeys(organ_terms))
        omics_terms = list(dict.fromkeys(omics_terms))
        other_terms = list(dict.fromkeys(other_terms))

        # ── 知识图谱无效时的 fallback：直接用原始查询，不拆词 ──
        if not disease_terms and not omics_terms and not organ_terms and not other_terms:
            return keywords, {'diseases': [], 'omics': [], 'organ': [], 'other': [keywords]}

        # Build query parts
        query_parts: list[str] = []

        # Disease: OR（最重要，排在第一位）
        if disease_terms:
            disease_query = ' OR '.join(disease_terms[:5])
            query_parts.append(f'({disease_query})')

        # Organ: OR
        if organ_terms:
            organ_query = ' OR '.join(organ_terms[:5])
            query_parts.append(f'({organ_query})')

        # Omics: OR
        if omics_terms:
            omics_query = ' OR '.join(omics_terms[:5])
            query_parts.append(f'({omics_query})')

        # Other: OR（作为兜底）
        if other_terms:
            other_query = ' OR '.join(other_terms[:3])
            query_parts.append(f'({other_query})')

        # Combine with AND
        final_query = ' AND '.join(query_parts)

        return final_query, {
            'diseases': disease_terms,
            'omics': omics_terms,
            'organ': organ_terms,
            'other': other_terms,
        }

    def get_search_filters(self, classification: dict[str, list[str]]) -> dict[str, Any]:
        """Get suggested search filters based on classification"""
        filters: dict[str, list[str]] = {
            'suggested_sources': [],
            'suggested_databases': []
        }

        if classification['omics']:
            filters['suggested_databases'].extend(['gds', 'sra'])

        if classification['diseases']:
            filters['suggested_sources'].extend(['pubmed'])

        return filters
