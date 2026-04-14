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
from typing import Any

import aiohttp
from loguru import logger

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
    overall_design: str = ""      # 实验设计详细描述（GEO 特有字段）
    keywords: list[str] = field(default_factory=list)
    bioproject_id: str = ""       # GEO esummary 的 bioproject 字段
    ftplink: str = ""              # FTP 下载链接
    supplementary_files: list[dict] = field(default_factory=list)  # [{name, type, size}]
    series_matrix_available: bool = False  # 是否提供 Series Matrix 文件

    # 原始数据（可选）
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gse_id": self.gse_id,
            "title": self.title,
            "organism": self.organism,
            "platform": self.platform,
            "sample_count": self.sample_count,
            "pubmed_id": self.pubmed_id,
            "publication_date": self.publication_date,
            "summary": self.summary,
            "overall_design": self.overall_design,
            "keywords": self.keywords,
            "bioproject_id": self.bioproject_id,
            "ftplink": self.ftplink,
            "supplementary_files": self.supplementary_files,
            "series_matrix_available": self.series_matrix_available,
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
    records: list[GeoRecord] = field(default_factory=list)
    error: str | None = None
    retried: bool = False

    def is_success(self) -> bool:
        return self.error is None

    def get_hashes(self) -> list[str]:
        return [r.compute_hash() for r in self.records]


