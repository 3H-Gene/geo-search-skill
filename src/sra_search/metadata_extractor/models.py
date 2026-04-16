"""数据模型定义

包含所有核心数据结构：
- DatasetRecord: 数据集主表
- TopicRecord: 主题表
- TopicDatasetRelation: 主题-数据集关联表
- SearchHistoryRecord: 搜索历史
- ReviewLogRecord: 审核日志
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---- Enums ----

class AvailabilityStatus(str, Enum):
    """数据可用性状态"""
    UNVERIFIED = "unverified"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    RESTRICTED = "restricted"


class AccessType(str, Enum):
    """访问权限类型"""
    PUBLIC = "public"
    CONTROLLED = "controlled"
    UNKNOWN = "unknown"


class ReviewStatus(str, Enum):
    """审核状态"""
    PENDING = "pending"
    APPROVED = "approved"
    IRRELEVANT = "irrelevant"
    DELETED = "deleted"


class OmicsGranularity(str, Enum):
    """组学数据粒度"""
    SINGLE_CELL = "single_cell"
    BULK = "bulk"
    SPATIAL = "spatial"
    UNKNOWN = "unknown"


class MatchSource(str, Enum):
    """命中数据源"""
    PUBMED = "pubmed"
    GEO = "geo"
    SRA = "sra"
    BIOPROJECT = "bioproject"


class ReviewAction(str, Enum):
    """审核操作类型"""
    MARK_IRRELEVANT = "mark_irrelevant"
    APPROVE = "approve"
    DELETE = "delete"
    UNDO = "undo"


# ---- Helper ----

def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串"""
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(obj: Any) -> str:
    """安全的 JSON 序列化（处理 None 和空列表）"""
    if obj is None:
        return "[]"
    return json.dumps(obj, ensure_ascii=False)


def _json_loads(s: str) -> Any:
    """安全的 JSON 反序列化"""
    if not s:
        return []
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []


# ---- Data Models ----

