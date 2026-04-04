"""PubMed 文献检索器

搜索 PubMed 数据库，获取论文及其关联的 GSE 数据集。
检索路径：关键词 → ESearch(pubmed) → 提取 PMID → ELink(pubmed→gds) → GSE 编号
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from sra_search.search_engine.base import EntrezClient, get_entrez_client


@dataclass
class PubMedResult:
    """PubMed 检索结果"""
    pmid: str
    title: str = ""
    abstract: str = ""
    journal: str = ""
    publication_date: str = ""
    authors: list[str] = field(default_factory=list)
    doi: str = ""
    keywords: list[str] = field(default_factory=list)
    # 关联的 GSE 编号
    gse_ids: list[str] = field(default_factory=list)
    # 原始 JSON 数据
    raw_data: dict = field(default_factory=dict)


class PubMedSearcher:
    """PubMed 文献检索器"""

    def __init__(self, client: EntrezClient | None = None):
        self.client = client or get_entrez_client()

    async def search(
        self,
        term: str,
        retmax: int | None = None,
        mindate: str | None = None,
        maxdate: str | None = None,
    ) -> list[str]:
        """搜索 PubMed 返回 PMID 列表

        Args:
            term: 搜索词
            retmax: 最大返回数
            mindate: 起始日期 (YYYY/MM/DD)
            maxdate: 结束日期

        Returns:
            PMID 列表
        """
        logger.info(f"Searching PubMed: '{term}'")
        result = await self.client.esearch(
            db="pubmed",
            term=term,
            retmax=retmax,
            mindate=mindate,
            maxdate=maxdate,
            sort="relevance",
        )

        id_list = result.get("esearchresult", {}).get("idlist", [])
        count = result.get("esearchresult", {}).get("count", "0")
        logger.info(f"PubMed search '{term}': found {count} results, returning {len(id_list)}")
        return id_list

    async def fetch_summaries(self, pmids: list[str]) -> list[PubMedResult]:
        """批量获取 PubMed 文献摘要

        Args:
            pmids: PMID 列表

        Returns:
            PubMedResult 列表
        """
        if not pmids:
            return []

        # 使用 ESummary 获取摘要（比 EFetch 更可靠）
        result = await self.client.esummary(db="pubmed", ids=pmids)

        pubmed_results = []
        if not isinstance(result, dict):
            return []

        result_data = result.get("result", {})
        uids = result_data.get("uids", [])

        for uid in uids:
            if uid == "uids":
                continue
            data = result_data.get(uid, {})
            if not data:
                continue

            # 提取作者
            authors = []
            for author in data.get("authors", []):
                authors.append(author.get("name", ""))

            # 提取关键词 (ESummary 不包含关键词，从 attributes 推断)
            keywords = []
            if data.get("attributes"):
                keywords = data.get("attributes", [])

            # 提取 GSE 编号（从 title 或 journal 中）
            import re
            text = data.get("title", "") + " " + data.get("fulljournalname", "")
            gse_ids = list(set(re.findall(r"\bGSE\d{4,}\b", text)))

            pubmed_results.append(PubMedResult(
                pmid=uid,
                title=data.get("title", ""),
                abstract=data.get("title", ""),  # ESummary 不含 abstract
                journal=data.get("fulljournalname", ""),
                publication_date=data.get("pubdate", ""),
                authors=authors,
                doi=data.get("elocationid", ""),
                keywords=keywords,
                gse_ids=gse_ids,
                raw_data=data,
            ))

        return pubmed_results

    async def link_to_geo(self, pmids: list[str]) -> dict[str, list[str]]:
        """通过 ELink 从 PubMed 关联到 GEO 数据集

        Args:
            pmids: PMID 列表

        Returns:
            {pmid: [gse_ids]} 的映射
        """
        if not pmids:
            return {}

        logger.info(f"Linking {len(pmids)} PubMed IDs to GEO")
        mapping: dict[str, list[str]] = {}

        # 分批处理（ELink 单次最多支持约 200 个 ID）
        batch_size = 200
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i:i + batch_size]
            try:
                result = await self.client.elink(
                    dbfrom="pubmed",
                    db="gds",
                    ids=batch,
                )
                # 解析 ELink 结果
                linksets = result.get("linksets", [])
                for idx, linkset in enumerate(linksets):
                    if idx >= len(batch):
                        break
                    pmid = batch[idx]
                    gse_ids = []
                    linkset_data = linkset.get("linksetdbs", [])
                    for db_links in linkset_data:
                        if db_links.get("dbto") == "gds":
                            for link in db_links.get("links", []):
                                gse_ids.append(link)
                    if gse_ids:
                        mapping[pmid] = gse_ids
            except Exception as e:
                logger.warning(f"ELink pubmed→gds failed for batch {i}: {e}")

        logger.info(f"ELink pubmed→gds: {len(mapping)} PubMed entries linked to GEO")
        return mapping

    async def search_and_fetch(
        self,
        term: str,
        retmax: int | None = None,
        link_to_geo: bool = True,
    ) -> tuple[list[PubMedResult], dict[str, list[str]]]:
        """搜索 + 获取摘要 + 关联 GEO（一步完成）

        Returns:
            (pubmed_results, pmid_to_gse_mapping)
        """
        pmids = await self.search(term, retmax)
        if not pmids:
            return [], {}

        results = await self.fetch_summaries(pmids)

        geo_mapping: dict[str, list[str]] = {}
        if link_to_geo:
            geo_mapping = await self.link_to_geo(pmids)
            # 将关联的 GSE 写入对应结果
            for pr in results:
                if pr.pmid in geo_mapping:
                    pr.gse_ids = list(set(pr.gse_ids + geo_mapping[pr.pmid]))

        return results, geo_mapping
