"""SQLite 数据库操作封装

特性：
- WAL 模式（Write-Ahead Logging）提升并发性能
- 异步写入队列（asyncio.Queue + 后台写入协程）避免 database is locked
- 批量写入（batch insert/upsert）减少事务开销
- CRUD 操作封装
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from sra_search.config import get_settings
from sra_search.data_store.schema import ALL_TABLES, CREATE_INDEXES
from sra_search.metadata_extractor.models import (
    DatasetRecord,
    TopicDatasetRelation,
    TopicRecord,
    SearchHistoryRecord,
    ReviewLogRecord,
    _json_dumps,
    _json_loads,
)


class WriteQueue:
    """异步写入队列

    所有检索结果先入队列，由单一后台协程顺序写入数据库。
    支持批量攒批：每 batch_size 条或每 flush_interval 秒刷新一次。
    """

    def __init__(
        self,
        db: "Database",
        batch_size: int = 100,
        flush_interval: float = 2.0,
    ):
        self.db = db
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """启动后台写入协程"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._write_loop())
        logger.info(f"Write queue started (batch={self.batch_size}, interval={self.flush_interval}s)")

    async def stop(self) -> None:
        """停止后台写入协程，刷新剩余数据"""
        self._running = False
        if self._task and not self._task.done():
            # 等待队列清空
            await self._queue.join()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Write queue stopped")

    async def put(self, operation: str, data: dict) -> None:
        """提交一个写入操作到队列

        Args:
            operation: 操作类型 (upsert_dataset / insert_topic_dataset / etc.)
            data: 操作数据
        """
        await self._queue.put((operation, data))

    async def put_many(self, operations: List[tuple]) -> None:
        """批量提交写入操作"""
        for op in operations:
            await self._queue.put(op)

    async def _write_loop(self) -> None:
        """后台写入循环"""
        batch: List[tuple] = []
        last_flush = asyncio.get_event_loop().time()

        try:
            while self._running or not self._queue.empty():
                try:
                    # 等待新数据，带超时以便定期刷新
                    timeout = max(0.1, self.flush_interval - (asyncio.get_event_loop().time() - last_flush))
                    if batch:
                        timeout = min(timeout, 0.1)  # 有待写入数据时快速轮询

                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                    batch.append(item)
                    self._queue.task_done()
                except asyncio.TimeoutError:
                    pass

                # 检查是否需要刷新
                now = asyncio.get_event_loop().time()
                should_flush = (
                    len(batch) >= self.batch_size
                    or (batch and (now - last_flush) >= self.flush_interval)
                    or (not self._running and batch)  # 停止时刷新剩余
                )

                if should_flush and batch:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = now

        except asyncio.CancelledError:
            # 被取消时刷新剩余数据
            if batch:
                self._flush_batch(batch)
            raise

    def _flush_batch(self, batch: List[tuple]) -> None:
        """同步执行一批写入操作"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            flushed = 0
            errors = 0

            for operation, data in batch:
                try:
                    if operation == "upsert_dataset":
                        self._upsert_dataset(cursor, data)
                    elif operation == "insert_topic_dataset":
                        self._insert_topic_dataset(cursor, data)
                    elif operation == "insert_topic":
                        self._insert_topic(cursor, data)
                    elif operation == "insert_search_history":
                        self._insert_search_history(cursor, data)
                    elif operation == "insert_review_log":
                        self._insert_review_log(cursor, data)
                    elif operation == "update_review_status":
                        self._update_review_status(cursor, data)
                    else:
                        logger.warning(f"Unknown write operation: {operation}")
                        continue
                    flushed += 1
                except Exception as e:
                    errors += 1
                    logger.error(f"Write error ({operation}): {e}")
                    continue

            conn.commit()
            if flushed > 0:
                logger.debug(f"Flushed {flushed} writes ({errors} errors)")
        except Exception as e:
            logger.error(f"Batch flush failed: {e}")
            try:
                conn.rollback()
            except Exception:
                pass

    @staticmethod
    def _upsert_dataset(cursor: sqlite3.Cursor, data: dict) -> None:
        """插入或更新数据集"""
        cursor.execute("""
            INSERT INTO datasets (gse_id, title, pubmed_ids, sra_ids, bioproject_ids,
                organism, disease, organ, omics_type, omics_granularity, sample_count,
                platform, publication_date, journal, abstract, keywords,
                first_seen_at, last_updated, version, change_log,
                availability_status, availability_note, availability_checked_at,
                access_type, has_gse, metadata_hash)
            VALUES (:gse_id, :title, :pubmed_ids, :sra_ids, :bioproject_ids,
                :organism, :disease, :organ, :omics_type, :omics_granularity, :sample_count,
                :platform, :publication_date, :journal, :abstract, :keywords,
                :first_seen_at, :last_updated, :version, :change_log,
                :availability_status, :availability_note, :availability_checked_at,
                :access_type, :has_gse, :metadata_hash)
            ON CONFLICT(gse_id) DO UPDATE SET
                title = COALESCE(NULLIF(:title, ''), title),
                pubmed_ids = CASE WHEN :pubmed_ids != '[]' THEN :pubmed_ids ELSE pubmed_ids END,
                sra_ids = CASE WHEN :sra_ids != '[]' THEN :sra_ids ELSE sra_ids END,
                bioproject_ids = CASE WHEN :bioproject_ids != '[]' THEN :bioproject_ids ELSE bioproject_ids END,
                organism = COALESCE(NULLIF(:organism, ''), organism),
                disease = COALESCE(NULLIF(:disease, ''), disease),
                organ = COALESCE(NULLIF(:organ, ''), organ),
                omics_type = CASE WHEN :omics_type != '' THEN :omics_type ELSE omics_type END,
                omics_granularity = CASE WHEN :omics_granularity != 'unknown' THEN :omics_granularity ELSE omics_granularity END,
                sample_count = CASE WHEN :sample_count > 0 THEN :sample_count ELSE sample_count END,
                platform = COALESCE(NULLIF(:platform, ''), platform),
                publication_date = COALESCE(NULLIF(:publication_date, ''), publication_date),
                journal = COALESCE(NULLIF(:journal, ''), journal),
                abstract = CASE WHEN LENGTH(:abstract) > LENGTH(abstract) THEN :abstract ELSE abstract END,
                keywords = CASE WHEN :keywords != '[]' THEN :keywords ELSE keywords END,
                last_updated = :last_updated,
                version = :version,
                change_log = :change_log,
                availability_status = :availability_status,
                availability_note = :availability_note,
                availability_checked_at = :availability_checked_at,
                access_type = :access_type,
                has_gse = :has_gse,
                metadata_hash = :metadata_hash
        """, data)

    @staticmethod
    def _insert_topic_dataset(cursor: sqlite3.Cursor, data: dict) -> None:
        """插入主题-数据集关联"""
        cursor.execute("""
            INSERT OR IGNORE INTO topic_datasets
            (id, topic_id, gse_id, match_keyword, match_source, match_score,
             review_status, review_note, reviewed_at, added_at)
            VALUES (:id, :topic_id, :gse_id, :match_keyword, :match_source, :match_score,
             :review_status, :review_note, :reviewed_at, :added_at)
        """, data)

    @staticmethod
    def _insert_topic(cursor: sqlite3.Cursor, data: dict) -> None:
        """插入主题"""
        cursor.execute("""
            INSERT OR REPLACE INTO topics
            (topic_id, name, description, keywords_used, created_at, last_searched_at)
            VALUES (:topic_id, :name, :description, :keywords_used, :created_at, :last_searched_at)
        """, data)

    @staticmethod
    def _insert_search_history(cursor: sqlite3.Cursor, data: dict) -> None:
        """插入搜索历史"""
        cursor.execute("""
            INSERT INTO search_history
            (id, topic_id, search_time, keyword_used, results_count)
            VALUES (:id, :topic_id, :search_time, :keyword_used, :results_count)
        """, data)

    @staticmethod
    def _insert_review_log(cursor: sqlite3.Cursor, data: dict) -> None:
        """插入审核日志"""
        cursor.execute("""
            INSERT INTO review_log
            (id, topic_id, gse_id, action, old_status, new_status, note, acted_at)
            VALUES (:id, :topic_id, :gse_id, :action, :old_status, :new_status, :note, :acted_at)
        """, data)

    @staticmethod
    def _update_review_status(cursor: sqlite3.Cursor, data: dict) -> None:
        """更新审核状态"""
        cursor.execute("""
            UPDATE topic_datasets
            SET review_status = :review_status,
                review_note = :review_note,
                reviewed_at = :reviewed_at
            WHERE topic_id = :topic_id AND gse_id = :gse_id
        """, data)


class Database:
    """SQLite 数据库操作封装"""

    def __init__(self, db_path: Optional[str] = None):
        settings = get_settings()
        self.db_path = db_path or str(settings.db_path_resolved)
        self._conn: Optional[sqlite3.Connection] = None
        self.write_queue = WriteQueue(self, settings.db_write_batch_size, settings.db_write_flush_interval)

    def get_connection(self) -> sqlite3.Connection:
        """获取数据库连接（单例）"""
        if self._conn is None:
            self._connect()
        return self._conn

    def _connect(self) -> None:
        """建立数据库连接并初始化"""
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        settings = get_settings()
        self._conn = sqlite3.connect(str(db_file), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # WAL 模式 + busy_timeout
        if settings.db_wal_enabled:
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(f"PRAGMA busy_timeout={settings.db_busy_timeout};")

        logger.info(f"Database connected: {self.db_path}")

        # 初始化表结构
        self._init_tables()

    def _init_tables(self) -> None:
        """创建表和索引"""
        conn = self.get_connection()
        cursor = conn.cursor()
        for sql in ALL_TABLES:
            cursor.execute(sql)
        for sql in CREATE_INDEXES:
            cursor.execute(sql)
        conn.commit()
        logger.debug("Database tables initialized")

    async def start_write_queue(self) -> None:
        """启动异步写入队列"""
        await self.write_queue.start()

    async def stop_write_queue(self) -> None:
        """停止异步写入队列"""
        await self.write_queue.stop()

    # ---- High-level operations ----

    async def upsert_dataset(self, record: DatasetRecord) -> None:
        """插入或更新数据集（通过写入队列）"""
        await self.write_queue.put("upsert_dataset", record.to_db_row())

    async def upsert_datasets_batch(self, records: List[DatasetRecord]) -> None:
        """批量插入或更新数据集"""
        ops = [("upsert_dataset", r.to_db_row()) for r in records]
        await self.write_queue.put_many(ops)

    async def insert_topic_dataset(self, record: TopicDatasetRelation) -> None:
        """插入主题-数据集关联"""
        await self.write_queue.put("insert_topic_dataset", record.to_db_row())

    async def insert_topic(self, record: TopicRecord) -> None:
        """插入主题"""
        await self.write_queue.put("insert_topic", record.to_db_row())

    async def insert_search_history(self, record: SearchHistoryRecord) -> None:
        """插入搜索历史"""
        await self.write_queue.put("insert_search_history", record.to_db_row())

    async def insert_review_log(self, record: ReviewLogRecord) -> None:
        """插入审核日志"""
        await self.write_queue.put("insert_review_log", record.to_db_row())

    async def update_review(
        self,
        topic_id: str,
        gse_id: str,
        status: str,
        note: str = "",
        log_action: str = "",
        log_old_status: str = "",
    ) -> None:
        """更新审核状态并记录日志"""
        from sra_search.metadata_extractor.models import _now_iso
        now = _now_iso()

        await self.write_queue.put("update_review_status", {
            "topic_id": topic_id,
            "gse_id": gse_id,
            "review_status": status,
            "review_note": note,
            "reviewed_at": now,
        })

        if log_action:
            await self.write_queue.put("insert_review_log", {
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "gse_id": gse_id,
                "action": log_action,
                "old_status": log_old_status,
                "new_status": status,
                "note": note,
                "acted_at": now,
            })

    # ---- Query operations (synchronous, read-only) ----

    def get_dataset(self, gse_id: str) -> Optional[DatasetRecord]:
        """获取单个数据集"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM datasets WHERE gse_id = ?", (gse_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return DatasetRecord.from_db_row(dict(row))

    def list_datasets(
        self,
        topic_id: Optional[str] = None,
        review_status: Optional[str] = None,
        availability: Optional[str] = None,
        access_type: Optional[str] = None,
        granularity: Optional[str] = None,
        organism: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[DatasetRecord]:
        """列出数据集（支持多条件筛选）"""
        conn = self.get_connection()
        cursor = conn.cursor()

        query = "SELECT DISTINCT d.* FROM datasets d"
        params: list = []

        if topic_id:
            query += " JOIN topic_datasets td ON d.gse_id = td.gse_id"
            params_append = []
            if review_status:
                params_append.append(f"td.review_status = ?")
                params.append(review_status)
            if params_append:
                query += " WHERE " + " AND ".join(params_append)

        conditions = []
        if availability:
            conditions.append("d.availability_status = ?")
            params.append(availability)
        if access_type:
            conditions.append("d.access_type = ?")
            params.append(access_type)
        if granularity:
            conditions.append("d.omics_granularity = ?")
            params.append(granularity)
        if organism:
            conditions.append(f"d.organism LIKE ?")
            params.append(f"%{organism}%")

        if conditions:
            where = " WHERE " if "WHERE" not in query else " AND "
            query += where + " AND ".join(conditions)

        query += " ORDER BY d.last_updated DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [DatasetRecord.from_db_row(dict(r)) for r in rows]

    def get_topic(self, topic_id: str) -> Optional[TopicRecord]:
        """获取单个主题"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM topics WHERE topic_id = ?", (topic_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TopicRecord.from_db_row(dict(row))

    def list_topics(self) -> List[TopicRecord]:
        """列出所有主题"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM topics ORDER BY last_searched_at DESC")
        rows = cursor.fetchall()
        return [TopicRecord.from_db_row(dict(r)) for r in rows]

    def get_topic_datasets(
        self,
        topic_id: str,
        review_status: Optional[str] = None,
    ) -> List[dict]:
        """获取主题下的数据集关联（JOIN datasets）"""
        conn = self.get_connection()
        cursor = conn.cursor()

        query = """
            SELECT td.*, d.title, d.organism, d.sample_count, d.omics_type,
                   d.omics_granularity, d.platform, d.availability_status,
                   d.access_type
            FROM topic_datasets td
            JOIN datasets d ON td.gse_id = d.gse_id
            WHERE td.topic_id = ?
        """
        params: list = [topic_id]

        if review_status:
            query += " AND td.review_status = ?"
            params.append(review_status)

        query += " ORDER BY td.added_at DESC"

        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

    def count_datasets(
        self,
        topic_id: Optional[str] = None,
        review_status: Optional[str] = None,
        availability: Optional[str] = None,
    ) -> int:
        """统计数据集数量"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if topic_id:
            query = "SELECT COUNT(DISTINCT td.gse_id) FROM topic_datasets td"
            params: list = []
            where = ["td.topic_id = ?"]
            params.append(topic_id)
            if review_status:
                where.append("td.review_status = ?")
                params.append(review_status)
            if availability:
                query += " JOIN datasets d ON td.gse_id = d.gse_id"
                where.append("d.availability_status = ?")
                params.append(availability)
            query += " WHERE " + " AND ".join(where)
        else:
            query = "SELECT COUNT(*) FROM datasets WHERE 1=1"
            params = []
            if availability:
                query += " AND availability_status = ?"
                params.append(availability)

        cursor.execute(query, params)
        return cursor.fetchone()[0]

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Database connection closed")


# 全局数据库单例
_db: Optional[Database] = None


def get_database(db_path: Optional[str] = None) -> Database:
    """获取全局数据库单例"""
    global _db
    if _db is None:
        _db = Database(db_path)
    return _db


def reset_database() -> None:
    """重置全局数据库（测试用）"""
    global _db
    if _db is not None:
        _db.close()
    _db = None
