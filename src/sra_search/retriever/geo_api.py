"""GEO API 检索器

职责：
- 封装 NCBI E-utilities API
- 处理速率限制和错误重试
- 返回结构化结果

设计原则：
- 单一职责（仅负责 API 调用）
- 错误处理（重试/降级）
- 缓存友好（返回可哈希结果）
"""
import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from sra_search.cache import QueryCache


@dataclass
class GeoRecord:
    """GEO 记录结构

    所有字段类型明确，确保 deterministic 输出
    """
    gse_id: str
    title: str = ""
    organism: str = ""
    platform: str = ""
    sample_count: int = 0
    pubmed_id: str = ""
    publication_date: str = ""
    summary: str = ""
    keywords: List[str] = field(default_factory=list)

    # 原始数据（可选）
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gse_id": self.gse_id,
            "title": self.title,
            "organism": self.organism,
            "platform": self.platform,
            "sample_count": self.sample_count,
            "pubmed_id": self.pubmed_id,
            "publication_date": self.publication_date,
            "summary": self.summary,
            "keywords": self.keywords,
        }

    def compute_hash(self) -> str:
        """计算记录哈希（用于去重）"""
        import hashlib
        raw = f"{self.gse_id}:{self.title}:{self.sample_count}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]


@dataclass
class RetrievalResult:
    """检索结果"""
    query: str
    total_count: int = 0
    records: List[GeoRecord] = field(default_factory=list)
    error: Optional[str] = None
    retried: bool = False

    def is_success(self) -> bool:
        return self.error is None

    def get_hashes(self) -> List[str]:
        return [r.compute_hash() for r in self.records]


