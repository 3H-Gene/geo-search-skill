"""Smart Query Builder - Intelligent search query generator

Strategy:
- Same entity type (disease, omics) use OR logic
- Different entity types use AND logic
- Expand keywords using knowledge graph
"""
from __future__ import annotations

from typing import Any

from sra_search.knowledge_graph.graph import KnowledgeGraph


class SmartQueryBuilder:
    """Intelligent query builder using knowledge graph

    Transforms simple keyword lists into optimized NCBI queries:
    - (gout OR hyperuricemia) AND (single cell OR scRNA-Seq OR scRNA)
    """

    def __init__(self):
        self.kg = KnowledgeGraph()

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
        omics_terms: list[str] = []
        other_terms: list[str] = []

        # 首先检查整个关键词是否匹配组学类型（处理 "single cell" 这种情况）
        # 先尝试直接解析整个关键词
        full_omics = self.kg.omics.resolve(keywords)
        if full_omics:
            omics_terms.extend(full_omics.get('search_terms', []))
            omics_terms.extend(full_omics.get('aliases', []))

        for term in terms:
            term = term.strip()
            if not term:
                continue

            # Try to expand keyword
            expanded = self.kg.expand_keyword(term)
            if not expanded:
                expanded = [term]

            # Classify - 首先检查是否是已知疾病
            disease_entry = self.kg.disease.resolve(term)
            if disease_entry:
                # This is a disease - expand
                disease_terms.extend(disease_entry.get('search_terms', []))
                disease_terms.extend(disease_entry.get('synonyms', []))
                continue

            # 检查是否是组学类型
            is_omics = bool(self.kg.omics.resolve(term))
            if is_omics:
                # This is an omics type
                omics_entry = self.kg.omics.resolve(term)
                if omics_entry:
                    omics_terms.extend(omics_entry.get('search_terms', []))
                    omics_terms.extend(omics_entry.get('aliases', []))
            else:
                # 尝试部分匹配（如 "single" 可能匹配 "single cell"）
                for omics_name, omics_data in self.kg.omics._data.items():
                    keywords_list = omics_data.get('keywords', [])
                    aliases = omics_data.get('aliases', [])
                    all_terms = keywords_list + aliases + [omics_name]
                    # 检查 term 是否在任何关键词中
                    if any(term.lower() in t.lower() for t in all_terms):
                        omics_terms.extend(omics_data.get('search_terms', []))
                        omics_terms.extend(omics_data.get('aliases', []))
                        is_omics = True
                        break

                if not is_omics:
                    # Other keywords
                    other_terms.extend(expanded)

        # Deduplicate while preserving order
        disease_terms = list(dict.fromkeys(disease_terms))
        omics_terms = list(dict.fromkeys(omics_terms))
        other_terms = list(dict.fromkeys(other_terms))

        # ── 知识图谱无效时的 fallback：直接用原始查询，不拆词 ──
        # 如果所有分类都落空，说明知识图谱数据未加载或词不在词表中，
        # 此时直接用原始输入作为查询，比拆词后 OR 组合效果好得多。
        if not disease_terms and not omics_terms and not other_terms:
            return keywords, {'diseases': [], 'omics': [], 'other': [keywords]}

        # Build query parts
        query_parts: list[str] = []

        # Disease: OR
        if disease_terms:
            disease_query = ' OR '.join(disease_terms[:5])  # Limit to avoid query too long
            query_parts.append(f'({disease_query})')

        # Omics: OR
        if omics_terms:
            omics_query = ' OR '.join(omics_terms[:5])
            query_parts.append(f'({omics_query})')

        # Other: OR
        if other_terms:
            other_query = ' OR '.join(other_terms[:3])
            query_parts.append(f'({other_query})')

        # Combine with AND
        final_query = ' AND '.join(query_parts)

        return final_query, {
            'diseases': disease_terms,
            'omics': omics_terms,
            'other': other_terms
        }

    def get_search_filters(self, classification: dict[str, list[str]]) -> dict[str, Any]:
        """Get suggested search filters based on classification

        Returns recommended sources and strategies
        """
        filters: dict[str, list[str]] = {
            'suggested_sources': [],
            'suggested_databases': []
        }

        if classification['omics']:
            filters['suggested_databases'].extend(['gds', 'sra'])

        if classification['diseases']:
            filters['suggested_sources'].extend(['pubmed'])

        return filters
