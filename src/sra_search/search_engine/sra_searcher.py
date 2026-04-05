"""SRA 数据集检索器

搜索 SRA 数据库，获取 SRP/SRX 编号及元数据。

v0.4 改进（参考 ArcInstitute/SRAgent）：
- 增加 scRNA-seq 专用过滤（Illumina/配对/公开/有数据）
- 支持 organism 过滤（常用名 → 学名 → Entrez Organism 语法）
- 支持日期范围过滤
- 元数据分类整合（LibPrep/Tech10X/CellPrep）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

from sra_search.data.organisms import to_entrez_organism_filter
from sra_search.metadata_extractor.enums import classify_all
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
    srx_ids: list[str] = field(default_factory=list)
    srr_ids: list[str] = field(default_factory=list)
    bioproject_ids: list[str] = field(default_factory=list)
    gse_ids: list[str] = field(default_factory=list)  # 从 study_alias 提取
    study_alias: str = ""
    accession_auth: str = ""  # 用于判断 dbGaP 受控
    # ── 结构化分类字段（v0.4 新增）────────────────
    is_illumina: str = "unsure"
    is_single_cell: str = "unsure"
    lib_prep: str = "unknown"
    tech_10x: str = "not_applicable"
    cell_prep: str = "not_applicable"
    granularity: str = "unknown"
    raw_data: dict = field(default_factory=dict)


def build_scrna_query(
    keyword: str,
    organisms: list[str] | None = None,
    min_date: str | None = None,
    max_date: str | None = None,
    strict_scrna: bool = False,
) -> str:
    """构建 SRA scRNA-seq 专用检索查询

    参考 SRAgent esearch_scrna() 的过滤策略，在 Entrez 层面过滤
    无关数据，显著提升结果精度。

    Args:
        keyword: 用户原始查询词
        organisms: 生物体名称列表（常用名，如 ["human", "mouse"]）
        min_date: 最早发表日期，格式 "YYYY/MM/DD"
        max_date: 最晚发表日期，格式 "YYYY/MM/DD"
        strict_scrna: True 时启用严格 scRNA-seq 过滤（排除 Smart-seq）

    Returns:
        Entrez 查询字符串
    """
    # 1. 核心关键词
    query = keyword

    # 2. 日期范围
    if min_date and max_date:
        query += f" AND ({min_date}:{max_date}[PDAT])"

    # 3. Organism 过滤
    if organisms:
        org_filter = to_entrez_organism_filter(organisms)
        if org_filter:
            query += f" AND {org_filter}"

    # 4. 质量过滤（参考 SRAgent）
    query += ' AND "public"[Access]'
    query += ' AND "has data"[Properties]'
    query += ' AND "platform illumina"[Filter]'

    # 5. 严格 scRNA-seq 模式：排除低通量方法
    if strict_scrna:
        query += ' NOT ("Smart-seq" OR "Smart-seq2" OR "Smart-seq3" OR "MARS-seq" OR "CEL-seq")'

    return query


class SRASearcher:
    """SRA 数据集检索器（v0.4）"""

    def __init__(self, client: EntrezClient | None = None):
        self.client = client or get_entrez_client()

    async def search(
        self,
        term: str,
        retmax: int | None = None,
        mindate: str | None = None,
        maxdate: str | None = None,
        organisms: list[str] | None = None,
        strict_scrna: bool = False,
    ) -> list[str]:
        """搜索 SRA 返回 UID 列表

        Args:
            term: 搜索词
            retmax: 最大返回数
            mindate: 最早日期（YYYY/MM/DD）
            maxdate: 最晚日期（YYYY/MM/DD）
            organisms: 生物体过滤列表
            strict_scrna: 是否启用严格 scRNA-seq 过滤

        Returns:
            SRA UID 列表
        """
        # 是否需要构建增强查询
        if organisms or strict_scrna:
            enhanced_term = build_scrna_query(
                term,
                organisms=organisms,
                min_date=mindate,
                max_date=maxdate,
                strict_scrna=strict_scrna,
            )
            logger.info(f"Searching SRA (enhanced): '{enhanced_term}'")
        else:
            enhanced_term = term
            logger.info(f"Searching SRA: '{term}'")

        result = await self.client.esearch(
            db="sra",
            term=enhanced_term,
            retmax=retmax,
            mindate=mindate if not organisms else None,  # 已合并进 enhanced_term
            maxdate=maxdate if not organisms else None,
            sort="relevance",
        )

        id_list = result.get("esearchresult", {}).get("idlist", [])
        count = result.get("esearchresult", {}).get("count", "0")
        logger.info(f"SRA search: found {count} results, returning {len(id_list)}")
        return id_list  # type: ignore[no-any-return]

    async def fetch_summaries(self, uids: list[str]) -> list[SRAResult]:
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
        retmax: int | None = None,
        organisms: list[str] | None = None,
        strict_scrna: bool = False,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> list[SRAResult]:
        """搜索 + 获取摘要（一步完成）

        Args:
            term: 搜索词
            retmax: 最大返回数
            organisms: 生物体过滤
            strict_scrna: 严格 scRNA-seq 过滤
            min_date: 最早日期
            max_date: 最晚日期
        """
        uids = await self.search(
            term,
            retmax=retmax,
            mindate=min_date,
            maxdate=max_date,
            organisms=organisms,
            strict_scrna=strict_scrna,
        )
        if not uids:
            return []
        results = await self.fetch_summaries(uids)
        # 对结果进行元数据分类
        for r in results:
            self._enrich_metadata(r)
        return results

    def _enrich_metadata(self, result: SRAResult) -> None:
        """用枚举分类器丰富结构化元数据字段（原地修改）"""
        text = " ".join(filter(None, [
            result.title,
            result.library_strategy,
            result.library_source,
            result.library_selection,
            result.instrument,
            result.platform,
        ]))
        if text.strip():
            classified = classify_all(text)
            result.is_illumina = classified.get("is_illumina", "unsure")
            result.is_single_cell = classified.get("is_single_cell", "unsure")
            result.lib_prep = classified.get("lib_prep", "unknown")
            result.tech_10x = classified.get("tech_10x", "not_applicable")
            result.cell_prep = classified.get("cell_prep", "not_applicable")
            result.granularity = classified.get("granularity", "unknown")

    def _parse_sra_xml(self, xml_text: str) -> list[SRAResult]:
        """解析 SRA EFetch XML 响应

        SRA 的 XML 结构比较复杂，需要逐层解析 EXPERIMENT_PACKAGE。
        """
        results = []
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)

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

    def _parse_experiment_package(self, pkg) -> SRAResult | None:
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

            # 样本统计（支持多种 XML 结构）
            sample_count = 0
            run_count = 0
            # SAMPLE 元素可能在多个层级，尝试多种路径
            for tag in ["SAMPLE", "SAMPLE_DESCRIPTOR", "Sample"]:
                for _sample in pkg.iter(tag):
                    sample_count += 1
            # RUN 元素（最可靠的计数）
            for tag in ["RUN", "Run"]:
                for _run in pkg.iter(tag):
                    run_count += 1
            # 如果仍为 0，尝试从 STUDY_SAMPLES 获取
            if sample_count == 0:
                for tag in ["STUDY_SAMPLES", "STUDY_SAMPLE"]:
                    for _ in pkg.iter(tag):
                        sample_count += 1
            sample_count = max(sample_count, run_count)

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