class GeoRetriever:
    """GEO API 检索器

    封装 E-utilities 的 esearch 和 esummary 接口
    """

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(
        self,
        email: str | None = None,
        api_key: str | None = None,
        cache: QueryCache | None = None,
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

    async def gse_exists(self, gse_id: str) -> bool:
        """验证单个 GSE ID 是否在 GEO 数据库中真实存在。

        SRA 元数据的 study_alias 字段是自由文本，任何字符串都可以放进去。
        此方法通过 esearch 确认 GSE ID 确实存在于 GEO（count > 0）。

        Args:
            gse_id: GSE 编号，如 "GSE108395887"

        Returns:
            True if the GSE exists in GEO, False otherwise.
        """
        try:
            params = {
                "db": "gds",
                "term": gse_id,
                "retmax": 1,
                "email": self.email,
            }
            if self.api_key:
                params["api_key"] = self.api_key

            await self._rate_limit()

            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session:
                async with session.get(
                    f"{self.BASE_URL}/esearch.fcgi", params=params
                ) as resp:
                    if resp.status != 200:
                        return False
                    text = await resp.text()
                    import xml.etree.ElementTree as ET

                    root = ET.fromstring(text)
                    count_elem = root.find("Count")
                    if count_elem is None:
                        return False
                    return int(count_elem.text or "0") > 0
        except Exception:
            # 网络错误时保守处理：不过滤，保留该 GSE
            return True

    async def filter_valid_gse_ids(self, gse_ids: list[str]) -> list[str]:
        """批量验证 GSE ID 列表，返回仅存在于 GEO 的 ID。

        Args:
            gse_ids: GSE 编号列表

        Returns:
            仅存在于 GEO 的 GSE ID 列表（保持原顺序）
        """
        if not gse_ids:
            return []
        valid: list[str] = []
        for gid in gse_ids:
            if await self.gse_exists(gid):
                valid.append(gid)
        return valid

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

            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session:
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

                    gse_ids = [id_elem.text for id_elem in id_list.findall("Id") if id_elem.text]
                    count_elem = root.find("Count")
                    total_count = int(count_elem.text or "0") if count_elem is not None else 0

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

    async def _fetch_summaries(self, gse_ids: list[str], session: aiohttp.ClientSession) -> list[GeoRecord]:
        """批量获取 GSE 摘要信息"""
        records = []
        seen_accessions: set[str] = set()  # 用于去重，防止同一 GSE 从不同 UID 转换后重复

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
                        logger.warning(f"GEO esummary HTTP {resp.status} for batch {i}")
                        continue

                    # content_type=None: 兼容 NCBI 有时返回 text/html content-type 的情况
                    try:
                        data = await resp.json(content_type=None)
                    except Exception as json_err:
                        logger.warning(f"GEO esummary JSON parse error: {json_err}")
                        continue
                    result = data.get("result", {})

                    for gse_id in batch:
                        if gse_id not in result:
                            continue

                        item = result[gse_id]

                        # 优先使用 esummary 返回的 accession 字段（真实 GSE 编号）
                        # GEO esearch 返回的是内部 GDS UID（如 200272217）
                        # esummary 的 JSON 中 "accession" 字段才是真正的 GSE ID（如 "GSE272217"）
                        accession = item.get("accession", "")
                        if accession and accession.startswith("GSE"):
                            gse_accession = accession
                        else:
                            # fallback：GDS UID 减去 200000000 得到 GSE 编号
                            # 例：200272217 - 200000000 = 272217 → GSE272217
                            try:
                                uid_int = int(gse_id)
                                if uid_int > 200000000:
                                    gse_num = uid_int - 200000000
                                    gse_accession = f"GSE{gse_num}"
                                else:
                                    gse_accession = f"GSE{gse_id}"
                            except (ValueError, TypeError):
                                gse_accession = f"GSE{gse_id}"

                        # 解析补充文件列表
                        # supplementary_file 可以是 str 或 list[dict]
                        # dict 格式: {name, type, size}
                        supp_raw = item.get("supplementary_file", [])
                        supp_files: list[dict] = []
                        series_matrix_found = False
                        if isinstance(supp_raw, list):
                            for f in supp_raw:
                                if isinstance(f, dict):
                                    name = f.get("name", "")
                                    ftype = f.get("type", "")
                                    fsize = f.get("size", 0)
                                else:
                                    name = str(f)
                                    ftype = ""
                                    fsize = 0
                                supp_files.append({"name": name, "type": ftype, "size": fsize})
                                if "series_matrix" in name.lower():
                                    series_matrix_found = True
                                # 也检查 type 字段
                                if ftype and "series_matrix" in ftype.lower():
                                    series_matrix_found = True
                        elif isinstance(supp_raw, str) and supp_raw:
                            # 旧格式：直接是字符串
                            supp_files.append({"name": supp_raw, "type": "", "size": 0})
                            if "series_matrix" in supp_raw.lower():
                                series_matrix_found = True

                        record = GeoRecord(
                            gse_id=gse_accession,
                            title=item.get("title", ""),
                            # GEO esummary 字段名（已通过 API 实测确认）：
                            #   taxon      → organism（物种学名）
                            #   gpl        → platform（GPL 平台编号）
                            #   n_samples  → sample_count（样品总数）
                            #   pdat       → publication_date（入库日期）
                            #   pubmedids  → list[str]（直接是 PMID 字符串，非嵌套对象）
                            #   overall_design → 实验设计详细描述
                            #   supplementary_file → 补充文件列表
                            #   ftplink    → FTP 下载链接
                            organism=item.get("taxon", ""),
                            platform=item.get("gpl", ""),
                            sample_count=int(item.get("n_samples", 0) or 0),
                            pubmed_id=item.get("pubmedids", [""])[0] if item.get("pubmedids") else "",
                            publication_date=item.get("pdat", ""),
                            summary=item.get("summary", ""),
                            overall_design=item.get("overall_design", ""),
                            keywords=[],  # GEO esummary 无 keywords 字段
                            bioproject_id=item.get("bioproject", ""),
                            ftplink=item.get("ftplink", ""),
                            supplementary_files=supp_files,
                            series_matrix_available=series_matrix_found,
                        )
                        if gse_accession not in seen_accessions:
                            seen_accessions.add(gse_accession)
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

    def _deserialize_records(self, data: list[dict]) -> list[GeoRecord]:
        """反序列化记录（兼容旧缓存格式）"""
        records = []
        for r in data:
            # 旧缓存可能缺少新字段，提供默认值
            r.setdefault("bioproject_id", "")
            r.setdefault("overall_design", "")
            r.setdefault("ftplink", "")
            r.setdefault("supplementary_files", [])
            r.setdefault("series_matrix_available", False)
            records.append(GeoRecord(**r))
        return records


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
