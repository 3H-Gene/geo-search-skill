"""查询缓存模块

提供轻量级查询缓存，减少 GEO API 请求，提升响应速度。
缓存策略：TTL 24小时，按查询哈希存储。

特性：
- TTL 过期机制
- 最大存储上限控制
- 过期缓存自动/手动清理
- 缓存统计信息
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

# 默认缓存目录
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "sra-search"

# 默认最大缓存条数
DEFAULT_MAX_ENTRIES = 500

# 默认最大缓存大小（MB）
DEFAULT_MAX_SIZE_MB = 100


class QueryCache:
    """查询缓存管理器

    缓存内容：
    - query_hash.json: 查询哈希 -> 结果哈希列表
    - results/{hash}.json: 具体的缓存结果

    特性：
    - TTL 过期机制（默认 24 小时）
    - 最大存储上限（默认 500 条 / 100MB）
    - LRU 淘汰策略

    使用方式：
    ```python
    cache = QueryCache()
    if cached := cache.get(query):
        return cached
    cache.set(query, results)
    ```
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_hours: int = 24,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_size_mb: int = DEFAULT_MAX_SIZE_MB,
    ):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.ttl_hours = ttl_hours
        self.max_entries = max_entries
        self.max_size_mb = max_size_mb

        # 创建缓存目录
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 缓存索引文件
        self.index_file = self.cache_dir / "query_index.json"

    def _compute_hash(self, query: str) -> str:
        """计算查询哈希"""
        raw = json.dumps({"query": query}, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    def _get_cache_path(self, query_hash: str) -> Path:
        """获取缓存结果文件路径"""
        return self.cache_dir / "results" / f"{query_hash}.json"

    def _load_index(self) -> dict[str, Any]:
        """加载缓存索引"""
        if not self.index_file.exists():
            return {}
        try:
            with open(self.index_file, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load cache index: {e}")
            return {}

    def _save_index(self, index: dict[str, Any]) -> None:
        """保存缓存索引"""
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save cache index: {e}")

    def get(self, query: str) -> dict[str, Any] | None:
        """获取缓存的查询结果

        Args:
            query: 原始查询字符串

        Returns:
            缓存的查询结果（如果存在且未过期），否则返回 None
        """
        query_hash = self._compute_hash(query)

        # 检查索引
        index = self._load_index()
        entry = index.get(query_hash)
        if not entry:
            return None

        # 检查是否过期
        cached_at = entry.get("cached_at", "")
        if cached_at:
            from datetime import datetime, timezone
            try:
                cached_time = datetime.fromisoformat(cached_at)
                age_hours = (datetime.now(timezone.utc) - cached_time).total_seconds() / 3600
                if age_hours > self.ttl_hours:
                    logger.debug(f"Cache expired for query {query_hash}")
                    return None
            except (ValueError, TypeError):
                pass

        # 加载缓存结果
        cache_path = self._get_cache_path(query_hash)
        if not cache_path.exists():
            logger.warning(f"Cache file missing: {cache_path}")
            return None

        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load cache: {e}")
            return None

    def set(self, query: str, result: dict[str, Any]) -> None:
        """缓存查询结果

        Args:
            query: 原始查询字符串
            result: 完整的查询结果字典
        """
        from datetime import datetime, timezone

        query_hash = self._compute_hash(query)

        # 保存结果到单独文件
        cache_path = self._get_cache_path(query_hash)
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save cache: {e}")
            return

        # 更新索引
        index = self._load_index()
        index[query_hash] = {
            "query": query,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "result_hash": result.get("query_hash", ""),
        }
        self._save_index(index)
        logger.debug(f"Cached query: {query_hash}")

        # 强制执行存储限制（LRU 淘汰）
        self.enforce_limits()

    def invalidate(self, query: str | None = None, query_hash: str | None = None) -> None:
        """清除缓存

        Args:
            query: 要清除的查询字符串
            query_hash: 要清除的查询哈希
        """
        if query_hash is None and query:
            query_hash = self._compute_hash(query)

        if query_hash:
            # 删除缓存文件
            cache_path = self._get_cache_path(query_hash)
            if cache_path.exists():
                cache_path.unlink()

            # 更新索引
            index = self._load_index()
            index.pop(query_hash, None)
            self._save_index(index)
            logger.debug(f"Invalidated cache: {query_hash}")
        elif query is None:
            # 清除所有缓存
            results_dir = self.cache_dir / "results"
            if results_dir.exists():
                for f in results_dir.glob("*.json"):
                    f.unlink()
            self._save_index({})
            logger.info("Cleared all cache")

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计信息"""
        index = self._load_index()
        total = len(index)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        expired = 0
        valid = 0
        total_size = 0

        # 计算缓存文件大小
        results_dir = self.cache_dir / "results"
        if results_dir.exists():
            for f in results_dir.glob("*.json"):
                try:
                    total_size += f.stat().st_size
                except OSError:
                    pass

        for entry in index.values():
            cached_at = entry.get("cached_at", "")
            if cached_at:
                try:
                    cached_time = datetime.fromisoformat(cached_at)
                    age_hours = (now - cached_time).total_seconds() / 3600
                    if age_hours > self.ttl_hours:
                        expired += 1
                    else:
                        valid += 1
                except (ValueError, TypeError):
                    pass

        return {
            "total_entries": total,
            "valid_entries": valid,
            "expired_entries": expired,
            "cache_dir": str(self.cache_dir),
            "ttl_hours": self.ttl_hours,
            "max_entries": self.max_entries,
            "max_size_mb": self.max_size_mb,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }

    def clean_expired(self) -> dict[str, int]:
        """清理过期缓存条目

        Returns:
            清理统计 {"removed": count, "freed_bytes": bytes}
        """
        index = self._load_index()
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        to_remove: list[str] = []
        freed_bytes = 0

        for query_hash, entry in index.items():
            cached_at = entry.get("cached_at", "")
            if cached_at:
                try:
                    cached_time = datetime.fromisoformat(cached_at)
                    age_hours = (now - cached_time).total_seconds() / 3600
                    if age_hours > self.ttl_hours:
                        to_remove.append(query_hash)
                        # 累加文件大小
                        cache_path = self._get_cache_path(query_hash)
                        if cache_path.exists():
                            freed_bytes += cache_path.stat().st_size
                except (ValueError, TypeError, OSError):
                    to_remove.append(query_hash)

        # 删除过期条目
        for query_hash in to_remove:
            cache_path = self._get_cache_path(query_hash)
            if cache_path.exists():
                cache_path.unlink()
            index.pop(query_hash, None)

        if to_remove:
            self._save_index(index)
            logger.info(f"清理过期缓存: {len(to_remove)} 条，释放 {freed_bytes / 1024:.1f} KB")

        return {"removed": len(to_remove), "freed_bytes": freed_bytes}

    def enforce_limits(self) -> dict[str, Any]:
        """强制执行存储限制（LRU 淘汰）

        当缓存超过 max_entries 或 max_size_mb 时，删除最旧的条目。

        Returns:
            淘汰统计 {"evicted": count, "freed_bytes": bytes}
        """
        index = self._load_index()
        if len(index) <= self.max_entries:
            # 检查大小
            stats = self.get_stats()
            if stats["total_size_mb"] <= self.max_size_mb:
                return {"evicted": 0, "freed_bytes": 0}

        # 需要淘汰：按缓存时间排序，删除最旧的
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        entries_with_age: list[tuple[str, float]] = []

        for query_hash, entry in index.items():
            cached_at = entry.get("cached_at", "")
            if cached_at:
                try:
                    cached_time = datetime.fromisoformat(cached_at)
                    age_hours = (now - cached_time).total_seconds() / 3600
                    entries_with_age.append((query_hash, age_hours))
                except (ValueError, TypeError):
                    entries_with_age.append((query_hash, float("inf")))

        # 按年龄降序排序（最老的在前）
        entries_with_age.sort(key=lambda x: x[1], reverse=True)

        # 计算需要删除多少
        current_count = len(index)
        current_size = self.get_stats()["total_size_bytes"]

        target_count = self.max_entries - 10  # 保留一些余量
        target_size = self.max_size_mb * 1024 * 1024 * 0.8  # 保留 20% 余量

        to_evict: list[str] = []
        freed_bytes = 0

        for query_hash, _ in entries_with_age:
            if len(index) - len(to_evict) <= target_count:
                break
            if current_size - freed_bytes <= target_size:
                break

            cache_path = self._get_cache_path(query_hash)
            if cache_path.exists():
                freed_bytes += cache_path.stat().st_size
            to_evict.append(query_hash)

        # 执行淘汰
        for query_hash in to_evict:
            cache_path = self._get_cache_path(query_hash)
            if cache_path.exists():
                cache_path.unlink()
            index.pop(query_hash, None)

        if to_evict:
            self._save_index(index)
            logger.info(f"LRU 淘汰: {len(to_evict)} 条，释放 {freed_bytes / 1024:.1f} KB")

        return {"evicted": len(to_evict), "freed_bytes": freed_bytes}


# 全局缓存实例
_cache: QueryCache | None = None


def get_cache(cache_dir: Path | None = None) -> QueryCache:
    """获取全局缓存实例"""
    global _cache
    if _cache is None:
        _cache = QueryCache(cache_dir)
    return _cache


def clear_cache(query: str | None = None, query_hash: str | None = None) -> None:
    """清除缓存的便捷函数"""
    get_cache().invalidate(query, query_hash)
