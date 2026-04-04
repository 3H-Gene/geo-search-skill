"""Query Expander - 查询词扩展器

功能：
- 使用本体知识图谱扩展查询词
- 支持疾病同义词、器官、组学类型扩展
- 生成 E-utilities 可用的查询字符串

设计原则：
- 单一职责
- 与知识图谱解耦
- 失败处理：扩展失败时使用原始查询
"""
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExpansionResult:
    """扩展结果"""
    original_query: str
    expanded_query: str  # 可直接用于 E-utilities 的查询字符串
    expansions: dict[str, list[str]] = field(default_factory=dict)  # 各类型的扩展词
    used_knowledge_graph: bool = False
    fallback_used: bool = False

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "expanded_query": self.expanded_query,
            "expansions": self.expansions,
            "used_knowledge_graph": self.used_knowledge_graph,
            "fallback_used": self.fallback_used,
        }


class QueryExpander:
    """查询词扩展器

    使用本体知识扩展查询，提供更丰富的语义搜索能力
    """

    def __init__(self, ontology_dir: Path | None = None):
        """初始化

        Args:
            ontology_dir: 本体知识库目录，默认为 src/sra_search/data/ontologies
        """
        if ontology_dir is None:
            # 默认路径
            base = Path(__file__).parent.parent.parent.parent / "data" / "ontologies"
            ontology_dir = base

        self.ontology_dir = Path(ontology_dir)
        self._ontology_cache: dict[str, dict] = {}

    def expand(self, query: str, parsed_query: dict | None = None) -> ExpansionResult:
        """扩展查询词

        Args:
            query: 原始查询字符串
            parsed_query: 可选的预解析查询结果（来自 QueryParser）

        Returns:
            ExpansionResult: 扩展结果对象

        示例:
            >>> expander = QueryExpander()
            >>> result = expander.expand("gout scRNA-seq")
            >>> result.expanded_query
            '(gout OR hyperuricemia OR "uric acid") AND (scRNA-Seq OR "10x Genomics")'
        """
        expansions: dict[str, list[str]] = {
            "disease": [],
            "organ": [],
            "omics": [],
            "organism": [],
        }

        # 解析查询
        from sra_search.query.parser import QueryParser

        parser = QueryParser()
        if parsed_query is None:
            parsed = parser.parse(query)
            parsed_query = parsed.to_dict()

        # 1. 扩展疾病词
        if parsed_query.get("disease_terms"):
            disease_expansions = self._expand_diseases(parsed_query["disease_terms"])
            expansions["disease"] = disease_expansions

        # 2. 扩展器官词
        if parsed_query.get("tissue_terms"):
            organ_expansions = self._expand_organs(parsed_query["tissue_terms"])
            expansions["organ"] = organ_expansions

        # 3. 扩展组学类型
        if parsed_query.get("omics_type"):
            omics_expansions = self._expand_omics(parsed_query["omics_type"])
            expansions["omics"] = omics_expansions

        # 4. 构建扩展后的查询字符串
        expanded_query = self._build_expanded_query(query, parsed_query, expansions)

        return ExpansionResult(
            original_query=query,
            expanded_query=expanded_query,
            expansions=expansions,
            used_knowledge_graph=bool(expansions["disease"] or expansions["organ"]),
            fallback_used=False,
        )

    def _expand_diseases(self, disease_terms: list[str]) -> list[str]:
        """扩展疾病词（使用 DOID 本体）"""
        if not disease_terms:
            return []

        # 加载 DOID 本体
        doid_data = self._load_ontology("doid_hierarchy.json")
        if not doid_data:
            return disease_terms

        expanded = []
        for disease in disease_terms:
            disease_lower = disease.lower()

            # 查找直接匹配
            if disease_lower in doid_data:
                synonyms = doid_data[disease_lower].get("synonyms", [])
                expanded.append(disease)
                expanded.extend(synonyms)
            else:
                # 模糊匹配
                for key, value in doid_data.items():
                    if disease_lower in key or disease_lower in " ".join(value.get("synonyms", [])):
                        expanded.append(value.get("name", disease))
                        expanded.extend(value.get("synonyms", []))
                        break

        # 去重并限制数量
        return list(dict.fromkeys(expanded))[:10]

    def _expand_organs(self, tissue_terms: list[str]) -> list[str]:
        """扩展器官词（使用 Uberon 本体）"""
        if not tissue_terms:
            return []

        # 加载 Uberon 本体
        uberon_data = self._load_ontology("uberon_organs.json")
        if not uberon_data:
            return tissue_terms

        expanded = []
        for tissue in tissue_terms:
            tissue_lower = tissue.lower()

            if tissue_lower in uberon_data:
                synonyms = uberon_data[tissue_lower].get("synonyms", [])
                expanded.append(tissue)
                expanded.extend(synonyms)
            else:
                for key, value in uberon_data.items():
                    if tissue_lower in key or tissue_lower in " ".join(value.get("synonyms", [])):
                        expanded.append(value.get("name", tissue))
                        expanded.extend(value.get("synonyms", []))
                        break

        return list(dict.fromkeys(expanded))[:10]

    def _expand_omics(self, omics_type: str) -> list[str]:
        """扩展组学类型"""
        omics_data = self._load_ontology("omics_types.json")
        if not omics_data:
            return [omics_type]

        omics_lower = omics_type.lower()
        if omics_lower in omics_data:
            synonyms = omics_data[omics_lower].get("synonyms", [])
            return [omics_type] + synonyms

        return [omics_type]

    def _load_ontology(self, filename: str) -> dict | None:
        """加载本体数据（带缓存）"""
        if filename in self._ontology_cache:
            return self._ontology_cache[filename]

        filepath = self.ontology_dir / filename
        if not filepath.exists():
            return None

        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
                self._ontology_cache[filename] = data
                return data
        except (OSError, json.JSONDecodeError):
            return None

    def _build_expanded_query(
        self,
        query: str,
        parsed_query: dict,
        expansions: dict[str, list[str]]
    ) -> str:
        """构建扩展后的查询字符串

        生成可直接用于 E-utilities 的查询格式
        """
        parts = []

        # 疾病部分
        if expansions.get("disease"):
            disease_str = " OR ".join(f'"{d}"' for d in expansions["disease"])
            parts.append(f"({disease_str})")

        # 组学类型部分
        if expansions.get("omics"):
            omics_str = " OR ".join(f'"{o}"' for o in expansions["omics"])
            parts.append(f"({omics_str})")

        # 如果没有扩展成功，使用原始查询
        if not parts:
            return query

        return " AND ".join(parts)


