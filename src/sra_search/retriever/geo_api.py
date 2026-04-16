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

    第一阶段字段（V1 相关性过滤）：
    - gse_id, title, summary, overall_design, organism, sample_count, gdstype, platform

    第二阶段字段（LLM 增强）：
    - pubmed_id, publication_date, ftplink, bioproject_id, platformtitle, suppfile
    - supplementary_files, series_matrix_available
    - gsm_sample_names, gsm_attributes
    """
    gse_id: str = ""
    title: str = ""
    summary: str = ""                       # 第一阶段
    overall_design: str = ""                # 第一阶段
    organism: str = ""                      # 第一阶段
    sample_count: int = 0                   # 第一阶段
    gdstype: str = ""                       # 第一阶段：实验类型（scRNA/bulk等）
    platform: str = ""                       # 第一阶段：平台编号

    # 第二阶段：esummary 扩展
    pubmed_id: str = ""
    publication_date: str = ""
    ftplink: str = ""
    bioproject_id: str = ""
    platformtitle: str = ""                 # 平台名称（人类可读）
    suppfile: str = ""                      # 补充文件类型字符串

    # 第二阶段：网页解析
    supplementary_files: list[dict] = field(default_factory=list)  # [{name, type, size}]
    series_matrix_available: bool = False

    # 第二阶段：GSM 信息（外部获取后填充）
    gsm_sample_names: list[str] = field(default_factory=list)
    gsm_attributes: list[dict] = field(default_factory=list)

    # 原始数据（可选）
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            # 第一阶段
            "gse_id": self.gse_id,
            "title": self.title,
            "summary": self.summary,
            "overall_design": self.overall_design,
            "organism": self.organism,
            "sample_count": self.sample_count,
            "gdstype": self.gdstype,
            "platform": self.platform,
            # 第二阶段：esummary 扩展
            "pubmed_id": self.pubmed_id,
            "publication_date": self.publication_date,
            "ftplink": self.ftplink,
            "bioproject_id": self.bioproject_id,
            "platformtitle": self.platformtitle,
            "suppfile": self.suppfile,
            # 第二阶段：网页解析
            "supplementary_files": self.supplementary_files,
            "series_matrix_available": self.series_matrix_available,
            # 第二阶段：GSM 信息
            "gsm_sample_names": self.gsm_sample_names,
            "gsm_attributes": self.gsm_attributes,
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
        except Exception as e:
            # 网络错误时保守失败：不过滤无效 GSE，丢弃该 ID
            # 这意味着无效 GSE 可能导致下游少结果，但不会把假 GSE 传给 LLM
            logger.warning(
                f"GEO esearch failed for '{gse_id}', skipping validation: {e}"
            )
            return False

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

    async def get_gsm_sample_names(self, gse_id: str) -> list[str]:
        """获取 GSE 关联的所有 GSM 样本名称（用于样本分组推断）。

        通过 GEO esummary JSON 的 samples 字段提取真实的 GSM ID 列表，
        例如 ["GSM1234567", "GSM1234568", ...]。

        Args:
            gse_id: GSE 编号，如 "GSE217561"

        Returns:
            GSM 样本名称列表（降序：GSM 后数字大的在前），空列表表示失败
        """
        try:
            # esearch 找到 GDS UID
            search_params = {
                "db": "gds",
                "term": gse_id,
                "retmax": 1,
                "email": self.email,
            }
            if self.api_key:
                search_params["api_key"] = self.api_key

            await self._rate_limit()

            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session:
                async with session.get(
                    f"{self.BASE_URL}/esearch.fcgi", params=search_params
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"GEO esearch failed for {gse_id}: HTTP {resp.status}")
                        return []
                    text = await resp.text()
                    import xml.etree.ElementTree as ET

                    root = ET.fromstring(text)
                    id_list = root.find("IdList")
                    if id_list is None:
                        return []
                    ids = [e.text for e in id_list.findall("Id") if e.text]
                    if not ids:
                        return []

                # esummary 获取 samples 字段
                uid = ids[0]
                summary_params = {
                    "db": "gds",
                    "id": uid,
                    "retmode": "json",
                    "email": self.email,
                }
                if self.api_key:
                    summary_params["api_key"] = self.api_key

                await self._rate_limit()

                async with session.get(
                    f"{self.BASE_URL}/esummary.fcgi", params=summary_params
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"GEO esummary failed for {gse_id}: HTTP {resp.status}")
                        return []
                    data = await resp.json(content_type=None)

                result = data.get("result", {})
                item = result.get(uid, {})
                samples_raw = item.get("samples", [])

                if not isinstance(samples_raw, list):
                    return []

                # 提取 GSM 编号（降序排列：数字大的在前）
                gsm_names: list[str] = []
                for s in samples_raw:
                    acc = s.get("accession", "")
                    if acc and acc.startswith("GSM") and acc[3:].isdigit():
                        gsm_names.append(acc)

                gsm_names.sort(key=lambda x: int(x[3:]), reverse=True)
                logger.debug(
                    f"[{gse_id}] fetched {len(gsm_names)} GSM sample names"
                )
                return gsm_names

        except Exception as e:
            logger.warning(f"Failed to fetch GSM samples for {gse_id}: {e}")
            return []

    async def fetch_gsm_samples_batch(
        self,
        gse_ids: list[str],
        concurrency: int = 5,
    ) -> dict[str, list[str]]:
        """批量获取多个 GSE 的 GSM 样本名称。

        使用信号量控制并发数，避免对 NCBI 造成过大压力。
        单个 GSE 失败不影响其他 GSE。

        Args:
            gse_ids: GSE ID 列表
            concurrency: 最大并发数（默认 5）

        Returns:
            dict[GSE_ID, GSM列表]，失败/无结果的 GSE 不出现在字典中
        """
        if not gse_ids:
            return {}

        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_one(gse_id: str) -> tuple[str, list[str]]:
            async with semaphore:
                result = await self.get_gsm_sample_names(gse_id)
                return gse_id, result

        tasks = [_fetch_one(gse_id) for gse_id in gse_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: dict[str, list[str]] = {}
        for item in results:
            if isinstance(item, BaseException):
                logger.warning(f"GSM batch fetch exception: {item}")
                continue
            gse_id, gsm_names = item
            if gsm_names:  # 只保留有结果的
                out[gse_id] = gsm_names

        logger.debug(f"[GSM batch] fetched {len(out)}/{len(gse_ids)} GSE with GSM samples")
        return out

    async def get_gsm_attributes(self, gsm_id: str) -> dict[str, Any]:
        """获取单个 GSM 样品的详细属性（Sample Attributes）。

        通过 GEO esummary JSON 获取 GSM 的 Sample Attribute，如：
        - source_name: 组织/细胞来源
        - treatment: 处理条件
        - condition: 疾病/状态
        - group: 分组信息
        - individual: 供体编号

        Args:
            gsm_id: GSM 编号，如 "GSM1234567"

        Returns:
            dict: 包含 title, sample_attributes, pubmed_id 等字段的字典
        """
        try:
            # 先通过 esearch 找到 GSM 对应的 GDS UID
            search_params = {
                "db": "gds",
                "term": gsm_id,
                "retmax": 1,
                "email": self.email,
            }
            if self.api_key:
                search_params["api_key"] = self.api_key

            await self._rate_limit()

            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session:
                async with session.get(
                    f"{self.BASE_URL}/esearch.fcgi", params=search_params
                ) as resp:
                    if resp.status != 200:
                        return {}
                    text = await resp.text()
                    import xml.etree.ElementTree as ET

                    root = ET.fromstring(text)
                    id_list = root.find("IdList")
                    if id_list is None:
                        return {}
                    ids = [e.text for e in id_list.findall("Id") if e.text]
                    if not ids:
                        return {}

                # esummary 获取详细信息
                uid = ids[0]
                summary_params = {
                    "db": "gds",
                    "id": uid,
                    "retmode": "json",
                    "email": self.email,
                }
                if self.api_key:
                    summary_params["api_key"] = self.api_key

                await self._rate_limit()

                async with session.get(
                    f"{self.BASE_URL}/esummary.fcgi", params=summary_params
                ) as resp:
                    if resp.status != 200:
                        return {}
                    data = await resp.json(content_type=None)

                result = data.get("result", {})
                item = result.get(uid, {})

                # 提取关键字段
                attributes_raw = item.get("sample_type", [])
                if isinstance(attributes_raw, str):
                    attributes_raw = [attributes_raw]

                return {
                    "gsm_id": gsm_id,
                    "title": item.get("title", ""),
                    "accession": item.get("accession", ""),
                    "sample_type": attributes_raw,
                    "pubmed_id": item.get("pubmed_id", ""),
                    "GPL": item.get("GPL", ""),  # 平台ID
                    "taxon_id": item.get("taxon_id", ""),
                }

        except Exception as e:
            logger.warning(f"Failed to fetch attributes for {gsm_id}: {e}")
            return {}

    async def fetch_gsm_attributes_batch(
        self,
        gse_to_gsm: dict[str, list[str]],
        concurrency: int = 5,
    ) -> dict[str, list[dict[str, Any]]]:
        """批量获取多个 GSE 的 GSM 样品属性。

        Args:
            gse_to_gsm: dict[GSE_ID, GSM列表]
            concurrency: 最大并发数（默认5）

        Returns:
            dict[GSE_ID, GSM属性列表]
        """
        if not gse_to_gsm:
            return {}

        # 展平所有 GSM
        all_gsms: list[tuple[str, str]] = []  # [(gse_id, gsm_id), ...]
        for gse_id, gsm_list in gse_to_gsm.items():
            for gsm_id in gsm_list[:50]:  # 限制每个 GSE 最多取 50 个 GSM
                all_gsms.append((gse_id, gsm_id))

        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_one(gse_id: str, gsm_id: str) -> tuple[str, str, dict[str, Any]]:
            async with semaphore:
                attrs = await self.get_gsm_attributes(gsm_id)
                return gse_id, gsm_id, attrs

        tasks = [_fetch_one(gse_id, gsm_id) for gse_id, gsm_id in all_gsms]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 按 GSE 分组
        out: dict[str, list[dict[str, Any]]] = {gse_id: [] for gse_id in gse_to_gsm}
        for item in results:
            if isinstance(item, BaseException):
                continue
            gse_id, gsm_id, attrs = item
            if attrs:
                out[gse_id].append(attrs)

        logger.debug(f"[GSM attrs] fetched attributes for {len(all_gsms)} GSMs across {len(gse_to_gsm)} GSEs")
        return out

    def _extract_sample_attributes(self, text: str) -> dict[str, str]:
        """从文本中提取样本属性。

        常见格式：
        - "source_name: PBMC"
        - "treatment: LPS stimulation"
        - "disease state: gout flare"

        Args:
            text: 属性文本

        Returns:
            dict[属性名, 属性值]
        """
        attrs: dict[str, str] = {}
        lines = text.split("\n")
        for line in lines:
            if ":" in line:
                parts = line.split(":", 1)
                key = parts[0].strip().lower().replace(" ", "_")
                value = parts[1].strip()
                attrs[key] = value
        return attrs

    async def fetch_gsm_attributes_batch_optimized(
        self,
        gse_to_gsm: dict[str, list[str]],
        chunk_size: int = 100,
    ) -> dict[str, list[dict[str, Any]]]:
        """批量获取多个 GSE 的 GSM 样品属性（优化版）。

        真正的批量查询策略：
        1. 分块批量 esearch：每块 100 个 GSM 的 OR 查询获取 GDS UID
        2. 分块批量 esummary：用逗号分隔的 UID 列表一次获取详细信息

        Args:
            gse_to_gsm: dict[GSE_ID, GSM列表]
            chunk_size: 每批处理的 GSM 数量（默认100）

        Returns:
            dict[GSE_ID, GSM属性列表]
        """
        if not gse_to_gsm:
            return {}

        # 展平并限制每个 GSE 的 GSM 数量
        all_gsms: list[tuple[str, str]] = []  # [(gse_id, gsm_id), ...]
        for gse_id, gsm_list in gse_to_gsm.items():
            for gsm_id in gsm_list[:50]:
                all_gsms.append((gse_id, gsm_id))

        if not all_gsms:
            return {}

        gsm_ids = list(set([gsm_id for _, gsm_id in all_gsms]))  # 去重
        logger.debug(f"[GSM batch] fetching attributes for {len(gsm_ids)} unique GSMs")

        # 分块处理
        results: dict[str, dict[str, Any]] = {}
        for i in range(0, len(gsm_ids), chunk_size):
            chunk = gsm_ids[i:i + chunk_size]
            chunk_results = await self._batch_fetch_gsm_chunk(chunk)
            results.update(chunk_results)
            logger.debug(f"[GSM batch] processed chunk {i // chunk_size + 1}/{(len(gsm_ids) - 1) // chunk_size + 1}")

        # 按 GSE 分组
        out: dict[str, list[dict[str, Any]]] = {gse_id: [] for gse_id in gse_to_gsm}
        for gse_id, gsm_id in all_gsms:
            if gsm_id in results:
                out[gse_id].append(results[gsm_id])

        total_attrs = sum(len(v) for v in out.values())
        logger.info(f"[GSM batch] fetched {total_attrs} attributes for {len(gse_to_gsm)} GSEs ({len(gsm_ids)} GSMs)")
        return out

    async def _batch_fetch_gsm_chunk(self, gsm_ids: list[str]) -> dict[str, dict[str, Any]]:
        """批量获取一批 GSM 的属性。

        Args:
            gsm_ids: GSM ID 列表

        Returns:
            dict[gsm_id, 属性字典]
        """
        if not gsm_ids:
            return {}

        results: dict[str, dict[str, Any]] = {}

        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ) as session:

                # Step 1: 批量 esearch 获取 GDS UID
                # 构建 OR 查询: "GSM1234567[Accession] OR GSM1234568[Accession]"
                term = " OR ".join([f"{gsm}[Accession]" for gsm in gsm_ids])
                search_params = {
                    "db": "gds",
                    "term": term,
                    "retmax": len(gsm_ids),
                    "email": self.email,
                }
                if self.api_key:
                    search_params["api_key"] = self.api_key

                await self._rate_limit()

                async with session.get(
                    f"{self.BASE_URL}/esearch.fcgi", params=search_params
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[GSM batch] esearch failed: {resp.status}")
                        return {}
                    text = await resp.text()

                import xml.etree.ElementTree as ET
                root = ET.fromstring(text)
                id_list = root.find("IdList")
                if id_list is None or len(id_list) == 0:
                    logger.debug(f"[GSM batch] no GDS UIDs found for {len(gsm_ids)} GSMs")
                    return {}

                # 建立 GSM ID -> UID 的映射
                uid_list: list[str] = []
                gsm_to_uid: dict[str, str] = {}
                for id_elem in id_list.findall("Id"):
                    uid = id_elem.text
                    if uid:
                        uid_list.append(uid)

                # Step 2: 批量 esummary 获取详细信息
                if uid_list:
                    summary_params = {
                        "db": "gds",
                        "id": ",".join(uid_list),  # 逗号分隔的 UID 列表
                        "retmode": "json",
                        "email": self.email,
                    }
                    if self.api_key:
                        summary_params["api_key"] = self.api_key

                    await self._rate_limit()

                    async with session.get(
                        f"{self.BASE_URL}/esummary.fcgi", params=summary_params
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"[GSM batch] esummary failed: {resp.status}")
                            return {}
                        data = await resp.json(content_type=None)

                    result_data = data.get("result", {})
                    for uid, item in result_data.items():
                        if uid == "uids":  # 跳过 uids 列表本身
                            continue
                        # 从 title 或 accession 中提取 GSM ID
                        title = item.get("title", "")
                        accession = item.get("accession", "")
                        # 尝试匹配 GSM ID（通常在 title 中）
                        matched_gsm = self._extract_gsm_from_item(item, gsm_ids)
                        if matched_gsm:
                            attributes_raw = item.get("sample_type", [])
                            if isinstance(attributes_raw, str):
                                attributes_raw = [attributes_raw]
                            results[matched_gsm] = {
                                "gsm_id": matched_gsm,
                                "title": title,
                                "accession": accession,
                                "sample_type": attributes_raw,
                                "pubmed_id": item.get("pubmed_id", ""),
                                "GPL": item.get("GPL", ""),
                                "taxon_id": item.get("taxon_id", ""),
                            }

        except Exception as e:
            logger.warning(f"[GSM batch] chunk fetch failed: {e}")

        return results

    def _extract_gsm_from_item(
        self, item: dict[str, Any], gsm_ids: list[str]
    ) -> str | None:
        """从 esummary item 中提取对应的 GSM ID。

        NCBI esummary 返回的 GDS 记录中，GSM ID 通常在 title 字段中，
        格式如 "GSM1234567: Sample description"

        Args:
            item: esummary 返回的 item 字典
            gsm_ids: 目标 GSM ID 列表

        Returns:
            匹配的 GSM ID 或 None
        """
        title = item.get("title", "")
        accession = item.get("accession", "")

        # 尝试从 accession 直接匹配
        if accession and accession in gsm_ids:
            return accession

        # 尝试从 title 中提取（格式: "GSM1234567: ..."）
        if title:
            for gsm_id in gsm_ids:
                if title.startswith(f"{gsm_id}:"):
                    return gsm_id
                if gsm_id in title:
                    return gsm_id

        return None

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

                        # 使用 esummary 返回的 accession 字段筛选 GSE 记录
                        # GEO esearch 返回 GDS UID，esummary 返回 accession
                        # 非 GSE 开头（如 GSM、GPL）的记录直接跳过
                        accession = item.get("accession", "")
                        if not accession or not accession.startswith("GSE"):
                            # 非 GSE 记录（如 GSM UID 3xxxxxxx）直接跳过
                            logger.debug(f"Skipping non-GSE accession '{accession}' from UID {gse_id}")
                            continue

                        # 验证 GSE 位数（防御性检查）
                        gse_digits = accession[3:]
                        if not (gse_digits.isdigit() and 1 <= len(gse_digits) <= 7):
                            logger.warning(f"Skipping invalid GSE accession '{accession}' (digits: {gse_digits})")
                            continue
                        gse_accession = accession

                        # 解析 suppfile（补充文件类型字符串）
                        suppfile_str = item.get("suppfile", "")

                        # 检测 series_matrix（从 suppfile 字符串中判断）
                        series_matrix_found = bool(suppfile_str and "series_matrix" in suppfile_str.lower())

                        record = GeoRecord(
                            # 第一阶段字段
                            gse_id=gse_accession,
                            title=item.get("title", ""),
                            summary=item.get("summary", ""),
                            overall_design=item.get("overall_design", ""),
                            organism=item.get("taxon", ""),
                            sample_count=int(item.get("n_samples", 0) or 0),
                            gdstype=item.get("gdstype", ""),
                            platform=item.get("gpl", ""),
                            # 第二阶段字段：esummary 扩展
                            pubmed_id=item.get("pubmedids", [""])[0] if item.get("pubmedids") else "",
                            publication_date=item.get("pdat", ""),
                            ftplink=item.get("ftplink", ""),
                            bioproject_id=item.get("bioproject", ""),
                            platformtitle=item.get("platformtitle", ""),
                            suppfile=suppfile_str,
                            # 第二阶段字段：网页解析（暂用 suppfile，后续 P1 完善）
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
        # 新字段默认值
        new_fields = {
            "gdstype": "",
            "platformtitle": "",
            "suppfile": "",
            "gsm_sample_names": [],
            "gsm_attributes": [],
        }
        for r in data:
            # 旧缓存可能缺少新字段，提供默认值
            for field, default in new_fields.items():
                r.setdefault(field, default)
            # 也保留旧字段的默认值
            r.setdefault("overall_design", "")
            r.setdefault("ftplink", "")
            r.setdefault("supplementary_files", [])
            r.setdefault("series_matrix_available", False)
            records.append(GeoRecord(**r))
        return records

    async def fetch_supp_files_detail(self, gse_id: str) -> list[dict[str, Any]]:
        """从 GEO 网页获取补充文件详细信息（第二阶段）。

        esummary 只返回 suppfile 类型字符串（如 "MTX, TSV"），
        真正的文件名、大小、类型需要解析 GEO 网页。

        Args:
            gse_id: GSE 编号，如 "GSE217561"

        Returns:
            list[dict]：补充文件列表，每个 dict 包含 name, size, type
            例如：[{"name": "GSE217561_RAW.tar", "size": "729.3 Mb", "type": "TAR"}]
        """
        try:
            url = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse_id}&view=full"

            await self._rate_limit()

            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to fetch supp files for {gse_id}: HTTP {resp.status}")
                        return []

                    html = await resp.text()

            # 解析 HTML 表格
            # 结构：<table><thead><tr><th>Supplementary file</th><th>Size</th>...
            files: list[dict[str, Any]] = []

            try:
                from html.parser import HTMLParser

                class SuppFilesParser(HTMLParser):
                    def __init__(self: "SuppFilesParser") -> None:
                        super().__init__()
                        self.in_table = False
                        self.in_tbody = False
                        self.in_row = False
                        self.in_cell = False
                        self.current_row: list[str] = []
                        self.current_cell = ""
                        self.cell_count = 0
                        self.files: list[dict[str, Any]] = []
                        self.skip_next_td = False  # 跳过 Download 列

                    def handle_starttag(self: "SuppFilesParser", tag: str, attrs: list[tuple[str, str | None]]) -> None:
                        if tag == "table":
                            self.in_table = True
                            self.in_tbody = False
                            self.in_row = False
                            self.in_cell = False
                            self.current_row = []
                            self.current_cell = ""
                            self.cell_count = 0
                        elif tag == "tbody" and self.in_table:
                            self.in_tbody = True
                        elif tag == "tr" and self.in_tbody:
                            self.in_row = True
                            self.current_row = []
                            self.current_cell = ""
                            self.cell_count = 0
                            self.skip_next_td = False
                        elif tag == "td" and self.in_row:
                            self.in_cell = True
                            self.current_cell = ""
                            # 第3列是 Download 列，跳过
                            if self.cell_count == 2:
                                self.skip_next_td = True

                    def handle_endtag(self: "SuppFilesParser", tag: str) -> None:
                        if tag == "td" and self.in_cell:
                            self.in_cell = False
                            self.current_row.append(self.current_cell.strip())
                            self.cell_count += 1
                            self.skip_next_td = False
                        elif tag == "tr" and self.in_row:
                            self.in_row = False
                            # 解析行：第0列=文件名, 第1列=大小, 第3列=类型
                            # 第2列是 Download 链接，跳过
                            if len(self.current_row) >= 4:
                                name = self.current_row[0]
                                size = self.current_row[1]
                                ftype = self.current_row[3]
                                # 跳过空行或资源链接行
                                if name and not name.startswith("/"):
                                    self.files.append({
                                        "name": name,
                                        "size": size,
                                        "type": ftype,
                                    })
                        elif tag == "tbody" and self.in_tbody:
                            self.in_tbody = False
                        elif tag == "table":
                            self.in_table = False

                    def handle_data(self: "SuppFilesParser", data: str) -> None:
                        if self.in_cell and not self.skip_next_td:
                            self.current_cell += data

                parser = SuppFilesParser()
                parser.feed(html)
                files = parser.files

            except Exception as e:
                logger.warning(f"Failed to parse supp files HTML for {gse_id}: {e}")
                return []

            if files:
                logger.debug(f"Fetched {len(files)} supp files for {gse_id}")

            return files

        except Exception as e:
            logger.warning(f"Error fetching supp files for {gse_id}: {e}")
            return []

    async def fetch_supp_files_batch(
        self, gse_ids: list[str], concurrency: int = 3
    ) -> dict[str, list[dict[str, Any]]]:
        """批量获取多个 GSE 的补充文件详情（第二阶段）。

        Args:
            gse_ids: GSE 编号列表
            concurrency: 最大并发数（默认3，避免频繁请求）

        Returns:
            dict[GSE_ID, supp_files列表]
        """
        if not gse_ids:
            return {}

        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_one(gse_id: str) -> tuple[str, list[dict[str, Any]]]:
            async with semaphore:
                files = await self.fetch_supp_files_detail(gse_id)
                return gse_id, files

        tasks = [_fetch_one(gse_id) for gse_id in gse_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: dict[str, list[dict[str, Any]]] = {}
        for item in results:
            if isinstance(item, BaseException):
                continue
            gse_id, files = item
            if files:
                out[gse_id] = files

        return out


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
