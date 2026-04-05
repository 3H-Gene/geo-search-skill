"""审核筛选逻辑

按审核状态、主题、可用性等条件筛选数据集。
"""

from __future__ import annotations

from sra_search.data_store.database import Database


class ReviewFilters:
    """数据集审核筛选器"""

    def __init__(self, db: Database):
        self.db = db

    def get_pending(self, topic_id: str | None = None, limit: int = 50) -> list[dict]:
        """获取待审核的数据集

        Args:
            topic_id: 限定主题（可选）
            limit: 返回数量上限
        """
        if topic_id:
            return self.db.get_topic_datasets(topic_id, review_status="pending")[:limit]

        # 获取所有主题下的 pending 数据集
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT td.*, d.title, d.organism, d.sample_count, d.omics_type,
                   d.omics_granularity, d.platform, d.availability_status, d.access_type
            FROM topic_datasets td
            JOIN datasets d ON td.gse_id = d.gse_id
            WHERE td.review_status = 'pending'
            ORDER BY td.added_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_by_status(
        self,
        status: str,
        topic_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """按审核状态筛选

        Args:
            status: 审核状态 (pending / approved / irrelevant / deleted)
            topic_id: 限定主题
            limit: 返回数量上限
        """
        if topic_id:
            return self.db.get_topic_datasets(topic_id, review_status=status)[:limit]

        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT td.*, d.title, d.organism, d.sample_count, d.omics_type,
                   d.omics_granularity, d.platform, d.availability_status, d.access_type
            FROM topic_datasets td
            JOIN datasets d ON td.gse_id = d.gse_id
            WHERE td.review_status = ?
            ORDER BY td.reviewed_at DESC NULLS LAST
            LIMIT ?
            """,
            (status, limit),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_review_summary(self, topic_id: str | None = None) -> dict:
        """获取审核状态统计摘要

        Args:
            topic_id: 限定主题

        Returns:
            统计字典: {total, pending, approved, irrelevant, deleted}
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()

        if topic_id:
            cursor.execute(
                """
                SELECT review_status, COUNT(*) as cnt
                FROM topic_datasets
                WHERE topic_id = ?
                GROUP BY review_status
                """,
                (topic_id,),
            )
        else:
            cursor.execute(
                """
                SELECT review_status, COUNT(*) as cnt
                FROM topic_datasets
                GROUP BY review_status
                """
            )

        summary = {"total": 0, "pending": 0, "approved": 0, "irrelevant": 0, "deleted": 0}
        for row in cursor.fetchall():
            status = row["review_status"] or "pending"
            count = row["cnt"]
            if status in summary:
                summary[status] = count
            summary["total"] += count

        return summary

    def get_unreviewed_count(self, topic_id: str | None = None) -> int:
        """获取待审核数量"""
        summary = self.get_review_summary(topic_id)
        return int(summary["pending"])

    def get_review_log(
        self,
        topic_id: str | None = None,
        gse_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """获取审核操作日志

        Args:
            topic_id: 限定主题
            gse_id: 限定数据集
            limit: 返回数量上限
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()

        conditions = []
        params: list = []

        if topic_id:
            conditions.append("topic_id = ?")
            params.append(topic_id)
        if gse_id:
            conditions.append("gse_id = ?")
            params.append(gse_id)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        cursor.execute(
            f"""
            SELECT * FROM review_log
            {where}
            ORDER BY acted_at DESC
            LIMIT ?
            """,
            params + [limit],
        )
        return [dict(r) for r in cursor.fetchall()]
