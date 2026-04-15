"""标准输出 Schema 定义（强约束协议）

geo-search-skill 应被严格定义为 GEO 数据集发现（Data Discovery）工具：
- 只负责数据检索、元数据解析、结构化结果输出
- 明确不承担：数据下载、表达矩阵解析、数据预处理

===============================================================================
协议级定义（强约束）
===============================================================================

所有输出字段必须遵循以下规则：
1. 字段类型必须稳定（string/int/bool/List）
2. 可选字段必须有明确默认值
3. 不返回 null（使用空字符串或空列表）
4. 字段名不可改变（向下兼容）

输出 Schema（JSON）:

    {
      "gse_id": "string",           # 必须，唯一主键
      "title": "string",            # 必须
      "organism": "string",         # 必须，"" 表示未知
      "data_type": "string",       # 必须，枚举值
      "sample_count": int,         # 必须，>= 0
      "platform": "string",         # 可选
      "single_cell": bool,          # 必须
      "granularity": "string",      # 枚举：single_cell/bulk/spatial/unknown
      "has_perturbation": bool,    # 必须
      "perturbation_types": [],    # 必须，List[string]
      "disease": "string",          # 可选
      "tissue": "string",          # 可选
      "organ": "string",           # 可选
      "summary": "string",         # 可选
      "keywords": [],              # 必须，List[string]
      "pubmed_ids": [],            # 必须，List[string]
      "sra_ids": [],               # 必须，List[string]
      "bioproject_ids": [],        # 必须，List[string]
      "publication_date": "string", # 可选，YYYY-MM-DD
      "journal": "string",          # 可选
      "series_matrix_available": bool,
      "ftp_link": "string",        # 不提供（只做发现）
      "relevance_score": float,    # 0.0-1.0
      "recency_score": float,      # 0.0-1.0
      "quality_score": float,      # 0.0-1.0
      "total_score": float,        # 0.0-1.0
      "metadata_version": "1.0",    # 协议版本
      "extracted_at": "ISO8601",   # 时间戳
      "metadata_hash": "string"    # 内容哈希
    }

===============================================================================
接口契约
===============================================================================

1. ID 驱动接口（与 gse-downloader 解耦）:

    { "gse_id": "GSE12345" }

2. 结构化 payload 接口:

    {
      "gse_id": "GSE12345",
      "preferred_format": "metadata",
      "metadata": { ... }
    }
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DataType(str, Enum):
    """数据类型枚举"""
    RNA_SEQ = "RNA-seq"
    microarray = "microarray"
    ATAC_SEQ = "ATAC-seq"
    CHIP_SEQ = "ChIP-seq"
    scRNA_SEQ = "scRNA-seq"  # noqa: N815
    scATAC_SEQ = "scATAC-seq"  # noqa: N815
    SPATIAL = "spatial"
    PROTEOMICS = "proteomics"
    METAGENOMICS = "metagenomics"
    OTHER = "other"


class GranularityType(str, Enum):
    """数据粒度类型"""
    SINGLE_CELL = "single_cell"
    BULK = "bulk"
    SPATIAL = "spatial"
    UNKNOWN = "unknown"


class PerturbationType(str, Enum):
    """扰动类型枚举"""
    CRISPR = "CRISPR"
    KNOCKOUT = "knockout"
    KNOCKDOWN = "knockdown"
    DRUG = "drug"
    STIMULATION = "stimulation"
    OVEREXPRESSION = "overexpression"
    SIRNA = "siRNA"
    CHEMICAL = "chemical"
    RADIATION = "radiation"
    NONE = "none"


class DataQuality(str, Enum):
    """数据质量评估"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DatasetSchema:
    """标准输出 Schema（与 gse-downloader 解耦）

    设计原则：
    - gse_id 为唯一主键
    - 输出必须稳定、可解析
    - 不返回表达矩阵或下载链接（只提供元数据）
    """
    # === 核心标识 ===
    gse_id: str

    # === 基本信息 ===
    title: str = ""
    organism: str = ""
    data_type: str = DataType.OTHER.value
    sample_count: int = 0
    platform: str = ""

    # === 生物学语义 ===
    single_cell: bool = False
    granularity: str = GranularityType.UNKNOWN.value
    has_perturbation: bool = False
    perturbation_types: list[str] = field(default_factory=list)

    # === 疾病与组织 ===
    disease: str = ""
    tissue: str = ""
    organ: str = ""

    # === 描述与链接 ===
    summary: str = ""
    overall_design: str = ""          # 实验设计详细描述（GEO overall_design 字段）
    keywords: list[str] = field(default_factory=list)

    # === 数据文件信息 ===
    supplementary_files: list[dict] = field(default_factory=list)  # [{name, type, size}]
    series_matrix_available: bool = False
    ftp_link: str = ""
    has_processed_matrix: bool = False   # 有已处理的表达矩阵（count/RPKM/h5/h5ad 等）
    raw_only: bool = False               # 仅有原始数据（FASTQ/SRA，无处理矩阵）

    # === 关联 ID ===
    pubmed_ids: list[str] = field(default_factory=list)
    sra_ids: list[str] = field(default_factory=list)
    bioproject_ids: list[str] = field(default_factory=list)

    # === 元数据 ===
    publication_date: str = ""
    journal: str = ""

    # === 排序与质量 ===
    relevance_score: float = 0.0
    recency_score: float = 0.0
    quality_score: float = 0.0
    total_score: float = 0.0

    # === 审计字段 ===
    metadata_version: str = "1.0"
    extracted_at: str = field(default_factory=_now_iso)
    metadata_hash: str = ""

    # === LLM 分析字段（V2 新增）===
    llm_one_sentence_summary: str = ""    # 一句话总结
    llm_sample_grouping: str = ""         # 样本分组 (如 "6病例+6对照")
    llm_cell_count: str = ""              # 细胞数 (如 "15K")
    llm_relevance_reason: str = ""        # 相关性理由
    llm_analyzed_at: str = ""             # LLM 分析时间（ISO 8601）
    llm_model: str = ""                   # LLM 模型名称

    def compute_hash(self) -> str:
        """计算元数据哈希（用于去重和缓存）"""
        fields_to_hash = {
            "gse_id": self.gse_id,
            "title": self.title,
            "organism": self.organism,
            "data_type": self.data_type,
            "sample_count": self.sample_count,
            "single_cell": self.single_cell,
            "has_perturbation": self.has_perturbation,
            "perturbation_types": sorted(self.perturbation_types),
            "disease": self.disease,
            "tissue": self.tissue,
            "pubmed_ids": sorted(self.pubmed_ids),
            "sra_ids": sorted(self.sra_ids),
        }
        raw = json.dumps(fields_to_hash, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（JSON 序列化友好）"""
        self.metadata_hash = self.compute_hash()
        return {
            "gse_id": self.gse_id,
            "title": self.title,
            "organism": self.organism,
            "data_type": self.data_type,
            "sample_count": self.sample_count,
            "platform": self.platform,
            "single_cell": self.single_cell,
            "granularity": self.granularity,
            "has_perturbation": self.has_perturbation,
            "perturbation_types": self.perturbation_types,
            "disease": self.disease,
            "tissue": self.tissue,
            "organ": self.organ,
            "summary": self.summary,
            "overall_design": self.overall_design,
            "keywords": self.keywords,
            "supplementary_files": self.supplementary_files,
            "series_matrix_available": self.series_matrix_available,
            "ftp_link": self.ftp_link,
            "has_processed_matrix": self.has_processed_matrix,
            "raw_only": self.raw_only,
            "pubmed_ids": self.pubmed_ids,
            "sra_ids": self.sra_ids,
            "bioproject_ids": self.bioproject_ids,
            "publication_date": self.publication_date,
            "journal": self.journal,
            "relevance_score": self.relevance_score,
            "recency_score": self.recency_score,
            "quality_score": self.quality_score,
            "total_score": self.total_score,
            "metadata_version": self.metadata_version,
            "extracted_at": self.extracted_at,
            "metadata_hash": self.metadata_hash,
            # LLM 分析字段
            "llm_one_sentence_summary": self.llm_one_sentence_summary,
            "llm_sample_grouping": self.llm_sample_grouping,
            "llm_cell_count": self.llm_cell_count,
            "llm_relevance_reason": self.llm_relevance_reason,
            "llm_analyzed_at": self.llm_analyzed_at,
            "llm_model": self.llm_model,
        }

    def to_id_payload(self) -> dict[str, str]:
        """转换为 ID 驱动 payload（与 gse-downloader 解耦）"""
        return {"gse_id": self.gse_id}

    def to_full_payload(self, preferred_format: str = "metadata", include_raw: bool = False) -> dict[str, Any]:
        """转换为结构化 payload（高级接口）"""
        payload = {
            "gse_id": self.gse_id,
            "preferred_format": preferred_format,
            "metadata": {
                "title": self.title,
                "organism": self.organism,
                "data_type": self.data_type,
                "sample_count": self.sample_count,
            },
        }
        if include_raw:
            payload["raw_metadata"] = self.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatasetSchema:
        """从字典创建 Schema"""
        return cls(
            gse_id=data.get("gse_id", ""),
            title=data.get("title", ""),
            organism=data.get("organism", ""),
            data_type=data.get("data_type", DataType.OTHER.value),
            sample_count=data.get("sample_count", 0),
            platform=data.get("platform", ""),
            single_cell=data.get("single_cell", False),
            granularity=data.get("granularity", GranularityType.UNKNOWN.value),
            has_perturbation=data.get("has_perturbation", False),
            perturbation_types=data.get("perturbation_types", []),
            disease=data.get("disease", ""),
            tissue=data.get("tissue", ""),
            organ=data.get("organ", ""),
            summary=data.get("summary", ""),
            overall_design=data.get("overall_design", ""),
            keywords=data.get("keywords", []),
            supplementary_files=data.get("supplementary_files", []),
            series_matrix_available=data.get("series_matrix_available", False),
            ftp_link=data.get("ftp_link", ""),
            has_processed_matrix=data.get("has_processed_matrix", False),
            raw_only=data.get("raw_only", False),
            pubmed_ids=data.get("pubmed_ids", []),
            sra_ids=data.get("sra_ids", []),
            bioproject_ids=data.get("bioproject_ids", []),
            publication_date=data.get("publication_date", ""),
            journal=data.get("journal", ""),
            relevance_score=data.get("relevance_score", 0.0),
            recency_score=data.get("recency_score", 0.0),
            quality_score=data.get("quality_score", 0.0),
            total_score=data.get("total_score", 0.0),
            metadata_version=data.get("metadata_version", "1.0"),
            extracted_at=data.get("extracted_at", _now_iso()),
            metadata_hash=data.get("metadata_hash", ""),
            # LLM 分析字段
            llm_one_sentence_summary=data.get("llm_one_sentence_summary", ""),
            llm_sample_grouping=data.get("llm_sample_grouping", ""),
            llm_cell_count=data.get("llm_cell_count", ""),
            llm_relevance_reason=data.get("llm_relevance_reason", ""),
            llm_analyzed_at=data.get("llm_analyzed_at", ""),
            llm_model=data.get("llm_model", ""),
        )


@dataclass
class SearchResultSchema:
    """搜索结果 Schema（支持排序和摘要）"""
    query: str
    total_found: int = 0
    results: list[DatasetSchema] = field(default_factory=list)

    # === 统计摘要（用于 Agent 决策） ===
    stats: dict[str, Any] = field(default_factory=dict)

    # === 扩展查询词（用于调试） ===
    expanded_queries: list[str] = field(default_factory=list)

    # === LLM 辅助字段（V2 新增，可选） ===
    llm_summary: str = ""          # LLM 生成的结果摘要
    llm_model: str = ""            # 使用的 LLM 模型
    llm_scored_count: int = 0      # 经 LLM 评分的数据集数量
    llm_query_intent: dict[str, Any] = field(default_factory=dict)  # LLM 解析的查询意图

    # === 元数据 ===
    query_hash: str = ""
    searched_at: str = field(default_factory=_now_iso)

    def compute_hash(self) -> str:
        """计算查询哈希（用于缓存）"""
        raw = json.dumps({"query": self.query, "timestamp": self.searched_at}, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    def compute_stats(self) -> dict[str, Any]:
        """计算结果统计"""
        results = self.results
        sc_rna = sum(1 for r in results if r.single_cell)
        with_perturbation = sum(1 for r in results if r.has_perturbation)
        bulk_rna = sum(1 for r in results if r.data_type in [DataType.RNA_SEQ.value, DataType.microarray.value] and not r.single_cell)

        self.stats = {
            "total_found": self.total_found,
            "returned": len(results),
            "scRNA_seq": sc_rna,
            "bulk_RNA": bulk_rna,
            "with_perturbation": with_perturbation,
            "single_cell": sc_rna,
            "perturbation": with_perturbation,
        }
        return self.stats

    def sort_results(self, top_n: int = 50, weights: dict[str, float] | None = None) -> list[DatasetSchema]:
        """排序结果（默认使用 relevance + sample_size + recency + quality）"""
        if weights is None:
            weights = {"relevance": 0.4, "recency": 0.2, "quality": 0.2, "sample_size": 0.2}

        for r in self.results:
            # 归一化样本数分数（log scale）
            sample_score = min(r.sample_count / 1000, 1.0) if r.sample_count > 0 else 0

            # 计算总分
            r.total_score = (
                weights["relevance"] * r.relevance_score +
                weights["recency"] * r.recency_score +
                weights["quality"] * r.quality_score +
                weights["sample_size"] * sample_score
            )

        # 排序
        self.results.sort(key=lambda x: x.total_score, reverse=True)

        # 截取 top N
        if top_n and top_n > 0:
            self.results = self.results[:top_n]

        return self.results

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（JSON 序列化）"""
        self.query_hash = self.compute_hash()
        d: dict[str, Any] = {
            "query": self.query,
            "total_found": self.total_found,
            "results": [r.to_dict() for r in self.results],
            "stats": self.stats or self.compute_stats(),
            "expanded_queries": self.expanded_queries,
            "query_hash": self.query_hash,
            "searched_at": self.searched_at,
        }
        # 仅当 LLM 功能被使用时才包含相关字段
        if self.llm_summary or self.llm_model or self.llm_scored_count:
            d["llm_summary"] = self.llm_summary
            d["llm_model"] = self.llm_model
            d["llm_scored_count"] = self.llm_scored_count
        if self.llm_query_intent:
            d["llm_query_intent"] = self.llm_query_intent
        return d

    def to_summary(self, top_n: int = 10) -> dict[str, Any]:
        """生成结果摘要（用于 Agent 决策）"""
        top_results = [r.to_id_payload() for r in self.results[:top_n]]
        return {
            "top_datasets": top_results,
            "stats": self.stats or self.compute_stats(),
            "query_hash": self.query_hash,
        }


@dataclass
class QueryCacheSchema:
    """查询缓存 Schema"""
    query_hash: str
    query: str
    total_found: int
    result_hashes: list[str] = field(default_factory=list)
    cached_at: str = field(default_factory=_now_iso)
    expires_at: str = ""

    def is_expired(self, ttl_hours: int = 24) -> bool:
        """检查缓存是否过期"""
        if not self.expires_at:
            return True
        try:
            datetime.fromisoformat(self.cached_at)
            expiry = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) > expiry
        except (ValueError, TypeError):
            return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_hash": self.query_hash,
            "query": self.query,
            "total_found": self.total_found,
            "result_hashes": self.result_hashes,
            "cached_at": self.cached_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryCacheSchema:
        return cls(
            query_hash=data.get("query_hash", ""),
            query=data.get("query", ""),
            total_found=data.get("total_found", 0),
            result_hashes=data.get("result_hashes", []),
            cached_at=data.get("cached_at", _now_iso()),
            expires_at=data.get("expires_at", ""),
        )
