"""搜索报告服务

提供搜索报告的存储、查询、更新功能。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串"""
    return datetime.now(timezone.utc).isoformat()


def _compute_query_hash(query: str) -> str:
    """计算查询词的哈希值"""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


@dataclass
class SearchReportItem:
    """搜索报告中的单条结果"""
    rank: int = 0
    gse_id: str = ""
    relevance_score: float = 0.0
    one_sentence_summary: str = ""
    sample_grouping: str = ""
    cell_count: str = ""
    relevance_reason: str = ""
    data_type: str = ""
    sample_count: int = 0
    organism: str = ""
    tissue: str = ""
    platform: str = ""
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "gse_id": self.gse_id,
            "relevance_score": self.relevance_score,
            "one_sentence_summary": self.one_sentence_summary,
            "sample_grouping": self.sample_grouping,
            "cell_count": self.cell_count,
            "relevance_reason": self.relevance_reason,
            "data_type": self.data_type,
            "sample_count": self.sample_count,
            "organism": self.organism,
            "tissue": self.tissue,
            "platform": self.platform,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchReportItem:
        return cls(
            rank=data.get("rank", 0),
            gse_id=data.get("gse_id", ""),
            relevance_score=data.get("relevance_score", 0.0),
            one_sentence_summary=data.get("one_sentence_summary", ""),
            sample_grouping=data.get("sample_grouping", ""),
            cell_count=data.get("cell_count", ""),
            relevance_reason=data.get("relevance_reason", ""),
            data_type=data.get("data_type", ""),
            sample_count=data.get("sample_count", 0),
            organism=data.get("organism", ""),
            tissue=data.get("tissue", ""),
            platform=data.get("platform", ""),
            title=data.get("title", ""),
        )


@dataclass
class SearchReport:
    """搜索报告"""
    id: str = ""
    query: str = ""
    query_hash: str = ""
    mode: str = "v1"                    # v1 / v1+llm / llm-only
    sources: list[str] = field(default_factory=lambda: ["geo", "sra", "pubmed"])
    filters: dict[str, Any] = field(default_factory=dict)
    total_found: int = 0
    returned_count: int = 0
    llm_model: str = ""
    searched_at: str = ""
    expires_at: str = ""
    items: list[SearchReportItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "query_hash": self.query_hash,
            "mode": self.mode,
            "sources": self.sources,
            "filters": self.filters,
            "total_found": self.total_found,
            "returned_count": self.returned_count,
            "llm_model": self.llm_model,
            "searched_at": self.searched_at,
            "expires_at": self.expires_at,
            "items": [item.to_dict() for item in self.items],
        }


