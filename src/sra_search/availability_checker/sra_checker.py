"""SRA 数据集可用性检测

通过 EFetch 查询 SRA metadata 检测数据集状态，
支持 dbGaP 受控访问检测和样本数过滤。
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

from loguru import logger

from sra_search.utils.rate_limiter import RateLimiter


@dataclass
class SraCheckResult:
    """SRA 可用性检查结果"""
    accession: str  # SRP/SRX/SRR 编号
    status: str  # available / unavailable / restricted / unverified
    access_type: str  # public / controlled / unknown
    note: str = ""
    sample_count: int = 0
    study_status: str = ""  # public / withdrawn / suppressed
    has_data: bool = False


class SraChecker:
    """SRA 数据集可用性检查器"""

    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    # dbGaP 受控访问关键词
    _DBGA_KEYWORDS = [
        "dbgap", "controlled", "phs",
        "access required", "authorization required",
        "db gap", "database of genotypes and phenotypes",
    ]

    def __init__(self, rate_limiter: Optional[RateLimiter] = None):
        self.rate_limiter = rate_limiter or RateLimiter()

    async def check_srp(
        self,
        srp_id: str,
        session: aiohttp.ClientSession,
        min_samples: int = 3,
    ) -> SraCheckResult:
        """检查 SRA Study (SRP) 的可用性

        Args:
            srp_id: SRA Study 编号
            session: aiohttp 会话
            min_samples: 最小有效样本数阈值

        Returns:
            SraCheckResult
        """
        result = SraCheckResult(
            accession=srp_id,
            status="unverified",
            access_type="unknown",
        )

        # 使用 ESummary 查询 SRA study
        params = {
            "db": "sra",
            "id": srp_id,
            "retmode": "json",
        }

        await self.rate_limiter.acquire_async()
        try:
            async with session.get(
                self.ESUMMARY_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    result.status = "unverified"
                    result.note = f"SRA ESummary HTTP {resp.status}"
                    return result

                data = await resp.json(content_type=None)
                result = self._parse_esummary(srp_id, data, min_samples)
        except Exception as e:
            logger.warning(f"Failed to check SRA {srp_id}: {e}")
            result.note = f"SRA check failed: {e}"

        return result

    def _parse_esummary(
        self,
        srp_id: str,
        data: dict,
        min_samples: int,
    ) -> SraCheckResult:
        """解析 ESummary 返回的 SRA 数据"""
        result = SraCheckResult(
            accession=srp_id,
            status="unverified",
            access_type="unknown",
        )

        try:
            result_set = data.get("result", {})
            uids = result_set.get("uids", [])
            if not uids:
                result.status = "unavailable"
                result.note = "SRA study not found"
                return result

            # 取第一个 UID 的数据
            for uid in uids:
                exp = result_set.get(uid, {})
                if not exp:
                    continue

                # 检查 study 状态
                runs_info = exp.get("runs", "")
                study_title = exp.get("title", "")
                study_abstract = exp.get("abstract", "") or ""

                # 解析 accession_auth 和 consent
                acc_auth = exp.get("access", "")
                consent = exp.get("consent", "")
                study_type = exp.get("study_type", "") or ""

                # 检测受控访问
                combined_text = f"{acc_auth} {consent} {study_type} {study_title} {study_abstract}".lower()
                if any(kw in combined_text for kw in self._DBGA_KEYWORDS):
                    result.access_type = "controlled"
                    result.status = "restricted"
                    result.note = "dbGaP controlled access data"
                    return result

                result.access_type = "public"

                # 检查是否有实际数据
                if runs_info:
                    # runs 格式: "SRR123 SRR456" 或类似
                    run_ids = runs_info.split()
                    result.sample_count = len(run_ids)
                    result.has_data = len(run_ids) > 0

                    if result.sample_count == 0:
                        result.status = "unavailable"
                        result.note = "No SRA runs found"
                    elif result.sample_count < min_samples:
                        result.status = "available"
                        result.note = f"Low sample count ({result.sample_count} < {min_samples})"
                    else:
                        result.status = "available"
                        result.note = ""
                else:
                    # 尝试从 expxml 解析
                    exp_xml = exp.get("expxml", "")
                    if exp_xml:
                        result = self._parse_exp_xml(srp_id, exp_xml, min_samples, result)
                    else:
                        result.status = "available"
                        result.note = "Study found, could not verify run data"

                break

        except Exception as e:
            logger.warning(f"Error parsing SRA summary for {srp_id}: {e}")
            result.note = f"Parse error: {e}"

        return result

    def _parse_exp_xml(
        self,
        srp_id: str,
        xml_str: str,
        min_samples: int,
        result: SraCheckResult,
    ) -> SraCheckResult:
        """解析 SRA Experiment XML 获取运行信息"""
        try:
            root = ET.fromstring(xml_str)
            # 查找 RUN 元素
            runs = root.findall(".//RUN")
            if runs:
                result.sample_count = len(runs)
                result.has_data = True
                if result.sample_count < min_samples:
                    result.status = "available"
                    result.note = f"Low sample count ({result.sample_count} < {min_samples})"
                else:
                    result.status = "available"
                    result.note = ""
            else:
                result.status = "unavailable"
                result.note = "No RUN elements in experiment XML"
        except ET.ParseError:
            result.status = "available"
            result.note = "Could not parse experiment XML"

        return result

    async def check_srp_with_efetch(
        self,
        srp_id: str,
        session: aiohttp.ClientSession,
        min_samples: int = 3,
    ) -> SraCheckResult:
        """通过 EFetch XML 检查 SRA Study（备用方案）

        当 ESummary 不足时使用更详细的 EFetch XML 解析。
        """
        result = SraCheckResult(
            accession=srp_id,
            status="unverified",
            access_type="unknown",
        )

        params = {
            "db": "sra",
            "id": srp_id,
            "rettype": "full",
            "retmode": "xml",
        }

        await self.rate_limiter.acquire_async()
        try:
            async with session.get(
                self.EFETCH_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return result

                xml_str = await resp.text()
                result = self._parse_efetch_xml(srp_id, xml_str, min_samples)

        except Exception as e:
            logger.warning(f"EFetch check failed for {srp_id}: {e}")
            result.note = f"EFetch failed: {e}"

        return result

    def _parse_efetch_xml(
        self,
        srp_id: str,
        xml_str: str,
        min_samples: int,
    ) -> SraCheckResult:
        """解析 EFetch XML 获取详细 SRA metadata"""
        result = SraCheckResult(
            accession=srp_id,
            status="unverified",
            access_type="unknown",
        )

        try:
            root = ET.fromstring(xml_str)
            studies = root.findall(".//STUDY")
            if not studies:
                result.status = "unavailable"
                result.note = "No STUDY element found"
                return result

            study = studies[0]
            study_attr = study.attrib
            status = study_attr.get("status", "").lower()

            if status in ("withdrawn", "suppressed", "replaced"):
                result.status = "unavailable"
                result.note = f"Study status: {status}"
                result.study_status = status
                return result

            result.study_status = status or "public"

            # 检查 dbGaP 权限
            # 公开 SRA 不含 dbGaP 字段
            # 受控数据通常通过 dbGaP SUBMISSION_PACKAGE 指示
            study_desc = ET.tostring(study, encoding="unicode", method="text").lower()
            if any(kw in study_desc for kw in self._DBGA_KEYWORDS):
                result.access_type = "controlled"
                result.status = "restricted"
                result.note = "dbGaP controlled access detected"
                return result

            result.access_type = "public"

            # 统计样本数
            # 统计 SRR (RUN) 数量
            runs = root.findall(".//RUN")
            result.sample_count = len(runs)
            result.has_data = len(runs) > 0

            if result.sample_count == 0:
                result.status = "unavailable"
                result.note = "No RUN data found"
            elif result.sample_count < min_samples:
                result.status = "available"
                result.note = f"Low sample count ({result.sample_count} < {min_samples})"
            else:
                result.status = "available"

            # 检查 consent 字段
            consent_el = root.find(".//CONSENT")
            if consent_el is not None:
                consent_text = (consent_el.text or "").lower()
                if "controlled" in consent_text or "dbgap" in consent_text:
                    result.access_type = "controlled"
                    result.status = "restricted"
                    result.note = "Controlled access per consent"

        except ET.ParseError as e:
            logger.warning(f"Failed to parse SRA XML for {srp_id}: {e}")
            result.note = f"XML parse error: {e}"

        return result
