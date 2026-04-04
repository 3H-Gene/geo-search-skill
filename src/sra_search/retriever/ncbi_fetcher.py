"""NCBI 网页爬取工具（Fallback 方案）

当 Entrez API 返回信息不足时，直接爬取 NCBI/GEO/BioSample/BioProject 网页，
提取结构化元数据。

参考：ArcInstitute/SRAgent — SRAgent/tools/ncbi_fetch.py
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, List, Optional, Union

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; sra-search/0.4; research purposes)",
}
_REQUEST_INTERVAL = 0.4  # NCBI 礼貌爬取间隔（秒）


def _make_session() -> aiohttp.ClientSession:
    """创建带默认 Header 的 aiohttp Session"""
    return aiohttp.ClientSession(
        headers=_DEFAULT_HEADERS,
        connector=aiohttp.TCPConnector(ssl=False),
    )


async def _fetch_url(
    url: str,
    session: Optional[aiohttp.ClientSession] = None,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> Optional[str]:
    """抓取 URL，带指数退避重试

    Args:
        url: 目标 URL
        session: 可复用的 aiohttp Session（None 则临时创建）
        max_retries: 最大重试次数
        base_delay: 首次重试等待秒数（后续翻倍）

    Returns:
        页面 HTML 文本，失败返回 None
    """
    close_after = session is None
    if session is None:
        session = _make_session()

    try:
        for attempt in range(max_retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    elif resp.status == 429:
                        wait = base_delay * (2 ** attempt)
                        logger.warning(f"HTTP 429 from {url}, retrying in {wait}s")
                        await asyncio.sleep(wait)
                    else:
                        logger.warning(f"HTTP {resp.status} from {url}")
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    wait = base_delay * (2 ** attempt)
                    logger.warning(f"Request error: {e}, retrying in {wait}s")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Failed to fetch {url}: {e}")
                    return None
    finally:
        if close_after:
            await session.close()

    return None


# ──────────────────────────────────────────────
# GEO 页面解析
# ──────────────────────────────────────────────

_GEO_SECTION_NAMES = [
    "Status", "Title", "Organism", "Experiment type",
    "Summary", "Overall design", "Contributor(s)", "Citation(s)",
    "Platforms", "Samples", "BioProject", "SRA",
]


def _parse_geo_page(html: str, gse_id: str) -> Dict[str, str]:
    """从 GEO acc.cgi 页面解析元数据

    Returns:
        字段字典，如 {"title": "...", "organism": "...", "summary": "..."}
    """
    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, str] = {"gse_id": gse_id}

    for section_name in _GEO_SECTION_NAMES:
        for row in soup.find_all("tr"):
            text = row.get_text(strip=True)
            if text.startswith(section_name):
                # 去掉前缀，取剩余内容
                value = text[len(section_name):].strip()
                key = section_name.lower().replace(" ", "_").replace("(s)", "s")
                result[key] = value
                break

    # 尝试提取 sample count
    try:
        sample_row = soup.find("td", string=re.compile(r"Sample[s]?", re.I))
        if sample_row:
            next_td = sample_row.find_next_sibling("td")
            if next_td:
                m = re.search(r"\d+", next_td.get_text())
                if m:
                    result["sample_count"] = int(m.group())
    except Exception:
        pass

    return result


async def fetch_geo_record(
    gse_id: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, str]:
    """爬取 GEO 数据集详情页

    Args:
        gse_id: GEO 编号，如 "GSE110878"
        session: 可复用 aiohttp Session

    Returns:
        元数据字典；失败时返回仅含 gse_id 的字典
    """
    url = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse_id}"
    html = await _fetch_url(url, session=session)
    if html is None:
        logger.warning(f"Failed to fetch GEO record: {gse_id}")
        return {"gse_id": gse_id}

    result = _parse_geo_page(html, gse_id)
    await asyncio.sleep(_REQUEST_INTERVAL)
    return result


async def fetch_geo_records(
    gse_ids: List[str],
    max_concurrency: int = 3,
) -> Dict[str, Dict[str, str]]:
    """批量爬取 GEO 记录（有并发限制）

    Args:
        gse_ids: GSE 编号列表
        max_concurrency: 最大并发数

    Returns:
        {gse_id: 元数据字典}
    """
    sem = asyncio.Semaphore(max_concurrency)
    results: Dict[str, Dict[str, str]] = {}

    async with _make_session() as session:
        async def _fetch_one(gse_id: str):
            async with sem:
                data = await fetch_geo_record(gse_id, session=session)
                results[gse_id] = data

        await asyncio.gather(*[_fetch_one(g) for g in gse_ids])

    return results


# ──────────────────────────────────────────────
# SRA 页面解析
# ──────────────────────────────────────────────

async def fetch_sra_record(
    term: str,
    database: str = "sra",
    session: Optional[aiohttp.ClientSession] = None,
) -> str:
    """爬取 NCBI SRA 或 GEO(gds) 页面

    Args:
        term: Entrez ID 或 SRA 编号（如 SRX4967527）
        database: "sra" 或 "gds"
        session: 可复用 aiohttp Session

    Returns:
        页面主要文本内容；失败返回空字符串
    """
    url = f"https://www.ncbi.nlm.nih.gov/{database}/?term={term}"
    html = await _fetch_url(url, session=session)
    if html is None:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # 尝试定位主要内容区域
    section = soup.find("p", class_="details expand e-hidden")
    if section is None:
        section = soup.find("div", id="maincontent")

    if section is None:
        return ""

    text = re.sub(r"\n\n+", "\n\n", section.get_text(strip=True))
    await asyncio.sleep(_REQUEST_INTERVAL)
    return text


# ──────────────────────────────────────────────
# BioSample 页面解析
# ──────────────────────────────────────────────

async def fetch_biosample_record(
    biosample_id: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, Union[str, Dict[str, str]]]:
    """爬取 NCBI BioSample 详情

    Returns:
        含 title/organism/bioproject/attributes 的字典
    """
    url = f"https://www.ncbi.nlm.nih.gov/biosample/{biosample_id}"
    html = await _fetch_url(url, session=session)
    if html is None:
        return {"biosample_id": biosample_id}

    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, Union[str, Dict]] = {"biosample_id": biosample_id}

    # Title
    try:
        result["title"] = soup.select_one("h2.title").get_text(strip=True)
    except AttributeError:
        pass

    # Organism
    try:
        org_dd = soup.find("dt", string="Organism").find_next_sibling("dd")
        organism = org_dd.get_text(" ", strip=True)
        result["organism"] = organism.split("cellular organisms")[0].strip()
    except AttributeError:
        pass

    # BioProject
    try:
        bp_dd = soup.find("dt", string="BioProject").find_next_sibling("dd")
        result["bioproject"] = bp_dd.get_text(" ", strip=True)
    except AttributeError:
        pass

    # Attributes table
    try:
        tbl = soup.find("dt", string="Attributes").find_next_sibling("dd").find("table")
        result["attributes"] = {
            row.th.get_text(strip=True): row.td.get_text(" ", strip=True)
            for row in tbl.find_all("tr")
            if row.th and row.td
        }
    except AttributeError:
        pass

    await asyncio.sleep(_REQUEST_INTERVAL)
    return result


# ──────────────────────────────────────────────
# BioProject 页面解析
# ──────────────────────────────────────────────

async def fetch_bioproject_record(
    bioproject_id: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, Union[str, Dict[str, str]]]:
    """爬取 NCBI BioProject 详情

    Returns:
        含 title/subtitle/attributes 的字典
    """
    url = f"https://www.ncbi.nlm.nih.gov/bioproject/{bioproject_id}"
    html = await _fetch_url(url, session=session)
    if html is None:
        return {"bioproject_id": bioproject_id}

    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, Union[str, Dict]] = {"bioproject_id": bioproject_id}

    try:
        result["title"] = soup.select_one("div.Title h2").get_text(strip=True)
    except AttributeError:
        pass

    try:
        result["subtitle"] = soup.select_one("div.Title h3").get_text(strip=True)
    except AttributeError:
        pass

    # Attributes table
    try:
        attrs = {}
        for row in soup.select("#CombinedTable tr"):
            tds = row.find_all("td")
            if len(tds) == 2:
                attrs[tds[0].get_text(strip=True)] = tds[1].get_text(" ", strip=True)
        if attrs:
            result["attributes"] = attrs
    except Exception:
        pass

    await asyncio.sleep(_REQUEST_INTERVAL)
    return result


# ──────────────────────────────────────────────
# PubMed 页面解析
# ──────────────────────────────────────────────

async def fetch_pubmed_abstract(
    pmid: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, str]:
    """爬取 PubMed 摘要

    Returns:
        含 pmid/title/abstract 的字典
    """
    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"
    html = await _fetch_url(url, session=session)
    if html is None:
        return {"pmid": pmid}

    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, str] = {"pmid": pmid}

    # Title
    try:
        result["title"] = soup.select_one("h1.heading-title").get_text(strip=True)
    except AttributeError:
        pass

    # Abstract
    try:
        abstract_div = soup.find("div", class_="abstract-content selected")
        if abstract_div:
            result["abstract"] = abstract_div.get_text(" ", strip=True)
    except AttributeError:
        pass

    await asyncio.sleep(_REQUEST_INTERVAL)
    return result


# ──────────────────────────────────────────────
# 高层接口：补充 GEO 元数据
# ──────────────────────────────────────────────

class NCBIFetcher:
    """NCBI 网页爬取器（Fallback 元数据补充）

    当 Entrez API 返回信息不足时，直接爬取 NCBI 网页来补充元数据。

    Example::

        fetcher = NCBIFetcher()
        geo_data = await fetcher.enrich_geo("GSE110878")
        print(geo_data["summary"])
    """

    def __init__(self, max_concurrency: int = 3):
        self._sem = asyncio.Semaphore(max_concurrency)

    async def enrich_geo(self, gse_id: str) -> Dict[str, str]:
        """补充单个 GEO 数据集的元数据"""
        async with self._sem:
            return await fetch_geo_record(gse_id)

    async def enrich_geo_batch(
        self,
        gse_ids: List[str],
    ) -> Dict[str, Dict[str, str]]:
        """批量补充 GEO 元数据"""
        return await fetch_geo_records(gse_ids, max_concurrency=self._sem._value)

    async def get_biosample(self, biosample_id: str) -> Dict:
        """获取 BioSample 元数据"""
        async with self._sem:
            return await fetch_biosample_record(biosample_id)

    async def get_bioproject(self, bioproject_id: str) -> Dict:
        """获取 BioProject 元数据"""
        async with self._sem:
            return await fetch_bioproject_record(bioproject_id)

    async def get_pubmed_abstract(self, pmid: str) -> Dict[str, str]:
        """获取 PubMed 摘要"""
        async with self._sem:
            return await fetch_pubmed_abstract(pmid)


# ──────────────────────────────────────────────
# 快速测试
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import json

    async def main():
        fetcher = NCBIFetcher()

        print("=== GEO Record ===")
        geo = await fetcher.enrich_geo("GSE110878")
        print(json.dumps(geo, indent=2, ensure_ascii=False))

        print("\n=== PubMed Abstract ===")
        pub = await fetcher.get_pubmed_abstract("34747624")
        print(json.dumps(pub, indent=2, ensure_ascii=False))

    asyncio.run(main())