@dataclass
class DatasetRecord:
    """数据集主表（datasets）—— 全局唯一，以 GSE 为主键

    设计说明：
    - 可用性字段（availability_*、access_type）在全局层，因为数据集可否下载与主题无关
    - 版本控制字段（version、change_log）用于增量更新时追踪变更
    """
    gse_id: str  # GSE 编号（主键），has_gse=false 时使用 SRP:{SRP编号}
    title: str = ""
    pubmed_ids: list[str] = field(default_factory=list)
    sra_ids: list[str] = field(default_factory=list)  # SRP 编号列表
    bioproject_ids: list[str] = field(default_factory=list)
    organism: str = ""
    disease: str = ""
    organ: str = ""
    omics_type: str = ""  # JSON 数组存储，含细分类型
    omics_granularity: str = OmicsGranularity.UNKNOWN.value
    sample_count: int = 0
    platform: str = ""
    publication_date: str = ""
    journal: str = ""
    abstract: str = ""             # 摘要（来自 GEO summary 字段）
    overall_design: str = ""       # 实验设计详细描述（来自 GEO overall_design 字段）
    keywords: list[str] = field(default_factory=list)
    # --- 数据文件信息 ---
    supplementary_files: list[dict] = field(default_factory=list)  # [{name, type, size}]
    series_matrix_available: bool = False
    ftplink: str = ""
    # --- 系统字段 ---
    first_seen_at: str = field(default_factory=_now_iso)
    last_updated: str = field(default_factory=_now_iso)
    version: int = 1
    change_log: list[dict] = field(default_factory=list)  # JSON 数组
    # --- 可用性字段 ---
    availability_status: str = AvailabilityStatus.UNVERIFIED.value
    availability_note: str = ""
    availability_checked_at: str = ""
    access_type: str = AccessType.UNKNOWN.value
    # --- 标记字段 ---
    has_gse: bool = True
    metadata_hash: str = ""
    # --- LLM 缓存字段（避免重复分析同一数据集） ---
    llm_summary: str = ""
    llm_sample_grouping: str = ""
    llm_cell_count: str = ""
    llm_relevance_reason: str = ""
    llm_analyzed_at: str = ""
    llm_model: str = ""
    # --- GSM 样本名称（搜索阶段获取，用于样本分组推断） ---
    gsm_sample_names: list[str] = field(default_factory=list)

    def compute_metadata_hash(self) -> str:
        """计算元数据摘要哈希（用于检测数据集修订/更新）

        排除运行时字段（first_seen_at、last_updated、version、change_log、
        availability_*、access_type、metadata_hash、llm_*）。
        """
        fields_to_hash = {
            "title": self.title,
            "pubmed_ids": sorted(self.pubmed_ids),
            "sra_ids": sorted(self.sra_ids),
            "bioproject_ids": sorted(self.bioproject_ids),
            "organism": self.organism,
            "disease": self.disease,
            "organ": self.organ,
            "omics_type": self.omics_type,
            "sample_count": self.sample_count,
            "platform": self.platform,
            "journal": self.journal,
            "abstract": self.abstract,
            "overall_design": self.overall_design,
            "supplementary_files": [f.get("name", "") for f in self.supplementary_files],
            "series_matrix_available": self.series_matrix_available,
            "keywords": sorted(self.keywords),
        }
        raw = json.dumps(fields_to_hash, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def update_hash(self) -> str:
        """计算并更新 metadata_hash，返回新 hash"""
        self.metadata_hash = self.compute_metadata_hash()
        return self.metadata_hash

    def to_db_row(self) -> dict[str, Any]:
        """转换为数据库行字典（JSON 字段序列化为字符串）"""
        return {
            "gse_id": self.gse_id,
            "title": self.title,
            "pubmed_ids": _json_dumps(self.pubmed_ids),
            "sra_ids": _json_dumps(self.sra_ids),
            "bioproject_ids": _json_dumps(self.bioproject_ids),
            "organism": self.organism,
            "disease": self.disease,
            "organ": self.organ,
            "omics_type": self.omics_type,
            "omics_granularity": self.omics_granularity,
            "sample_count": self.sample_count,
            "platform": self.platform,
            "publication_date": self.publication_date,
            "journal": self.journal,
            "abstract": self.abstract,
            "overall_design": self.overall_design,
            "keywords": _json_dumps(self.keywords),
            "supplementary_files": _json_dumps(self.supplementary_files),
            "series_matrix_available": 1 if self.series_matrix_available else 0,
            "ftplink": self.ftplink,
            "first_seen_at": self.first_seen_at,
            "last_updated": self.last_updated,
            "version": self.version,
            "change_log": _json_dumps(self.change_log),
            "availability_status": self.availability_status,
            "availability_note": self.availability_note,
            "availability_checked_at": self.availability_checked_at,
            "access_type": self.access_type,
            "has_gse": self.has_gse,
            "metadata_hash": self.metadata_hash,
            "llm_summary": self.llm_summary,
            "llm_sample_grouping": self.llm_sample_grouping,
            "llm_cell_count": self.llm_cell_count,
            "llm_relevance_reason": self.llm_relevance_reason,
            "llm_analyzed_at": self.llm_analyzed_at,
            "llm_model": self.llm_model,
            "gsm_sample_names": _json_dumps(self.gsm_sample_names),
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> DatasetRecord:
        """从数据库行字典创建 DatasetRecord"""
        return cls(
            gse_id=row["gse_id"],
            title=row.get("title", ""),
            pubmed_ids=_json_loads(row.get("pubmed_ids", "[]")),
            sra_ids=_json_loads(row.get("sra_ids", "[]")),
            bioproject_ids=_json_loads(row.get("bioproject_ids", "[]")),
            organism=row.get("organism", ""),
            disease=row.get("disease", ""),
            organ=row.get("organ", ""),
            omics_type=row.get("omics_type", ""),
            omics_granularity=row.get("omics_granularity", OmicsGranularity.UNKNOWN.value),
            sample_count=row.get("sample_count", 0),
            platform=row.get("platform", ""),
            publication_date=row.get("publication_date", ""),
            journal=row.get("journal", ""),
            abstract=row.get("abstract", ""),
            overall_design=row.get("overall_design", ""),
            keywords=_json_loads(row.get("keywords", "[]")),
            supplementary_files=_json_loads(row.get("supplementary_files", "[]")),
            series_matrix_available=bool(row.get("series_matrix_available", 0)),
            ftplink=row.get("ftplink", ""),
            first_seen_at=row.get("first_seen_at", _now_iso()),
            last_updated=row.get("last_updated", _now_iso()),
            version=row.get("version", 1),
            change_log=_json_loads(row.get("change_log", "[]")),
            availability_status=row.get("availability_status", AvailabilityStatus.UNVERIFIED.value),
            availability_note=row.get("availability_note", ""),
            availability_checked_at=row.get("availability_checked_at", ""),
            access_type=row.get("access_type", AccessType.UNKNOWN.value),
            has_gse=bool(row.get("has_gse", True)),
            metadata_hash=row.get("metadata_hash", ""),
            llm_summary=row.get("llm_summary", ""),
            llm_sample_grouping=row.get("llm_sample_grouping", ""),
            llm_cell_count=row.get("llm_cell_count", ""),
            llm_relevance_reason=row.get("llm_relevance_reason", ""),
            llm_analyzed_at=row.get("llm_analyzed_at", ""),
            llm_model=row.get("llm_model", ""),
            gsm_sample_names=_json_loads(row.get("gsm_sample_names", "[]")),
        )


@dataclass
class TopicRecord:
    """主题表（topics）"""
    topic_id: str  # 主题 ID（主键，UUID 或自增）
    name: str  # 主题名称
    description: str = ""
    keywords_used: list[str] = field(default_factory=list)  # JSON
    created_at: str = field(default_factory=_now_iso)
    last_searched_at: str = field(default_factory=_now_iso)

    def to_db_row(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "name": self.name,
            "description": self.description,
            "keywords_used": _json_dumps(self.keywords_used),
            "created_at": self.created_at,
            "last_searched_at": self.last_searched_at,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> TopicRecord:
        return cls(
            topic_id=row["topic_id"],
            name=row["name"],
            description=row.get("description", ""),
            keywords_used=_json_loads(row.get("keywords_used", "[]")),
            created_at=row.get("created_at", _now_iso()),
            last_searched_at=row.get("last_searched_at", _now_iso()),
        )


@dataclass
class TopicDatasetRelation:
    """主题-数据集关联表（topic_datasets）—— 审核状态在此层

    设计说明：
    - 同一数据集在不同主题可独立审核
    - 同一数据集在同一主题可被多个关键词命中（不去重，保留追溯性）
    """
    id: str  # 关联 ID（主键）
    topic_id: str  # 外键 → topics
    gse_id: str  # 外键 → datasets
    match_keyword: str = ""
    match_source: str = MatchSource.GEO.value
    match_score: float = 0.0
    # --- 审核字段 ---
    review_status: str = ReviewStatus.PENDING.value
    review_note: str = ""
    reviewed_at: str = ""
    added_at: str = field(default_factory=_now_iso)

    def to_db_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic_id": self.topic_id,
            "gse_id": self.gse_id,
            "match_keyword": self.match_keyword,
            "match_source": self.match_source,
            "match_score": self.match_score,
            "review_status": self.review_status,
            "review_note": self.review_note,
            "reviewed_at": self.reviewed_at,
            "added_at": self.added_at,
        }

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> TopicDatasetRelation:
        return cls(
            id=row["id"],
            topic_id=row["topic_id"],
            gse_id=row["gse_id"],
            match_keyword=row.get("match_keyword", ""),
            match_source=row.get("match_source", MatchSource.GEO.value),
            match_score=row.get("match_score", 0.0),
            review_status=row.get("review_status", ReviewStatus.PENDING.value),
            review_note=row.get("review_note", ""),
            reviewed_at=row.get("reviewed_at", ""),
            added_at=row.get("added_at", _now_iso()),
        )


@dataclass
class SearchHistoryRecord:
    """搜索历史记录"""
    id: str
    topic_id: str | None = None
    search_time: str = field(default_factory=_now_iso)
    keyword_used: str = ""
    results_count: int = 0

    def to_db_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic_id": self.topic_id,
            "search_time": self.search_time,
            "keyword_used": self.keyword_used,
            "results_count": self.results_count,
        }


@dataclass
class ReviewLogRecord:
    """审核操作日志（用于撤销和审计）"""
    id: str
    topic_id: str
    gse_id: str
    action: str  # ReviewAction
    old_status: str = ""
    new_status: str = ""
    note: str = ""
    acted_at: str = field(default_factory=_now_iso)

    def to_db_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic_id": self.topic_id,
            "gse_id": self.gse_id,
            "action": self.action,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "note": self.note,
            "acted_at": self.acted_at,
        }