class FallbackExpander:
    """降级扩展器（无知识图谱时使用）"""

    # 常见疾病同义词（硬编码降级方案）
    DISEASE_SYNONYMS = {
        "gout": ["hyperuricemia", "uric acid", "urate"],
        "diabetes": ["T2D", "type 2 diabetes", "hyperglycemia"],
        "cancer": ["carcinoma", "tumor", "malignancy", "neoplasm"],
        "fibrosis": ["fibrotic", "scarring"],
        "hepatitis": ["liver inflammation", "hepatic inflammation"],
        "obesity": ["adiposity", "overweight"],
    }

    # 组学类型扩展
    OMICS_SYNONYMS = {
        "rna-seq": ["RNA sequencing", "transcriptome", "rnaseq"],
        "scrna-seq": ["single cell RNA", "10x", "drop-seq", "smart-seq"],
        "atac-seq": ["chromatin accessibility", "open chromatin"],
        "chip-seq": ["histone modification", "tf binding"],
    }

    def expand(self, query: str) -> str:
        """简单扩展（仅使用硬编码同义词）"""
        query_lower = query.lower()

        for disease, synonyms in self.DISEASE_SYNONYMS.items():
            if disease in query_lower:
                synonym_str = " OR ".join(f'"{s}"' for s in synonyms)
                return f'({disease} OR {synonym_str})'

        for omics, synonyms in self.OMICS_SYNONYMS.items():
            if omics in query_lower:
                synonym_str = " OR ".join(f'"{s}"' for s in synonyms)
                return f'({omics} OR {synonym_str})'

        return query
