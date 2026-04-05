"""查询缓存模块

提供轻量级查询缓存，减少 GEO API 请求，提升响应速度。
缓存策略：TTL 24小时，按查询哈希存储。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

# 默认缓存目录
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "sra-search"


class QueryCache:
    """查询缓存管理器

    缓存内容：
    - query_hash.json: 查询哈希 -> 结果哈希列表
    - results/{hash}.json: 具体的缓存结果

    使用方式：
    ```python
    cache = QueryCache()
    if cached := cache.get(query):
        return cached
    cache.set(query, results)
    ```
    """

    def __init__(self, cache_dir: Path | None = None, ttl_hours: int = 24):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.ttl_hours = ttl_hours

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
        }


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