class GeoRetriever:
    """GEO API 检索器

    封装 E-utilities 的 esearch 和 esummary 接口
    """

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(
        self,
        email: Optional[str] = None,
        api_key: Optional[str] = None,
        cache: Optional[QueryCache] = None,
    ):
        """初始化

        Args:
            email: NCBI 邮箱（必填）
            api_key: NCBI API Key（可选，提升速率限制）
            cache: 查询缓存实例
        """
        self.email = email or os.environ.get("SRA_SEARCH_NCBI_EMAIL", "anonymous@example.com")
        self.api_key = api_key or os.environ.get("SRA_SEARCH_NCBI_API_KEY")

        # 速率限制配置
        if self.api_key:
            self.rate_limit = 10  # 有 API key: 10次/秒
        else:
            self.rate_limit = 3  # 无 API key: 3次/秒

        self.cache = cache
        self._last_request_time = 0.0

    async def search(
        self,
        query: str,
        retmax: int = 100,
        use_cache: bool = True,
    ) -> RetrievalResult:
        """执行 GEO 搜索

        Args:
            query: 查询字符串
            retmax: 最大返回数量
            use_cache: 是否使用缓存

        Returns:
            RetrievalResult: 检索结果
        """
        # 检查缓存
        if use_cache and self.cache:
            cached = self.cache.get(query)
            if cached:
                return RetrievalResult(
                    query=query,
                    total_count=cached["total_count"],
                    records=self._deserialize_records(cached.get("records", [])),
                    retried=False,
                )

        # 执行搜索
        try:
            result = await self._do_search(query, retmax)

            # 保存缓存
            if use_cache and self.cache and result.is_success():
                self.cache.set(
                    query,
                    {
                        "total_count": result.total_count,
                        "records": [r.to_dict() for r in result.records],
                    },
                )

            return result

        except Exception as e:
            return RetrievalResult(
                query=query,
                error=str(e),
            )

    async def _do_search(self, query: str, retmax: int, retry: int = 0) -> RetrievalResult:
        """执行实际搜索（带重试）"""
        max_retries = 3

        try:
            # 1. esearch 获取 GSE ID 列表
            search_url = f"{self.BASE_URL}/esearch.fcgi"
            params = {
                "db": "gds",
                "term": query,
                "retmax": retmax,
                "usehistory": "y",
                "email": self.email,
            }
            if self.api_key:
                params["api_key"] = self.api_key

            # 速率限制
            await self._rate_limit()

            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, params=params) as resp:
                    if resp.status != 200:
                        if retry < max_retries:
                            await asyncio.sleep(1 * (retry + 1))
                            return await self._do_search(query, retmax, retry + 1)
                        return RetrievalResult(query=query, error=f"HTTP {resp.status}")

                    text = await resp.text()
                    import xml.etree.ElementTree as ET

                    root = ET.fromstring(text)
                    id_list = root.find("IdList")
                    if id_list is None:
                        return RetrievalResult(query=query, total_count=0, records=[])

                    gse_ids = [id_elem.text for id_elem in id_list.findall("Id")]
                    total_count = int(root.find("Count").text or "0")

                    if not gse_ids:
                        return RetrievalResult(query=query, total_count=0, records=[])

                    # 2. esummary 获取详细信息
                    records = await self._fetch_summaries(gse_ids, session)

                    return RetrievalResult(
                        query=query,
                        total_count=total_count,
                        records=records,
                        retried=retry > 0,
                    )

        except asyncio.TimeoutError:
            if retry < max_retries:
                return await self._do_search(query, retmax, retry + 1)
            return RetrievalResult(query=query, error="Timeout after retries")

        except Exception as e:
            if retry < max_retries:
                await asyncio.sleep(1 * (retry + 1))
                return await self._do_search(query, retmax, retry + 1)
            return RetrievalResult(query=query, error=str(e))

    async def _fetch_summaries(self, gse_ids: List[str], session: aiohttp.ClientSession) -> List[GeoRecord]:
        """批量获取 GSE 摘要信息"""
        records = []

        # 分批处理（每次最多 200 个）
        batch_size = 200
        for i in range(0, len(gse_ids), batch_size):
            batch = gse_ids[i : i + batch_size]

            summary_url = f"{self.BASE_URL}/esummary.fcgi"
            params = {
                "db": "gds",
                "id": ",".join(batch),
                "email": self.email,
                "retmode": "json",
            }
            if self.api_key:
                params["api_key"] = self.api_key

            await self._rate_limit()

            try:
                async with session.get(summary_url, params=params) as resp:
                    if resp.status != 200:
                        continue

                    data = await resp.json()
                    result = data.get("result", {})

                    for gse_id in batch:
                        if gse_id not in result:
                            continue

                        item = result[gse_id]
                        record = GeoRecord(
                            gse_id=f"GSE{gse_id}",
                            title=item.get("title", ""),
                            organism=item.get("organism", ""),
                            platform=item.get("platform", ""),
                            sample_count=item.get("sampleset", 0),
                            pubmed_id=item.get("pubmedids", [{}])[0].get("value", ""),
                            publication_date=item.get("pubdate", ""),
                            summary=item.get("summary", ""),
                            keywords=item.get("keywords", "").split(", ") if item.get("keywords") else [],
                        )
                        records.append(record)

            except Exception:
                continue

        return records

    async def _rate_limit(self):
        """速率限制"""
        import time

        min_interval = 1.0 / self.rate_limit
        now = time.time()
        elapsed = now - self._last_request_time

        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)

        self._last_request_time = time.time()

    def _deserialize_records(self, data: List[Dict]) -> List[GeoRecord]:
        """反序列化记录"""
        return [GeoRecord(**r) for r in data]


class FailureHandler:
    """失败处理策略

    根据不同失败类型采取不同策略：
    - 无结果：扩展查询
    - 结果过多：细化查询
    - 查询模糊：请求澄清
    """

    @staticmethod
    def handle_no_result(query: str, original_expander) -> str:
        """无结果时扩展查询"""
        # 尝试移除限制性词汇
        words_to_remove = {"single", "cell", "scRNA", "spatial", "atac", "chip"}
        tokens = query.split()
        expanded = " ".join(t for t in tokens if t.lower() not in words_to_remove)

        if expanded == query:
            # 如果没有可移除的词，添加通配符
            return f"{query}*"

        return expanded

    @staticmethod
    def handle_too_many(query: str, current_count: int) -> str:
        """结果过多时细化查询"""
        # 添加更多限制词
        modifiers = ["human", "disease", "patient"]
        for mod in modifiers:
            if mod.lower() not in query.lower():
                query = f"{query} {mod}"

        return query

    @staticmethod
    def get_clarification_prompt(query: str) -> str:
        """获取澄清请求"""
        return (
            f"您的查询 \"{query}\" 结果可能太宽泛。\n"
            "请考虑提供以下信息以获得更精确的结果：\n"
            "- 物种（human / mouse / rat）\n"
            "- 组学类型（RNA-seq / scRNA-seq / ATAC-seq）\n"
            "- 具体疾病或组织\n"
            "- 是否需要 perturbation 数据"
        )
