"""主题定义与拆解逻辑

将用户输入的研究主题（如"膀胱癌虚拟细胞研究"）拆解为
疾病、器官、组学类型、物种等维度，供关键词生成器使用。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from loguru import logger

from sra_search.knowledge_graph import KnowledgeGraph


@dataclass
class TopicDefinition:
    """主题定义"""
    topic_id: str
    name: str
    description: str
    diseases: list[str] = field(default_factory=list)
    organs: list[str] = field(default_factory=list)
    omics_types: list[str] = field(default_factory=list)
    species: list[str] = field(default_factory=list)
    keywords_used: list[str] = field(default_factory=list)
    extra_keywords: list[str] = field(default_factory=list)


class TopicParser:
    """主题解析器

    将自然语言主题拆解为结构化维度。
    """

    def __init__(self, kg: KnowledgeGraph | None = None):
        self.kg = kg or KnowledgeGraph()

    def parse(self, text: str, extra_keywords: list[str] | None = None) -> TopicDefinition:
        """解析主题文本

        Args:
            text: 用户输入的主题描述
            extra_keywords: 额外的关键词

        Returns:
            TopicDefinition 结构化主题
        """
        # 使用知识图谱分析文本
        query = self.kg.analyze_text(text)

        # 补充通过扩展词识别到的实体
        expanded_query = self.kg.analyze_text(" ".join(query.expanded_terms))

        # 合并结果，去重
        all_diseases = list(dict.fromkeys(query.diseases + expanded_query.diseases))
        all_organs = list(dict.fromkeys(query.organs + expanded_query.organs))
        all_omics = list(dict.fromkeys(query.omics_types + expanded_query.omics_types))
        all_species = list(dict.fromkeys(query.species + expanded_query.species))

        # 如果没识别到疾病但文本中有关键词，尝试用 expand_keyword
        if not all_diseases:
            for term in text.split():
                expanded = self.kg.expand_keyword(term)
                for e in expanded:
                    if self.kg.disease.is_known_disease(e) and e not in all_diseases:
                        all_diseases.append(e)

        if not all_organs:
            for term in text.split():
                expanded = self.kg.expand_keyword(term)
                for e in expanded:
                    if self.kg.organ.is_known_organ(e) and e not in all_organs:
                        all_organs.append(e)

        # 如果仍然没有识别到组学类型，添加默认类型
        if not all_omics:
            all_omics = ["scRNA-seq", "bulk RNA-seq", "spatial transcriptomics",
                         "ATAC-seq", "proteomics", "GWAS"]

        # 默认物种
        if not all_species:
            all_species = ["Homo sapiens"]

        definition = TopicDefinition(
            topic_id=str(uuid.uuid4()),
            name=text.strip(),
            description=text.strip(),
            diseases=all_diseases,
            organs=all_organs,
            omics_types=all_omics,
            species=all_species,
            keywords_used=query.expanded_terms,
            extra_keywords=extra_keywords or [],
        )

        logger.info(
            f"Topic parsed: {definition.name} "
            f"({len(all_diseases)} diseases, {len(all_organs)} organs, "
            f"{len(all_omics)} omics, {len(all_species)} species)"
        )

        return definition

    def parse_from_dimensions(
        self,
        name: str,
        diseases: list[str] | None = None,
        organs: list[str] | None = None,
        omics_types: list[str] | None = None,
        species: list[str] | None = None,
        description: str = "",
    ) -> TopicDefinition:
        """从用户明确指定的维度创建主题

        用户可以直接指定疾病、器官、组学类型，跳过自动解析。
        """
        return TopicDefinition(
            topic_id=str(uuid.uuid4()),
            name=name,
            description=description or name,
            diseases=diseases or [],
            organs=organs or [],
            omics_types=omics_types or [],
            species=species or ["Homo sapiens"],
            keywords_used=[],
            extra_keywords=[],
        )
