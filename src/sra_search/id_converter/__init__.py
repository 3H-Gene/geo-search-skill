"""NCBI 编号转换器

支持 GEO / SRA / BioProject / PubMed 之间的 ID 互转：
  GSE → SRP / ERP / DRP
  SRP / ERP / DRP → GSE
  SRP → BioProject / PRJNA / PRJEB
  SRP → BioSample
  SRR → SRX → SRP
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import aiohttp
from loguru import logger

from sra_search.utils.rate_limiter import RateLimiter


@dataclass
class ConversionResult:
    """转换结果"""
    source: str  # 原始编号
    source_type: str  # 源类型
    target_type: str  # 目标类型
    targets: list[str]  # 转换后的编号列表
    note: str = ""


_ACCESSION_PATTERNS = {
    "GSE": re.compile(r"^GSE\d+$", re.IGNORECASE),
    "GSM": re.compile(r"^GSM\d+$", re.IGNORECASE),
    "SRP": re.compile(r"^(SRP|ERP|DRP)\d+$", re.IGNORECASE),
    "SRX": re.compile(r"^SRX\d+$", re.IGNORECASE),
    "SRR": re.compile(r"^SRR\d+$", re.IGNORECASE),
    "PRJNA": re.compile(r"^PRJNA\d+$", re.IGNORECASE),
    "PRJEB": re.compile(r"^PRJEB\d+$", re.IGNORECASE),
    "PRJ": re.compile(r"^PRJ\w+$", re.IGNORECASE),
    "BioSample": re.compile(r"^SAM[DN]\w+$", re.IGNORECASE),
}


def detect_accession_type(accession: str) -> str | None:
    """检测 accession 类型"""
    acc_upper = accession.strip().upper()
    for acc_type, pattern in _ACCESSION_PATTERNS.items():
        if pattern.match(acc_upper):
            return acc_type
    return None


class NCBIConverter:
    """NCBI ID 转换器"""

    ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
    ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

    def __init__(self, rate_limiter: RateLimiter | None = None):
        self.rate_limiter = rate_limiter or RateLimiter(rate=3.0)

    async def convert(
        self,
        accession: str,
        target_db: Literal["sra", "gds", "bioproject", "biosample", "pubmed"],
        session: aiohttp.ClientSession,
    ) -> ConversionResult:
        """将 accession 转换为目标数据库的 ID"""
        acc_type = detect_accession_type(accession)
        if acc_type is None:
            return ConversionResult(
                source=accession,
                source_type="unknown",
                target_type=target_db,
                targets=[],
                note=f"Unknown accession format: {accession}",
            )

        if acc_type == "GSE":
            return await self._gse_to_target(accession, target_db, session)
        elif acc_type in ("SRP", "ERP", "DRP"):
            return await self._srp_to_target(accession, target_db, session)
        elif acc_type in ("SRX", "SRR"):
            return await self._srx_srr_to_target(accession, target_db, session)
        elif acc_type in ("PRJNA", "PRJEB", "PRJ"):
            return await self._prj_to_target(accession, target_db, session)
        else:
            return ConversionResult(
                source=accession,
                source_type=acc_type,
                target_type=target_db,
                targets=[],
                note=f"Conversion from {acc_type} not implemented",
            )

    async def _gse_to_target(
        self,
        gse_id: str,
        target_db: str,
        session: aiohttp.ClientSession,
    ) -> ConversionResult:
        """GSE → SRA / GDS / BioProject / PubMed"""
        result = ConversionResult(
            source=gse_id,
            source_type="GSE",
            target_type=target_db,
            targets=[],
        )

        if target_db == "sra":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "db": "sra",
                    "term": gse_id,
                    "retmode": "json",
                    "retmax": 20,
                }
                async with session.get(
                    self.ESEARCH_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        ids = data.get("esearchresult", {}).get("idlist", [])
                        if ids:
                            result.targets = ids
                            result.note = f"Found {len(ids)} SRA study(ies)"
                        else:
                            result.note = "No SRA studies linked to this GSE"
                    else:
                        result.note = f"ESearch failed: HTTP {resp.status}"
            except Exception as e:
                result.note = f"ESearch error: {e}"

        elif target_db == "gds":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "db": "gds",
                    "term": gse_id,
                    "retmode": "json",
                    "retmax": 10,
                }
                async with session.get(
                    self.ESEARCH_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        ids = data.get("esearchresult", {}).get("idlist", [])
                        if ids:
                            result.targets = [f"GDS{id}" for id in ids[:5]]
                        result.note = "GEO Datasets IDs"
            except Exception as e:
                result.note = f"ESearch error: {e}"

        elif target_db == "bioproject":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "db": "bioproject",
                    "term": gse_id,
                    "retmode": "json",
                    "retmax": 10,
                }
                async with session.get(
                    self.ESEARCH_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        ids = data.get("esearchresult", {}).get("idlist", [])
                        if ids:
                            result.targets = ids
                        result.note = f"Found {len(ids)} BioProject(s)"
            except Exception as e:
                result.note = f"ESearch error: {e}"

        elif target_db == "pubmed":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "dbfrom": "gds",
                    "db": "pubmed",
                    "id": gse_id,
                    "retmode": "json",
                }
                async with session.get(
                    self.ELINK_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        links = data.get("linksets", [{}])
                        if links:
                            result.targets = [str(rid) for rid in links[0].get("linkids", [])[:10]]
                        result.note = "Linked PubMed articles"
            except Exception as e:
                result.note = f"ELink error: {e}"

        return result

    async def _srp_to_target(
        self,
        srp_id: str,
        target_db: str,
        session: aiohttp.ClientSession,
    ) -> ConversionResult:
        """SRP → GDS / BioProject / BioSample"""
        result = ConversionResult(
            source=srp_id,
            source_type="SRP",
            target_type=target_db,
            targets=[],
        )

        if target_db == "gds":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "dbfrom": "sra",
                    "db": "gds",
                    "id": srp_id,
                    "retmode": "json",
                }
                async with session.get(
                    self.ELINK_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        links = data.get("linksets", [{}])
                        if links:
                            gds_ids = [str(rid) for rid in links[0].get("linkids", [])[:20]]
                            if gds_ids:
                                result.targets = gds_ids
                                result.note = f"Found {len(gds_ids)} GDS record(s)"
            except Exception as e:
                result.note = f"ELink error: {e}"

        elif target_db == "bioproject":
            result.targets = [srp_id]
            result.note = "SRA Study IS a BioProject"

        elif target_db == "biosample":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "dbfrom": "sra",
                    "db": "biosample",
                    "id": srp_id,
                    "retmode": "json",
                }
                async with session.get(
                    self.ELINK_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        links = data.get("linksets", [{}])
                        if links:
                            result.targets = [str(rid) for rid in links[0].get("linkids", [])[:20]]
                            result.note = f"Found {len(result.targets)} BioSample(s)"
            except Exception as e:
                result.note = f"ELink error: {e}"

        return result

    async def _srx_srr_to_target(
        self,
        acc_id: str,
        target_db: str,
        session: aiohttp.ClientSession,
    ) -> ConversionResult:
        """SRR/SRX → SRP"""
        result = ConversionResult(
            source=acc_id,
            source_type=acc_id[:3].upper(),
            target_type="sra",
            targets=[],
        )

        if target_db != "sra":
            return result

        await self.rate_limiter.acquire_async()
        try:
            params = {
                "db": "sra",
                "term": acc_id,
                "retmode": "json",
                "retmax": 5,
            }
            async with session.get(
                self.ESEARCH_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    ids = data.get("esearchresult", {}).get("idlist", [])
                    if ids:
                        result.targets = ids
                        result.note = f"Found {len(ids)} parent SRA Study(ies)"
                    else:
                        result.note = "No parent SRA Study found"
        except Exception as e:
            result.note = f"ESearch error: {e}"

        return result

    async def _prj_to_target(
        self,
        prj_id: str,
        target_db: str,
        session: aiohttp.ClientSession,
    ) -> ConversionResult:
        """PRJNA/PRJEB → SRP / BioSample"""
        result = ConversionResult(
            source=prj_id,
            source_type="BioProject",
            target_type=target_db,
            targets=[],
        )

        if target_db == "sra":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "db": "sra",
                    "term": prj_id,
                    "retmode": "json",
                    "retmax": 10,
                }
                async with session.get(
                    self.ESEARCH_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        ids = data.get("esearchresult", {}).get("idlist", [])
                        if ids:
                            result.targets = ids
                            result.note = f"Found {len(ids)} SRA Study(ies)"
            except Exception as e:
                result.note = f"ESearch error: {e}"

        elif target_db == "biosample":
            await self.rate_limiter.acquire_async()
            try:
                params = {
                    "dbfrom": "bioproject",
                    "db": "biosample",
                    "id": prj_id,
                    "retmode": "json",
                }
                async with session.get(
                    self.ELINK_URL, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        links = data.get("linksets", [{}])
                        if links:
                            result.targets = [str(rid) for rid in links[0].get("linkids", [])[:20]]
                            result.note = f"Found {len(result.targets)} BioSample(s)"
            except Exception as e:
                result.note = f"ELink error: {e}"

        return result
