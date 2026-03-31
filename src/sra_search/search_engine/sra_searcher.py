"""SRA 数据集检索器

搜索 SRA 数据库，获取 SRP/SRX 编号及元数据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from sra_search.search_engine.base import EntrezClient, get_entrez_client


@dataclass
class SRAResult:
    """SRA 检索结果"""
    srp_id: str  # SRA Study ID (SRP/SRPX/ERP/DRP)
    title: str = ""
    organism: str = ""
    instrument: str = ""
    library_strategy: str = ""
    library_source: str = ""
    library_selection: str = ""
    sample_count: int = 0
    run_count: int = 0
    platform: str = ""
    srx_ids: List[str] = field(default_factory=list)
    srr_ids: List[str] = field(default_factory=list)
    bioproject_ids: List[str] = field(default_factory=list)
    gse_ids: List[str] = field(default_factory=list)  # 从 study_alias 提取
    study_alias: str = ""
    accession_auth: str = ""  # 用于判断 dbGaP 受控
    raw_data: dict = field(default_factory=dict)


class SRASearcher:
    """SRA 数据集检索器"""

    def __init__(self, client: Optional[EntrezClient] = None):
        self.client = client or get_entrez_client()

    async def search(
        self,
        term: str,
        retmax: Optional[int] = None,
        mindate: Optional[str] = None,
        maxdate: Optional[str] = None,
    ) -> List[str]:
        """搜索 SRA 返回 UID 列表

        Args:
            term: 搜索词
            retmax: 最大返回数

        Returns:
            SRA UID 列表
        """
        logger.info(f"Searching SRA: '{term}'")
        result = await self.client.esearch(
            db="sra",
            term=term,
            retmax=retmax,
            mindate=mindate,
            maxdate=maxdate,
            sort="relevance",
        )

        id_list = result.get("esearchresult", {}).get("idlist", [])
        count = result.get("esearchresult", {}).get("count", "0")
        logger.info(f"SRA search '{term}': found {count} results, returning {len(id_list)}")
        return id_list

    async def fetch_summaries(self, uids: List[str]) -> List[SRAResult]:
        """批量获取 SRA 数据集摘要

        Args:
            uids: SRA UID 列表

        Returns:
            SRAResult 列表
        """
        if not uids:
            return []

        # SRA 使用 XML 格式的 EFetch
        results = await self.client.batch_efetch(db="sra", ids=uids, retmode="xml")

        sra_results = []
        for result_xml in results:
            if not isinstance(result_xml, str):
                continue
            parsed = self._parse_sra_xml(result_xml)
            sra_results.extend(parsed)

        return sra_results

    async def search_and_fetch(
        self,
        term: str,
        retmax: Optional[int] = None,
    ) -> List[SRAResult]:
        """搜索 + 获取摘要（一步完成）"""
        uids = await self.search(term, retmax)
        if not uids:
            return []
        return await self.fetch_summaries(uids)

    def _parse_sra_xml(self, xml_text: str) -> List[SRAResult]:
        """解析 SRA EFetch XML 响应

        SRA 的 XML 结构比较复杂，需要逐层解析 EXPERIMENT_PACKAGE。
        """
        results = []
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            # SRA XML namespace
            ns = {
                "sra": "http://www.ncbi.nlm.nih.gov/TraceDb/sra/SRA.xsd",
                "xsi": "http://www.w3.org/2001/XMLSchema-instance",
            }

            # 尝试无 namespace 解析
            for pkg in root.iter("EXPERIMENT_PACKAGE"):
                result = self._parse_experiment_package(pkg)
                if result:
                    results.append(result)

            # 也尝试带 namespace
            if not results:
                for pkg in root.iter():
                    if "EXPERIMENT_PACKAGE" in pkg.tag:
                        result = self._parse_experiment_package(pkg)
                        if result:
                            results.append(result)

        except ET.ParseError as e:
            logger.error(f"Failed to parse SRA XML: {e}")

        return results

    def _parse_experiment_package(self, pkg) -> Optional[SRAResult]:
        """解析单个 EXPERIMENT_PACKAGE"""
        try:
            # STUDY
            study = pkg.find(".//STUDY")
            if study is None:
                return None

            srp_id = study.get("accession", "")
            if not srp_id:
                return None

            # Study alias (可能包含 GSE 编号)
            study_alias = study.get("alias", "")
            gse_ids = re.findall(r"\bGSE\d{4,}\b", study_alias)

            # Title
            title_elem = study.find(".//STUDY_TITLE")
            title = title_elem.text if title_elem is not None and title_elem.text else ""

            # BioProject
            bioproject_ids = []
            for bp_id_elem in study.findall(".//STUDY_LINK/URL_LINK/LABEL"):
                if bp_id_elem.text and "BioProject" in (bp_id_elem.text):
                    pass
            for id_elem in study.findall(".//EXTERNAL_ID"):
                namespace = id_elem.get("namespace", "")
                if namespace in ("BioProject", "bioproject", "BioSample"):
                    bioproject_ids.append(id_elem.text or "")

            # Accession auth (dbGaP 检测)
            accession_auth = ""
            # SRA metadata 中有些 study 会包含 consent 或 accession_auth
            # 这些信息可能在 package level 或 study level

            # 样本统计
            sample_count = 0
            run_count = 0
            for sample in pkg.findall(".//SAMPLE"):
                sample_count += 1
            for run in pkg.findall(".//RUN"):
                run_count += 1

            # Organism
            organism = ""
            org_elem = study.find(".//SCIENTIFIC_NAME")
            if org_elem is not None and org_elem.text:
                organism = org_elem.text

            # Experiment 信息
            library_strategy = ""
            library_source = ""
            library_selection = ""
            instrument = ""
            platform = ""

            for exp in pkg.findall(".//EXPERIMENT"):
                for lib in exp.findall(".//LIBRARY_DESCRIPTOR"):
                    ls = lib.find("LIBRARY_STRATEGY")
                    if ls is not None and ls.text:
                        library_strategy = ls.text
                    lso = lib.find("LIBRARY_SOURCE")
                    if lso is not None and lso.text:
                        library_source = lso.text
                    lse = lib.find("LIBRARY_SELECTION")
                    if lse is not None and lse.text:
                        library_selection = lse.text

                for inst in exp.findall(".//INSTRUMENT_MODEL"):
                    if inst.text:
                        instrument = inst.text

                for plat in exp.findall(".//PLATFORM"):
                    for child in plat:
                        platform = child.text if child.text else ""

            # SRX IDs
            srx_ids = []
            for exp in pkg.findall(".//EXPERIMENT"):
                srx = exp.get("accession", "")
                if srx:
                    srx_ids.append(srx)

            # SRR IDs
            srr_ids = []
            for run in pkg.findall(".//RUN"):
                srr = run.get("accession", "")
                if srr:
                    srr_ids.append(srr)

            return SRAResult(
                srp_id=srp_id,
                title=title,
                organism=organism,
                instrument=instrument,
                library_strategy=library_strategy,
                library_source=library_source,
                library_selection=library_selection,
                sample_count=max(sample_count, run_count),
                run_count=run_count,
                platform=platform,
                srx_ids=srx_ids,
                srr_ids=srr_ids,
                bioproject_ids=list(set(bioproject_ids)),
                gse_ids=gse_ids,
                study_alias=study_alias,
                accession_auth=accession_auth,
            )

        except Exception as e:
            logger.warning(f"Failed to parse EXPERIMENT_PACKAGE: {e}")
            return None