class SearchReportService:
    """搜索报告服务

    提供搜索报告的 CRUD 操作。
    """

    def __init__(self, db):
        """初始化服务

        Args:
            db: Database 实例
        """
        self.db = db

    def _get_connection(self):
        """获取数据库连接"""
        return self.db.get_connection()

    def save_report(
        self,
        query: str,
        mode: str,
        sources: list[str],
        total_found: int,
        returned_count: int,
        llm_model: str,
        items: list[SearchReportItem],
        filters: dict[str, Any] | None = None,
    ) -> str:
        """保存搜索报告

        Args:
            query: 搜索关键词
            mode: 模式 (v1 / v1+llm / llm-only)
            sources: 数据源列表
            total_found: 原始命中数
            returned_count: 返回数量
            llm_model: 使用的 LLM 模型
            items: 结果列表
            filters: 搜索过滤器

        Returns:
            报告 ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        report_id = str(uuid.uuid4())
        query_hash = _compute_query_hash(query)
        now = _now_iso()
        sources_json = json.dumps(sources, ensure_ascii=False)
        filters_json = json.dumps(filters or {}, ensure_ascii=False)

        try:
            # 先查询是否已存在相同报告
            cursor.execute(
                "SELECT id FROM search_reports WHERE query_hash = ? AND mode = ? AND sources = ?",
                (query_hash, mode, sources_json)
            )
            existing = cursor.fetchone()

            if existing:
                # 已存在则更新
                report_id = existing[0]
                cursor.execute("""
                    UPDATE search_reports SET
                        query = ?, filters = ?, total_found = ?, returned_count = ?,
                        llm_model = ?, searched_at = ?
                    WHERE id = ?
                """, (query, filters_json, total_found, returned_count, llm_model, now, report_id))
                # 删除旧报告项
                cursor.execute("DELETE FROM search_report_items WHERE report_id = ?", (report_id,))
            else:
                # 不存在则插入新报告
                cursor.execute("""
                    INSERT INTO search_reports (
                        id, query, query_hash, mode, sources, filters,
                        total_found, returned_count, llm_model, searched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    report_id, query, query_hash, mode, sources_json, filters_json,
                    total_found, returned_count, llm_model, now
                ))

            # 插入报告项
            for item in items:
                item_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO search_report_items (
                        id, report_id, rank, gse_id, relevance_score,
                        one_sentence_summary, sample_grouping, cell_count, relevance_reason,
                        data_type, sample_count, organism, tissue, platform, title
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item_id, report_id, item.rank, item.gse_id, item.relevance_score,
                    item.one_sentence_summary, item.sample_grouping, item.cell_count, item.relevance_reason,
                    item.data_type, item.sample_count, item.organism, item.tissue, item.platform, item.title
                ))

            conn.commit()
            logger.info(f"[SearchReportService] 保存报告: {report_id}, query={query!r}, items={len(items)}")
            return report_id

        except Exception as e:
            conn.rollback()
            logger.error(f"[SearchReportService] 保存失败: {e}")
            raise

    def get_report_by_query(
        self,
        query: str,
        mode: str = "v1+llm",
        sources: list[str] | None = None,
    ) -> SearchReport | None:
        """根据查询词获取报告

        Args:
            query: 搜索关键词
            mode: 模式
            sources: 数据源列表

        Returns:
            SearchReport 或 None
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query_hash = _compute_query_hash(query)
        sources_json = json.dumps(sources or ["geo", "sra", "pubmed"], ensure_ascii=False)

        cursor.execute("""
            SELECT id, query, query_hash, mode, sources, filters,
                   total_found, returned_count, llm_model, searched_at, expires_at
            FROM search_reports
            WHERE query_hash = ? AND mode = ? AND sources = ?
            ORDER BY searched_at DESC
            LIMIT 1
        """, (query_hash, mode, sources_json))

        row = cursor.fetchone()
        if not row:
            return None

        report = SearchReport(
            id=row[0],
            query=row[1],
            query_hash=row[2],
            mode=row[3],
            sources=json.loads(row[4]),
            filters=json.loads(row[5]) if row[5] else {},
            total_found=row[6],
            returned_count=row[7],
            llm_model=row[8] or "",
            searched_at=row[9] or "",
            expires_at=row[10] or "",
        )

        # 获取报告项
        cursor.execute("""
            SELECT rank, gse_id, relevance_score,
                   one_sentence_summary, sample_grouping, cell_count, relevance_reason,
                   data_type, sample_count, organism, tissue, platform, title
            FROM search_report_items
            WHERE report_id = ?
            ORDER BY rank ASC
        """, (report.id,))

        for item_row in cursor.fetchall():
            report.items.append(SearchReportItem(
                rank=item_row[0],
                gse_id=item_row[1],
                relevance_score=item_row[2],
                one_sentence_summary=item_row[3] or "",
                sample_grouping=item_row[4] or "",
                cell_count=item_row[5] or "",
                relevance_reason=item_row[6] or "",
                data_type=item_row[7] or "",
                sample_count=item_row[8] or 0,
                organism=item_row[9] or "",
                tissue=item_row[10] or "",
                platform=item_row[11] or "",
                title=item_row[12] or "",
            ))

        return report

    def list_reports(self, limit: int = 20, offset: int = 0) -> list[SearchReport]:
        """列出最近的报告

        Args:
            limit: 返回数量
            offset: 偏移量

        Returns:
            SearchReport 列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, query, query_hash, mode, sources, filters,
                   total_found, returned_count, llm_model, searched_at, expires_at
            FROM search_reports
            ORDER BY searched_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))

        reports = []
        for row in cursor.fetchall():
            report = SearchReport(
                id=row[0],
                query=row[1],
                query_hash=row[2],
                mode=row[3],
                sources=json.loads(row[4]),
                filters=json.loads(row[5]) if row[5] else {},
                total_found=row[6],
                returned_count=row[7],
                llm_model=row[8] or "",
                searched_at=row[9] or "",
                expires_at=row[10] or "",
            )
            reports.append(report)

        return reports

    def delete_report(self, report_id: str) -> bool:
        """删除报告

        Args:
            report_id: 报告 ID

        Returns:
            是否成功
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 先删除关联的 items
            cursor.execute("DELETE FROM search_report_items WHERE report_id = ?", (report_id,))
            # 再删除报告
            cursor.execute("DELETE FROM search_reports WHERE id = ?", (report_id,))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"[SearchReportService] 删除失败: {e}")
            return False

    def delete_report_by_query(self, query: str, mode: str | None = None) -> int:
        """根据查询词删除报告

        Args:
            query: 搜索关键词
            mode: 模式（可选）

        Returns:
            删除的报告数量
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query_hash = _compute_query_hash(query)

        try:
            # 获取匹配的报告 ID
            if mode:
                cursor.execute(
                    "SELECT id FROM search_reports WHERE query_hash = ? AND mode = ?",
                    (query_hash, mode)
                )
            else:
                cursor.execute(
                    "SELECT id FROM search_reports WHERE query_hash = ?",
                    (query_hash,)
                )

            report_ids = [row[0] for row in cursor.fetchall()]

            for report_id in report_ids:
                cursor.execute("DELETE FROM search_report_items WHERE report_id = ?", (report_id,))
                cursor.execute("DELETE FROM search_reports WHERE id = ?", (report_id,))

            conn.commit()
            return len(report_ids)
        except Exception as e:
            conn.rollback()
            logger.error(f"[SearchReportService] 删除失败: {e}")
            return 0
