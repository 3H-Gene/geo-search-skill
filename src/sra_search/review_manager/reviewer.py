"""数据集审核管理器

提供单条/批量审核操作、状态变更日志和撤销功能。
审核状态在 topic_datasets 关联表层面（同一数据集在不同主题可独立审核）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loguru import logger

from sra_search.data_store.database import Database
from sra_search.metadata_extractor.models import ReviewLogRecord, _now_iso

# 合法审核状态
VALID_STATUSES = {"pending", "approved", "irrelevant", "deleted"}

# 状态转换规则
VALID_TRANSITIONS = {
    "pending": {"approved", "irrelevant", "deleted"},
    "approved": {"irrelevant", "deleted"},
    "irrelevant": {"approved", "deleted"},
    "deleted": {"approved", "irrelevant", "pending"},
}


@dataclass
class ReviewResult:
    """审核操作结果"""
    topic_id: str
    gse_id: str
    action: str
    old_status: str
    new_status: str
    note: str
    success: bool
    error: str = ""


class Reviewer:
    """数据集审核管理器"""

    def __init__(self, db: Database):
        self.db = db

    async def mark(
        self,
        gse_id: str,
        topic_id: str,
        status: str,
        note: str = "",
    ) -> ReviewResult:
        """标记单条数据集的审核状态

        Args:
            gse_id: GSE 编号
            topic_id: 主题 ID
            status: 目标状态 (approved / irrelevant / deleted)
            note: 审核备注
        """
        return await self._do_review(gse_id, topic_id, status, note, "mark")

    async def approve(
        self,
        gse_id: str,
        topic_id: str,
        note: str = "",
    ) -> ReviewResult:
        """确认数据集相关"""
        return await self._do_review(gse_id, topic_id, "approved", note, "approve")

    async def delete(
        self,
        gse_id: str,
        topic_id: str,
        note: str = "",
    ) -> ReviewResult:
        """从主题中删除数据集"""
        return await self._do_review(gse_id, topic_id, "deleted", note, "delete")

    async def batch_mark(
        self,
        topic_id: str,
        status: str,
        gse_ids: list[str] | None = None,
        current_status: str | None = None,
        note: str = "",
    ) -> list[ReviewResult]:
        """批量标记审核状态

        Args:
            topic_id: 主题 ID
            status: 目标状态
            gse_ids: 指定 GSE 编号列表（可选）
            current_status: 当前状态过滤（可选，如 pending）
            note: 审核备注
        """
        # 获取目标数据集
        if gse_ids:
            targets = gse_ids
        elif current_status:
            datasets = self.db.get_topic_datasets(topic_id, review_status=current_status)
            targets = [d["gse_id"] for d in datasets]
        else:
            datasets = self.db.get_topic_datasets(topic_id)
            targets = [d["gse_id"] for d in datasets]

        results = []
        for gse_id in targets:
            result = await self._do_review(gse_id, topic_id, status, note, "batch_mark")
            results.append(result)

        logger.info(
            f"Batch review: {topic_id} -> {status}, "
            f"success={sum(1 for r in results if r.success)}, "
            f"failed={sum(1 for r in results if not r.success)}"
        )
        return results

    async def undo(
        self,
        gse_id: str,
        topic_id: str,
    ) -> ReviewResult:
        """撤销最近一次审核操作

        基于审核日志回退到上一个状态。
        """
        # 查找最近一条日志
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM review_log WHERE topic_id = ? AND gse_id = ? ORDER BY acted_at DESC LIMIT 1",
            (topic_id, gse_id),
        )
        row = cursor.fetchone()
        if row is None:
            return ReviewResult(
                topic_id=topic_id,
                gse_id=gse_id,
                action="undo",
                old_status="",
                new_status="",
                note="",
                success=False,
                error="No review log found for undo",
            )

        log_entry = dict(row)
        old_status = log_entry["old_status"]
        new_status = log_entry["new_status"]

        # 撤销：将当前 new_status 恢复为 old_status
        target_status = old_status if old_status else "pending"

        result = await self._do_review(
            gse_id, topic_id, target_status,
            note=f"[UNDO] Reverted from {new_status}",
            log_action="undo",
            skip_transition_check=True,  # 撤销操作不需要检查转换规则
        )

        # 记录撤销日志
        if result.success:
            await self.db.insert_review_log(ReviewLogRecord(
                id=str(uuid.uuid4()),
                topic_id=topic_id,
                gse_id=gse_id,
                action="undo",
                old_status=new_status,
                new_status=target_status,
                note=f"Reverted {new_status} -> {target_status}",
                acted_at=_now_iso(),
            ))

        return result

    async def _do_review(
        self,
        gse_id: str,
        topic_id: str,
        status: str,
        note: str,
        log_action: str,
        skip_transition_check: bool = False,
    ) -> ReviewResult:
        """执行审核状态变更"""
        # 验证状态
        if status not in VALID_STATUSES:
            return ReviewResult(
                topic_id=topic_id, gse_id=gse_id, action=log_action,
                old_status="", new_status=status, note=note,
                success=False, error=f"Invalid status: {status}",
            )

        # 获取当前状态
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT review_status FROM topic_datasets WHERE topic_id = ? AND gse_id = ?",
            (topic_id, gse_id),
        )
        row = cursor.fetchone()
        if row is None:
            return ReviewResult(
                topic_id=topic_id, gse_id=gse_id, action=log_action,
                old_status="", new_status=status, note=note,
                success=False, error="No topic-dataset relation found",
            )

        old_status = row["review_status"] or "pending"

        # 检查状态转换是否合法
        if not skip_transition_check:
            allowed = VALID_TRANSITIONS.get(old_status, set())
            if status not in allowed and old_status != status:
                logger.warning(
                    f"Invalid transition: {old_status} -> {status} for {gse_id} in {topic_id}"
                )
                # 允许但记录警告

        # 执行更新
        try:
            await self.db.update_review(
                topic_id=topic_id,
                gse_id=gse_id,
                status=status,
                note=note,
                log_action=log_action,
                log_old_status=old_status,
            )
            logger.debug(f"Review: {gse_id} in {topic_id}: {old_status} -> {status}")
            return ReviewResult(
                topic_id=topic_id, gse_id=gse_id, action=log_action,
                old_status=old_status, new_status=status, note=note,
                success=True,
            )
        except Exception as e:
            logger.error(f"Review failed for {gse_id}: {e}")
            return ReviewResult(
                topic_id=topic_id, gse_id=gse_id, action=log_action,
                old_status=old_status, new_status=status, note=note,
                success=False, error=str(e),
            )
