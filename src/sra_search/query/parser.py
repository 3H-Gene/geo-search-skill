"""Query Parser - 结构化查询解析器

功能：
- 将自然语言查询解析为结构化查询对象
- 提取疾病、组学类型、物种、组织等语义
- 支持查询意图识别

设计原则：
- 输出 deterministic（可预测）
- 字段类型强约束
"""
from dataclasses import dataclass, field


# === 查询意图枚举 ===
class QueryIntent(str):
    """查询意图类型"""
    DISEASE = "disease"  # 疾病相关
    DRUG = "drug"  # 药物/perturbation 相关
    SINGLE_CELL = "single_cell"  # 单细胞相关
    SPATIAL = "spatial"  # 空间组学相关
    GENERAL = "general"  # 一般查询


# === 组学类型映射 ===
OMICS_KEYWORDS: dict[str, set[str]] = {
    "RNA-seq": {"rna-seq", "rna seq", "rnaseq", "transcriptome", "transcriptomics", "mrna", "mrna-seq"},
    "scRNA-seq": {"scrna", "scRNA", "single cell", "single-cell", "10x", "drop-seq", "smart-seq", "cel-seq"},
    "ATAC-seq": {"atac", "atac-seq", "chromatin accessibility", "open chromatin"},
    "ChIP-seq": {"chip-seq", "chipseq", "histone", "transcription factor", "tf binding"},
    "microarray": {"microarray", "gene array", "affymetrix", "agilent"},
    "spatial": {"spatial", "spatial transcriptomics", "visium", "stereo-seq", "slide-seq"},
    "proteomics": {"proteomics", "protein", "mass spec", "lc-ms"},
    "WGS": {"wgs", "whole genome", "whole-genome sequencing"},
    "WES": {"wes", "whole exome", "whole-exome sequencing"},
    "methylation": {"methylation", "bisulfite", "epigenomics"},
}

# === 物种关键词 ===
ORGANISM_KEYWORDS: dict[str, set[str]] = {
    "Homo sapiens": {"human", "humans", "homo sapiens", "patient", "patients"},
    "Mus musculus": {"mouse", "mice", "mus musculus", "murine"},
    "Rattus norvegicus": {"rat", "rats", "rattus norvegicus"},
    "Danio rerio": {"zebrafish", "danio rerio"},
    "Drosophila melanogaster": {"fruit fly", "drosophila", "fly"},
    "Arabidopsis thaliana": {"arabidopsis", "plant"},
}

# === Perturbation 关键词 ===
PERTURBATION_KEYWORDS: dict[str, set[str]] = {
    "CRISPR": {"crispr", "cas9", "cas12", "genome editing", "gene editing"},
    "knockout": {"knockout", "ko", "knock-out", "null"},
    "knockdown": {"knockdown", "kd", "knock-down", "sirna", "rnai"},
    "drug": {"drug", "drug treatment", "compound", "inhibitor", "therapy", "chemotherapy"},
    "stimulation": {"stimulation", "stimulate", "induced", "treatment", "agonist"},
    "overexpression": {"overexpression", "over-express", "oe", "transgenic"},
    "siRNA": {"sirna", "small interfering", "rnai"},
    "chemical": {"chemical", "toxicity", "exposure", "pollutant"},
    "radiation": {"radiation", "irradiation", "uv", "gamma"},
}


