"""多源检索聚合器

整合 GEO、SRA、PubMed 三个数据源的检索结果，
统一转换为 DatasetRecord 格式。
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from loguru import logger

from sra_search.config import get_settings
from sra_search.metadata_extractor.models import (
    DatasetRecord,
    MatchSource,
)
from sra_search.retriever.geo_api import GeoRetriever
from sra_search.search_engine.pubmed_searcher import PubMedSearcher
from sra_search.search_engine.query_builder import SmartQueryBuilder
from sra_search.search_engine.sra_searcher import SRASearcher


@dataclass
class DatasetSearchResult:
    """检索结果包装器 —— 包含 DatasetRecord 及来源信息"""

    dataset: DatasetRecord
    match_source: str = MatchSource.GEO.value
    match_score: float = 0.0
    matched_keyword: str = ""

    def __repr__(self) -> str:
        return f"<DatasetSearchResult {self.dataset.gse_id} src={self.match_source}>"


class SearchAggregator:
    """多源检索聚合器

    协调 GEO、SRA、PubMed 三个检索器：
    1. 使用 SmartQueryBuilder 扩展查询词
    2. 并发请求各数据源
    3. 合并去重后返回 DatasetRecord 列表
    """

    def __init__(
        self,
        geo_retriever: GeoRetriever | None = None,
        sra_searcher: SRASearcher | None = None,
        pubmed_searcher: PubMedSearcher | None = None,
    ):
        """初始化聚合器

        Args:
            geo_retriever: GEO 检索器（默认使用 GeoRetriever）
            sra_searcher: SRA 检索器（默认使用 SRASearcher）
            pubmed_searcher: PubMed 检索器（默认使用 PubMedSearcher）
        """
        self.geo_retriever = geo_retriever
        self.sra_searcher = sra_searcher or SRASearcher()
        self.pubmed_searcher = pubmed_searcher or PubMedSearcher()
        self.query_builder = SmartQueryBuilder()

    def _build_optimized_query(self, keyword: str) -> str:
        """使用知识图谱构建优化查询"""
        try:
            query, classification = self.query_builder.build_query(keyword)
            return query
        except Exception as e:
            logger.warning(f"Query optimization failed: {e}, using original keyword")
            return keyword

    async def search(
        self,
        keyword: str,
        sources: list[str] | None = None,
        retmax: int | None = None,
        organisms: list[str] | None = None,
        min_date: str | None = None,
        max_date: str | None = None,
        strict_scrna: bool = False,
    ) -> list[DatasetSearchResult]:
        """执行多源检索

        Args:
            keyword: 搜索关键词
            sources: 数据源列表（geo/sra/pubmed/bioproject），None 表示全部
            retmax: 每源最大返回数
            organisms: 生物体过滤列表（常用名，如 ["human", "mouse"]）
            min_date: 最早发表日期（YYYY/MM/DD），仅 SRA/GEO 支持
            max_date: 最晚发表日期（YYYY/MM/DD）
            strict_scrna: 是否启用严格 scRNA-seq 过滤（仅 SRA 源）

        Returns:
            DatasetSearchResult 列表（按 match_score 降序）
        """
        settings = get_settings()
        retmax = retmax or settings.search_retmax

        # 默认全部数据源
        if sources is None:
            sources = ["geo", "sra", "pubmed"]

        # 构建优化查询
        optimized_query = self._build_optimized_query(keyword)
        logger.info(f"Optimized query: '{keyword}' -> '{optimized_query}'")

        # 并发执行各数据源检索
        tasks = []
        source_labels = []

        if "geo" in sources:
            tasks.append(self._search_geo(optimized_query, retmax))
            source_labels.append("geo")

        if "sra" in sources:
            tasks.append(self._search_sra(
                optimized_query, retmax,
                organisms=organisms,
                min_date=min_date,
                max_date=max_date,
                strict_scrna=strict_scrna,
            ))
            source_labels.append("sra")

        if "pubmed" in sources:
            tasks.append(self._search_pubmed(optimized_query, retmax))
            source_labels.append("pubmed")

        if "bioproject" in sources:
            tasks.append(self._search_bioproject(optimized_query, retmax))
            source_labels.append("bioproject")

        if not tasks:
            logger.warning("No valid sources specified")
            return []

        # 并发等待所有任务
        results_list: list[list[DatasetSearchResult] | BaseException] = await asyncio.gather(*tasks, return_exceptions=True)  # type: ignore[assignment]

        # 合并结果（按 gse_id 去重）
        seen_gse_ids: set = set()
        all_results: list[DatasetSearchResult] = []
        source_raw_counts: dict[str, int] = {}
        for i, results in enumerate(results_list):
            source = source_labels[i] if i < len(source_labels) else "unknown"
            if isinstance(results, BaseException):
                logger.error(f"Source {source} failed: {results}")
                continue
            if results:
                source_raw_counts[source] = len(results)  # type: ignore[arg-type]
                for r in results:  # type: ignore[union-attr]
                    # 去重：同一 GSE ID 只保留第一个
                    gse_key = r.dataset.gse_id
                    if gse_key not in seen_gse_ids:
                        seen_gse_ids.add(gse_key)
                        all_results.append(r)

        # 按 score 降序排序
        all_results.sort(key=lambda x: x.match_score, reverse=True)

        # 汇总日志：各源原始命中数 + 合并后数量
        raw_parts = [f"{src}:{cnt}" for src, cnt in source_raw_counts.items()]
        logger.info(f"Source raw hits: [{', '.join(raw_parts)}] → merged: {len(all_results)} unique records")

        return all_results

    async def _search_geo(
        self,
        query: str,
        retmax: int,
    ) -> list[DatasetSearchResult]:
        """搜索 GEO 数据库"""
        try:
            if self.geo_retriever is None:
                self.geo_retriever = GeoRetriever()

            result = await self.geo_retriever.search(query, retmax)

            if not result.is_success():
                logger.warning(f"GEO search failed: {result.error}")
                return []

            # 查询词列表，用于相关性评分
            query_terms = [t.lower() for t in query.split() if len(t) > 2]

            records = []
            for geo_rec in result.records:
                dataset = DatasetRecord(
                    gse_id=geo_rec.gse_id,
                    title=geo_rec.title,
                    organism=geo_rec.organism,
                    platform=geo_rec.platform,
                    sample_count=geo_rec.sample_count,
                    pubmed_ids=[geo_rec.pubmed_id] if geo_rec.pubmed_id else [],
                    bioproject_ids=[geo_rec.bioproject_id] if geo_rec.bioproject_id else [],
                    publication_date=geo_rec.publication_date,
                    abstract=geo_rec.summary,
                    keywords=geo_rec.keywords,
                    has_gse=True,
                )
                dataset.update_hash()

                # 基于标题+摘要对查询词的匹配情况调整分数
                # 确保完全不相关的结果排名靠后
                rec_text = (geo_rec.title + " " + geo_rec.summary).lower()
                matched = sum(1 for t in query_terms if t in rec_text)
                if query_terms:
                    relevance_ratio = matched / len(query_terms)
                    # GEO 直接命中基础分 0.8，按相关性比例加权
                    geo_score = 0.4 + 0.4 * relevance_ratio
                else:
                    geo_score = 0.8

                records.append(DatasetSearchResult(
                    dataset=dataset,
                    match_source=MatchSource.GEO.value,
                    match_score=geo_score,
                    matched_keyword=query,
                ))

            return records

        except Exception as e:
            logger.error(f"GEO search error: {e}")
            return []

    async def _search_sra(
        self,
        query: str,
        retmax: int,
        organisms: list[str] | None = None,
        min_date: str | None = None,
        max_date: str | None = None,
        strict_scrna: bool = False,
    ) -> list[DatasetSearchResult]:
        """搜索 SRA 数据库"""
        try:
            # 确保 geo_retriever 已初始化（用于 GSE 存在性验证）
            if self.geo_retriever is None:
                self.geo_retriever = GeoRetriever()

            results = await self.sra_searcher.search_and_fetch(
                query,
                retmax,
                organisms=organisms,
                strict_scrna=strict_scrna,
                min_date=min_date,
                max_date=max_date,
            )

            # 查询词列表（用于相关性检查）
            query_terms = [t.lower() for t in query.split() if len(t) > 2]

            # ── 第一步：按 GSE ID 聚合同一研究的多个 SRX/SRR ──────────────
            # SRA EFetch 每个 EXPERIMENT_PACKAGE 只含一个 SRX，sample_count 只是
            # 该 package 内的 SAMPLE 数（通常为 1）。需要把同一 GSE 的所有 packages
            # 合并，累加 sample_count，才能得到正确的样本总数。
            #
            # 数据结构：
            #   gse_groups:  gse_id → list[SRAResult]
            #   srp_singles: srp_id → SRAResult  （无 GSE 关联的纯 SRA 记录）
            gse_groups: dict[str, list] = {}
            srp_singles: dict[str, object] = {}

            for sra_rec in results:
                if sra_rec.gse_ids:
                    # 归入第一个 GSE 分组（通常只有一个 GSE）
                    primary_gse = sra_rec.gse_ids[0]
                    if primary_gse not in gse_groups:
                        gse_groups[primary_gse] = []
                    gse_groups[primary_gse].append(sra_rec)
                else:
                    # 无 GSE，以 srp_id 为 key 去重
                    if sra_rec.srp_id not in srp_singles:
                        srp_singles[sra_rec.srp_id] = sra_rec

            records: list[DatasetSearchResult] = []

            # ── 第二步：处理有 GSE 关联的聚合组 ─────────────────────────────
            for gse_id, group in gse_groups.items():
                # SRA study_alias 是自由文本，可能包含虚假 GSE 编号。
                # 必须验证 GSE 是否在 GEO 中真实存在，否则跳过。
                if not await self.geo_retriever.gse_exists(gse_id):
                    logger.debug(
                        f"SRA: gse_id {gse_id} not found in GEO, "
                        f"treating as SRA-only record {group[0].srp_id}"
                    )
                    # 降为纯 SRA 处理（复用 srp_singles 逻辑）
                    if group[0].srp_id not in srp_singles:
                        srp_singles[group[0].srp_id] = group[0]
                    continue

                # 取第一个 record 作为代表（标题、organism、platform 等）
                rep = group[0]

                # 累计 sample_count：
                #   - 收集所有 unique SRX ID（每个 SRX 对应一个样本）
                #   - 若 SRX 为空，退而使用 run_count 之和
                all_srx: set[str] = set()
                total_runs = 0
                for r in group:
                    all_srx.update(r.srx_ids)
                    total_runs += r.run_count

                # SRX 数量是最好的"样本数"代理（每个 SRX = 一个实验/样本）
                agg_sample_count = len(all_srx) if all_srx else max(
                    sum(r.sample_count for r in group),
                    total_runs,
                )

                # 合并 SRP/SRR/BioProject ID 列表
                all_srp_ids = list(dict.fromkeys(r.srp_id for r in group))
                all_srr_ids: list[str] = []
                all_bp_ids: set[str] = set()
                for r in group:
                    all_srr_ids.extend(r.srr_ids)
                    all_bp_ids.update(r.bioproject_ids)

                dataset = DatasetRecord(
                    gse_id=gse_id,
                    title=rep.title,
                    organism=rep.organism,
                    platform=rep.platform,
                    sample_count=agg_sample_count,
                    sra_ids=all_srp_ids,
                    bioproject_ids=list(all_bp_ids),
                    has_gse=True,
                )
                dataset.update_hash()
                records.append(DatasetSearchResult(
                    dataset=dataset,
                    match_source=MatchSource.SRA.value,
                    match_score=0.6,
                    matched_keyword=query,
                ))

            # ── 第三步：处理无 GSE 的纯 SRA 记录 ────────────────────────────
            for srp_id, sra_rec in srp_singles.items():
                # 无 GSE 编号：必须通过相关性检查才加入结果
                title_lower = sra_rec.title.lower()  # type: ignore[union-attr]
                matched = sum(1 for t in query_terms if t in title_lower)
                has_match = (not query_terms) or matched > 0

                if not has_match:
                    logger.debug(f"SRA: skipping unrelated SRA-only record {srp_id}: {sra_rec.title[:60]}")  # type: ignore[union-attr]
                    continue

                dataset = DatasetRecord(
                    gse_id=srp_id,
                    title=sra_rec.title,  # type: ignore[union-attr]
                    organism=sra_rec.organism,  # type: ignore[union-attr]
                    platform=sra_rec.platform,  # type: ignore[union-attr]
                    sample_count=sra_rec.sample_count,  # type: ignore[union-attr]
                    sra_ids=[srp_id],
                    bioproject_ids=sra_rec.bioproject_ids,  # type: ignore[union-attr]
                    has_gse=False,
                )
                dataset.update_hash()
                records.append(DatasetSearchResult(
                    dataset=dataset,
                    match_source=MatchSource.SRA.value,
                    match_score=0.4,
                    matched_keyword=query,
                ))

            return records

        except Exception as e:
            logger.error(f"SRA search error: {e}")
            return []

    async def _search_pubmed(
        self,
        query: str,
        retmax: int,
    ) -> list[DatasetSearchResult]:
        """搜索 PubMed 并关联 GEO 数据集"""
        try:
            pubmed_results, geo_mapping = await self.pubmed_searcher.search_and_fetch(
                query, retmax, link_to_geo=True
            )

            # 查询词列表，用于相关性检查
            query_terms = [t.lower() for t in query.split() if len(t) > 2]

            records = []
            for pub_rec in pubmed_results:
                # 相关性检查：论文标题或摘要必须与查询词相关
                # 避免把无关论文的 GSE 关联也混入结果
                pub_text = (pub_rec.title + " " + pub_rec.abstract).lower()
                matched_terms = sum(1 for t in query_terms if t in pub_text)
                if query_terms and matched_terms == 0:
                    # 论文与查询词完全无关，跳过
                    logger.debug(f"PubMed: skipping unrelated paper PMID={pub_rec.pmid}: {pub_rec.title[:60]}")
                    continue

                # 从标题中提取 GSE 编号（格式为 GSE + 4位以上数字）
                title_gse_ids = re.findall(r"\bGSE\d{4,}\b", pub_rec.title)
                # 从 ELink 关联的 GSE 编号
                # 注意：ELink 返回的可能是 GDS 内部数字 UID（如 "100272217"），需要过滤
                linked_gse_ids = [
                    g for g in pub_rec.gse_ids
                    if g.startswith("GSE")  # 只保留格式正确的 GSE ID
                ]
                # 合并去重
                all_gse_ids = list(dict.fromkeys(title_gse_ids + linked_gse_ids))

                if not all_gse_ids:
                    continue

                for gse_id in all_gse_ids[:5]:  # 最多取5个
                    dataset = DatasetRecord(
                        gse_id=gse_id,
                        title=pub_rec.title,
                        pubmed_ids=[pub_rec.pmid],
                        publication_date=pub_rec.publication_date,
                        journal=pub_rec.journal,
                        abstract=pub_rec.abstract,
                        keywords=pub_rec.keywords,
                        has_gse=True,
                    )
                    dataset.update_hash()
                    records.append(DatasetSearchResult(
                        dataset=dataset,
                        match_source=MatchSource.PUBMED.value,
                        match_score=0.5,
                        matched_keyword=query,
                    ))

            return records

        except Exception as e:
            logger.error(f"PubMed search error: {e}")
            return []

    async def _search_bioproject(
        self,
        query: str,
        retmax: int,
    ) -> list[DatasetSearchResult]:
        """搜索 BioProject 数据库

        策略：通过 SRA 的 bioproject 关联字段来间接搜索
        这里简单实现：复用 SRA 搜索结果中的 bioproject 信息
        """
        # BioProject 本身不适合关键词搜索（是元数据数据库）
        # 实际通过 SRA/PubMed 关联间接获取
        logger.debug("BioProject search: using indirect approach via SRA/PubMed")
        return []