@dataclass
class ParsedQuery:
    """结构化查询对象

    所有字段必须有明确类型，确保 deterministic 输出
    """
    # 原始查询
    raw_query: str

    # 解析出的语义成分
    disease_terms: list[str] = field(default_factory=list)
    tissue_terms: list[str] = field(default_factory=list)
    organism: str = ""  # "" 表示未指定
    omics_type: str = ""  # "" 表示未指定
    perturbation: bool = False
    perturbation_types: list[str] = field(default_factory=list)
    single_cell: bool = False
    spatial: bool = False

    # 意图识别
    intent: str = QueryIntent.GENERAL

    # 原始关键词（未解析为语义的词）
    raw_keywords: list[str] = field(default_factory=list)

    # 原始查询组件（用于后续处理）
    query_components: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典（强约束输出）"""
        return {
            "raw_query": self.raw_query,
            "disease_terms": self.disease_terms,
            "tissue_terms": self.tissue_terms,
            "organism": self.organism,
            "omics_type": self.omics_type,
            "perturbation": self.perturbation,
            "perturbation_types": self.perturbation_types,
            "single_cell": self.single_cell,
            "spatial": self.spatial,
            "intent": self.intent,
            "raw_keywords": self.raw_keywords,
        }

    def has_semantic_info(self) -> bool:
        """判断是否有结构化语义信息"""
        return bool(
            self.disease_terms
            or self.tissue_terms
            or self.organism
            or self.omics_type
            or self.perturbation
            or self.single_cell
            or self.spatial
        )


class QueryParser:
    """查询解析器

    将自然语言查询解析为结构化 ParsedQuery 对象
    """

    def __init__(self):
        self.omics_keywords = OMICS_KEYWORDS
        self.organism_keywords = ORGANISM_KEYWORDS
        self.perturbation_keywords = PERTURBATION_KEYWORDS

    def parse(self, query: str) -> ParsedQuery:
        """解析查询字符串

        Args:
            query: 用户输入的查询字符串

        Returns:
            ParsedQuery: 结构化查询对象

        示例:
            >>> parser = QueryParser()
            >>> result = parser.parse("breast cancer scRNA-seq")
            >>> result.disease_terms
            ['breast cancer']
            >>> result.omics_type
            'scRNA-seq'
            >>> result.single_cell
            True
        """
        query_lower = query.lower()
        tokens = self._tokenize(query_lower)

        # 初始化结果
        result = ParsedQuery(raw_query=query)

        # 1. 检测组学类型
        result.omics_type = self._detect_omics_type(tokens)

        # 2. 检测单细胞
        result.single_cell = self._detect_single_cell(query_lower)

        # 3. 检测空间组学
        result.spatial = self._detect_spatial(query_lower)

        # 4. 检测物种
        result.organism = self._detect_organism(tokens)

        # 5. 检测 perturbation
        result.perturbation, result.perturbation_types = self._detect_perturbation(tokens)

        # 6. 检测组织/器官
        result.tissue_terms = self._detect_tissue(tokens)

        # 7. 检测疾病（简化版，实际可用知识图谱）
        result.disease_terms = self._detect_disease(query_lower)

        # 8. 识别意图
        result.intent = self._infer_intent(result)

        # 9. 提取原始关键词
        result.raw_keywords = self._extract_keywords(query_lower, result)

        # 10. 保存查询组件
        result.query_components = tokens

        return result

    def _tokenize(self, text: str) -> list[str]:
        """分词"""
        import re

        # 分割并过滤
        tokens = re.findall(r"\b\w+\b", text.lower())
        return [t for t in tokens if len(t) > 1]

    def _detect_omics_type(self, tokens: list[str]) -> str:
        """检测组学类型"""
        for omics_type, keywords in self.omics_keywords.items():
            for token in tokens:
                if token in keywords:
                    return omics_type

        # 检查复合词
        for omics_type, keywords in self.omics_keywords.items():
            for kw in keywords:
                if len(kw) > 5 and kw in " ".join(tokens):
                    return omics_type

        return ""

    def _detect_single_cell(self, query: str) -> bool:
        """检测单细胞"""
        sc_keywords = {"single cell", "single-cell", "scrna", "scRNA", "10x", "drop-seq", "smart-seq", "cel-seq"}
        return any(kw in query for kw in sc_keywords)

    def _detect_spatial(self, query: str) -> bool:
        """检测空间组学"""
        spatial_keywords = {"spatial", "visium", "stereo-seq", "slide-seq", "spatial transcriptomics"}
        return any(kw in query for kw in spatial_keywords)

    def _detect_organism(self, tokens: list[str]) -> str:
        """检测物种"""
        for organism, keywords in self.organism_keywords.items():
            for token in tokens:
                if token in keywords:
                    return organism
        return ""

    def _detect_perturbation(self, tokens: list[str]) -> tuple[bool, list[str]]:
        """检测 perturbation 类型"""
        found_types = []
        for pert_type, keywords in self.perturbation_keywords.items():
            for token in tokens:
                if token in keywords:
                    found_types.append(pert_type)
                    break

        return bool(found_types), found_types

    def _detect_tissue(self, tokens: list[str]) -> list[str]:
        """检测组织/器官（简化版）"""
        # 常见组织词
        tissue_keywords = {
            "liver", "lung", "brain", "heart", "kidney", "breast", "skin",
            "blood", "muscle", "fat", "colon", "intestine", "stomach",
            "pancreas", "spleen", "thymus", "bone", "cartilage"
        }
        return [t for t in tokens if t in tissue_keywords]

    def _detect_disease(self, query: str) -> list[str]:
        """检测疾病（简化版）"""
        # 常见疾病词
        disease_keywords = {
            "cancer", "carcinoma", "tumor", "leukemia", "lymphoma",
            "diabetes", "obesity", "fibrosis", "cirrhosis",
            "arthritis", "osteoporosis", "alzheimer", "parkinson",
            "hepatitis", "nephritis", "pneumonia", "tuberculosis",
            "gout", "hyperuricemia",
        }
        found = [d for d in disease_keywords if d in query]
        return found

    def _infer_intent(self, parsed: ParsedQuery) -> str:
        """推断查询意图"""
        if parsed.single_cell:
            return QueryIntent.SINGLE_CELL
        if parsed.spatial:
            return QueryIntent.SPATIAL
        if parsed.perturbation:
            return QueryIntent.DRUG
        if parsed.disease_terms:
            return QueryIntent.DISEASE
        return QueryIntent.GENERAL

    def _extract_keywords(self, query: str, parsed: ParsedQuery) -> list[str]:
        """提取原始关键词（排除已解析的语义词）"""
        all_semantic = set()
        all_semantic.update(parsed.disease_terms)
        all_semantic.update(parsed.tissue_terms)

        # 组学类型关键词
        for kw_set in self.omics_keywords.values():
            all_semantic.update(kw_set)

        # Perturbation 关键词
        for kw_set in self.perturbation_keywords.values():
            all_semantic.update(kw_set)

        # 物种关键词
        for kw_set in self.organism_keywords.values():
            all_semantic.update(kw_set)

        # 过滤
        tokens = self._tokenize(query)
        return [t for t in tokens if t not in all_semantic and t not in {"and", "or", "the", "a", "an", "in", "of", "for", "with", "from"}]
