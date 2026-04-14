"""Schema 转换器

将 DatasetRecord 转换为标准 Schema (DatasetSchema)，
支持 perturbation 检测、single-cell 识别、排序评分计算。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from sra_search.metadata_extractor.models import DatasetRecord, OmicsGranularity
from sra_search.schema import (
    DatasetSchema,
    DataType,
    GranularityType,
    SearchResultSchema,
)

if TYPE_CHECKING:
    from sra_search.llm.client import LLMClient


# ── 关键词库（从 keywords.json 加载，支持运行时覆盖）──────────────────────────

# 默认硬编码值（确保 keywords.json 不存在时仍能工作）
_DEFAULT_DISEASE_TERMS: list[str] = [
    # 多词短语（长词优先）
    "monosodium urate", "uric acid", "gouty arthritis",
    # 单关键词
    "hyperuricemia", "hyperuricemic", "gout", "gouty",
    "msu", "tophus", "tophi", "podagra", "urate", "uric",
]

_DEFAULT_SC_METHOD_TERMS: list[str] = [
    # 多词短语
    "single-cell rna", "single cell rna", "single-cell transcriptome",
    "single-cell", "single cell",
    # 单关键词（按长度降序）
    "snrnaseq", "scrnaseq", "singlecell", "single-nucleus",
    "scseq", "snrna", "scrna", "scRNA-seq", "scRNAseq",
    "cellranger", "multiome", "cite-seq", "10x", "nuclei",
]

# 模块级运行时覆盖（可通过 set_keywords() 修改）
_disease_terms: list[str] = _DEFAULT_DISEASE_TERMS
_sc_method_terms: list[str] = _DEFAULT_SC_METHOD_TERMS
_keywords_loaded = False


def _load_keywords() -> None:
    """从 keywords.json 懒加载关键词库（仅首次调用时加载）"""
    global _disease_terms, _sc_method_terms, _keywords_loaded
    if _keywords_loaded:
        return

    try:
        # 查找 keywords.json（与 data/ontologies/ 同级的 data/ 目录）
        candidates = [
            Path(__file__).resolve().parent / "data" / "keywords.json",
            Path(__file__).resolve().parent.parent.parent / "data" / "keywords.json",
            Path.cwd() / "data" / "keywords.json",
        ]
        for path in candidates:
            if path.is_file():
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)

                disease_data = data.get("disease_terms", {})
                if isinstance(disease_data, dict):
                    _disease_terms = disease_data.get("terms", _DEFAULT_DISEASE_TERMS)
                elif isinstance(disease_data, list):
                    _disease_terms = disease_data

                sc_data = data.get("sc_method_terms", {})
                if isinstance(sc_data, dict):
                    _sc_method_terms = sc_data.get("terms", _DEFAULT_SC_METHOD_TERMS)
                elif isinstance(sc_data, list):
                    _sc_method_terms = sc_data

                _keywords_loaded = True
                logger.info(
                    f"[converter] Loaded keywords from {path.name}: "
                    f"{len(_disease_terms)} disease terms, "
                    f"{len(_sc_method_terms)} sc_method terms"
                )
                return
        logger.debug("[converter] keywords.json not found, using defaults")
    except Exception as e:
        logger.warning(f"[converter] Failed to load keywords.json: {e}, using defaults")

    _keywords_loaded = True


def get_disease_terms() -> list[str]:
    """获取疾病关键词列表（懒加载）"""
    _load_keywords()
    return _disease_terms


def get_sc_method_terms() -> list[str]:
    """获取单细胞技术关键词列表（懒加载）"""
    _load_keywords()
    return _sc_method_terms


def set_keywords(
    disease_terms: list[str] | None = None,
    sc_method_terms: list[str] | None = None,
) -> None:
    """运行时覆盖关键词库（优先级高于 keywords.json）

    用于在测试或动态配置场景下注入自定义关键词。

    Args:
        disease_terms: 新的疾病关键词列表，None 表示保持当前值
        sc_method_terms: 新的单细胞技术关键词列表，None 表示保持当前值
    """
    global _disease_terms, _sc_method_terms
    if disease_terms is not None:
        _disease_terms = disease_terms
        logger.info(f"[converter] Disease terms overridden with {len(disease_terms)} terms")
    if sc_method_terms is not None:
        _sc_method_terms = sc_method_terms
        logger.info(f"[converter] SC method terms overridden with {len(sc_method_terms)} terms")


# 触发懒加载（converter.py 被 import 时自动加载）
_load_keywords()

# perturbation 检测关键词（仅保留明确指示干预实验的词，避免误报）
PERTURBATION_KEYWORDS: dict[str, list[str]] = {
    "CRISPR": ["crispr", "cas9", "sgrna", "guide rna", "gene editing", "genome editing", "crispr-cas"],
    "KNOCKOUT": ["knockout", " ko ", "knock-out", "null mutation", "gene deletion", "gene disruption"],
    "KNOCKDOWN": ["knockdown", "knock-down", " rnai", "rna interference", "sirna", "shrna"],
    "DRUG": ["drug treatment", "drug exposure", "inhibitor treatment", "compound treatment",
             "pharmacological", "chemotherapy", "treated with", "dose response"],
    "OVEREXPRESSION": ["overexpression", "overexpress", "over-expression", "transgenic", "ectopic expression"],
    "SIRNA": ["sirna", "shrna", "mirna mimic", "antisense oligonucleotide"],
    "CHEMICAL": ["chemical exposure", "toxic", "pollutant exposure", "oxidative stress", "genotoxic"],
    "RADIATION": ["radiation", "irradiation", "gamma irradiation", "uv irradiation", "x-ray irradiation"],
    "STIMULATION": ["cytokine stimulation", "lps stimulation", "tcr stimulation", "bcr stimulation",
                    "growth factor stimulation", "ifn stimulation", "tnf stimulation"],
}


def detect_perturbation(text: str) -> list[str]:
    """从文本中检测 perturbation 类型

    Args:
        text: 标题、摘要等文本内容

    Returns:
        检测到的 perturbation 类型列表
    """
    if not text:
        return []

    text_lower = text.lower()
    detected: list[str] = []

    for ptype, keywords in PERTURBATION_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                if ptype not in detected:
                    detected.append(ptype)
                break

    return detected


def infer_data_type(omics_type: str, title: str = "", platform: str = "") -> str:
    """推断数据类型

    Args:
        omics_type: 组学类型字符串
        title: 数据集标题
        platform: 平台信息

    Returns:
        标准化的数据类型
    """
    # 从 omics_type 推断
    omics_lower = (omics_type or "").lower()
    if "scrna" in omics_lower or "single-cell rna" in omics_lower:
        return DataType.scRNA_SEQ.value
    if "rna-seq" in omics_lower or "rna seq" in omics_lower:
        return DataType.RNA_SEQ.value
    if "microarray" in omics_lower:
        return DataType.microarray.value
    if "atac" in omics_lower:
        return DataType.ATAC_SEQ.value
    if "chip" in omics_lower:
        return DataType.CHIP_SEQ.value
    if "scatac" in omics_lower:
        return DataType.scATAC_SEQ.value
    if "spatial" in omics_lower:
        return DataType.SPATIAL.value
    if "proteomics" in omics_lower or "mass spec" in omics_lower:
        return DataType.PROTEOMICS.value
    if "metagenomics" in omics_lower or "16s" in omics_lower:
        return DataType.METAGENOMICS.value

    # 从标题推断
    title_lower = (title or "").lower()
    if any(kw in title_lower for kw in ["scrna", "single-cell", "single cell", "10x ", "cellranger"]):
        return DataType.scRNA_SEQ.value
    if any(kw in title_lower for kw in ["rna-seq", "rna seq", "transcriptome"]):
        return DataType.RNA_SEQ.value
    if any(kw in title_lower for kw in ["microarray", "gene expression array"]):
        return DataType.microarray.value

    # 从平台推断
    platform_lower = (platform or "").lower()
    if "10x" in platform_lower or "chromium" in platform_lower:
        return DataType.scRNA_SEQ.value

    return DataType.OTHER.value


def infer_granularity(
    omics_granularity: str,
    sample_count: int = 0,
    title: str = "",
    platform: str = "",
) -> str:
    """推断数据粒度

    Args:
        omics_granularity: 原始粒度字段
        sample_count: 样本数量
        title: 标题
        platform: 平台

    Returns:
        标准化的粒度类型
    """
    # 直接映射
    if omics_granularity == OmicsGranularity.SINGLE_CELL.value:
        return GranularityType.SINGLE_CELL.value
    if omics_granularity == OmicsGranularity.BULK.value:
        return GranularityType.BULK.value
    if omics_granularity == OmicsGranularity.SPATIAL.value:
        return GranularityType.SPATIAL.value

    # 标题关键词
    title_lower = (title or "").lower()
    if any(kw in title_lower for kw in ["single-cell", "single cell", "scrna", "scRNA-seq", "10x"]):
        return GranularityType.SINGLE_CELL.value
    if any(kw in title_lower for kw in ["spatial", "visium", "merfish", "xenium", "spatial transcriptomics"]):
        return GranularityType.SPATIAL.value

    # 平台关键词
    platform_lower = (platform or "").lower()
    if any(kw in platform_lower for kw in ["10x", "chromium", "drop-seq", "smart-seq", "cel-seq"]):
        return GranularityType.SINGLE_CELL.value

    # 样本数启发式（大量样本暗示单细胞）
    if sample_count > 500:
        return GranularityType.SINGLE_CELL.value

    return GranularityType.UNKNOWN.value


def _norm(term: str) -> str:
    """Normalize term: lowercase + strip non-alphanumeric."""
    import re
    return re.sub(r"[^a-z0-9]", "", term.lower())


def _in_text(term: str, text: str) -> bool:
    """Substring match: handles concatenated scientific terms like scRNAseq."""
    return _norm(term) in _norm(text)


def compute_relevance_score(query: str, dataset: DatasetSchema) -> float:
    """计算相关性分数

    Args:
        query: 原始查询词
        dataset: 数据集 Schema

    Returns:
        0-1 之间的相关性分数
    """
    import re

    # ── 0. 清理查询词 ───────────────────────────────────────────────────────
    raw_terms = query.lower().split()
    clean_terms = [re.sub(r"^[^a-z]+|[^a-z]+$", "", t) for t in raw_terms]
    logic_words = {"and", "or", ""}
    query_clean = [t for t in clean_terms if t and t not in logic_words]

    if not query_clean:
        return 0.0

    title_lower = dataset.title.lower()
    summary_lower = dataset.summary.lower() if dataset.summary else ""
    keywords_text = " ".join(dataset.keywords).lower()
    disease_field_lower = (dataset.disease or "").lower()
    tissue_lower = (dataset.tissue or "").lower()
    text_lower = (
        title_lower + " " + summary_lower + " " +
        keywords_text + " " + disease_field_lower + " " + tissue_lower
    )

    # ── 1. 疾病/方法论关键词库（从 keywords.json 加载，支持运行时覆盖）──────
    # 仅包含痛风/高尿酸血症的精确术语，避免"arthritis/inflammation"误匹配
    # 按长度降序排列（长词优先），使 _in_text 子串匹配更准确
    disease_terms = get_disease_terms()  # noqa: N806
    sc_method_terms = get_sc_method_terms()  # noqa: N806

    # 查询是否包含疾病关键词 / scRNA 关键词
    query_text_lower = query.lower()
    is_disease_query = any(_in_text(dt, query_text_lower) for dt in disease_terms)
    is_sc_query = any(_in_text(st, query_text_lower) for st in sc_method_terms) or (
        "single" in query_text_lower and "cell" in query_text_lower
    )

    # 数据集是否包含疾病/单细胞关键词（子串匹配）
    has_disease_in_dataset = any(_in_text(dt, text_lower) for dt in disease_terms)
    has_sc_in_dataset = any(_in_text(st, text_lower) for st in sc_method_terms)
    # 利用已推断的 data_type/single_cell 字段进一步确认
    is_sc_dataset = dataset.single_cell or has_sc_in_dataset

    # ── 2. 计算相关性分数 ───────────────────────────────────────────────────
    score = 0.0

    # ① 疾病关键词命中（最重要）
    if has_disease_in_dataset:
        score += 0.45

    # ② scRNA 技术类型命中
    if is_sc_dataset:
        score += 0.25

    # ③ 标题子串匹配（query 词在标题 → 额外加成）
    title_matched = sum(1 for t in query_clean if _in_text(t, title_lower))
    score += 0.15 * (title_matched / len(query_clean))

    # ④ 摘要/关键词匹配
    summary_matched = sum(
        1 for t in query_clean
        if _in_text(t, summary_lower) or _in_text(t, keywords_text)
    )
    score += 0.10 * (summary_matched / len(query_clean))

    # ⑤ 结构化字段精确匹配（disease/tissue 字段）
    disease_matched = sum(1 for t in query_clean if t in disease_field_lower)
    score += 0.05 * min(disease_matched, 2) / 2

    # ── 3. 惩罚逻辑 ──────────────────────────────────────────────────────────

    # 惩罚A：疾病查询但数据集完全无疾病/scRNA 上下文
    if is_disease_query:
        if not has_disease_in_dataset and not is_sc_dataset:
            score *= 0.05   # 非常低，几乎不相关
        elif not has_disease_in_dataset and is_sc_dataset:
            score *= 0.15   # 有 scRNA 但无疾病词，轻度惩罚

    # 惩罚B：scRNA 查询但数据集不是 scRNA（bulk/microarray 等）
    # 这是当前输出中最大的问题：GSE160308 是 bulk RNA-seq，不应出现在 scRNA 查询结果前列
    if is_sc_query and not is_sc_dataset:
        score *= 0.25   # 大幅降权，让 bulk 数据集排名靠后

    return min(score, 1.0)


def compute_recency_score(publication_date: str) -> float:
    """计算新颖性分数（基于发表日期）

    Args:
        publication_date: ublication_date 字段（格式如 "2023" 或 "2023-01-15"）

    Returns:
        0-1 之间的新颖性分数
    """
    if not publication_date:
        return 0.0

    from datetime import datetime, timezone

    try:
        # 尝试解析年份
        if len(publication_date) == 4 and publication_date.isdigit():
            year = int(publication_date)
            pub_date = datetime(year, 1, 1, tzinfo=timezone.utc)
        else:
            # 兼容 GEO 格式 "2021/02/04" 和 ISO 格式 "2021-02-04"
            normalized = publication_date.replace("/", "-").replace("Z", "+00:00")
            pub_date = datetime.fromisoformat(normalized).replace(tzinfo=timezone.utc)

        # 计算年龄（年）
        now = datetime.now(timezone.utc)
        age_years = (now - pub_date).days / 365.25

        # 分数：越新越高
        if age_years <= 1:
            return 1.0
        elif age_years <= 2:
            return 0.8
        elif age_years <= 3:
            return 0.6
        elif age_years <= 5:
            return 0.4
        elif age_years <= 10:
            return 0.2
        else:
            return 0.1
    except (ValueError, TypeError):
        return 0.0


def compute_quality_score(dataset: DatasetSchema) -> float:
    """计算数据质量分数

    Args:
        dataset: 数据集 Schema

    Returns:
        0-1 之间的质量分数
    """
    score = 0.0

    # 有 GSE ID 加分
    if dataset.gse_id.startswith("GSE"):
        score += 0.2

    # 有 PubMed ID 加分
    if dataset.pubmed_ids:
        score += 0.2

    # 有 SRA ID 加分
    if dataset.sra_ids:
        score += 0.15

    # 有 BioProject ID 加分
    if dataset.bioproject_ids:
        score += 0.1

    # 有摘要加分
    if dataset.summary and len(dataset.summary) > 50:
        score += 0.15

    # 样本数合理（太少或太多可能有问题）
    if 10 <= dataset.sample_count <= 1000:
        score += 0.1
    elif dataset.sample_count > 0:
        score += 0.05

    # 有平台信息
    if dataset.platform:
        score += 0.1

    return min(score, 1.0)


class SchemaConverter:
    """DatasetRecord 到 DatasetSchema 的转换器

    功能：
    1. 将 DatasetRecord 转换为标准 Schema
    2. 检测 perturbation 类型
    3. 识别 single-cell 数据
    4. 计算排序分数
    """

    def __init__(self, query: str = ""):
        """初始化转换器

        Args:
            query: 原始查询词（用于计算相关性分数）
        """
        self.query = query

    def convert(self, record: DatasetRecord) -> DatasetSchema:
        """将 DatasetRecord 转换为 DatasetSchema

        Args:
            record: 原始数据集记录

        Returns:
            填充好的 DatasetSchema
        """
        # 合并文本用于 perturbation 检测
        text = f"{record.title} {record.abstract}"

        # 检测 perturbation
        perturbation_types = detect_perturbation(text)
        has_perturbation = len(perturbation_types) > 0

        # 推断数据类型
        data_type = infer_data_type(
            omics_type=record.omics_type,
            title=record.title,
            platform=record.platform,
        )

        # 推断粒度
        granularity = infer_granularity(
            omics_granularity=record.omics_granularity,
            sample_count=record.sample_count,
            title=record.title,
            platform=record.platform,
        )

        # single_cell 标记
        single_cell = granularity == GranularityType.SINGLE_CELL.value

        # 解析 keywords
        keywords = record.keywords if isinstance(record.keywords, list) else []

        # 创建 Schema
        schema = DatasetSchema(
            gse_id=record.gse_id,
            title=record.title,
            organism=record.organism,
            data_type=data_type,
            sample_count=record.sample_count,
            platform=record.platform,
            single_cell=single_cell,
            granularity=granularity,
            has_perturbation=has_perturbation,
            perturbation_types=perturbation_types,
            disease=record.disease,
            tissue=record.organ or "",  # 使用 organ 作为 tissue
            organ=record.organ,
            summary=record.abstract,
            keywords=keywords,
            pubmed_ids=record.pubmed_ids if isinstance(record.pubmed_ids, list) else [],
            sra_ids=record.sra_ids if isinstance(record.sra_ids, list) else [],
            bioproject_ids=record.bioproject_ids if isinstance(record.bioproject_ids, list) else [],
            publication_date=record.publication_date,
            journal=record.journal,
        )

        # 计算排序分数
        schema.relevance_score = compute_relevance_score(self.query, schema)
        schema.recency_score = compute_recency_score(record.publication_date)
        schema.quality_score = compute_quality_score(schema)

        return schema

    def convert_batch(
        self,
        records: list[DatasetRecord],
        top_n: int = 50,
    ) -> SearchResultSchema:
        """批量转换并排序

        Args:
            records: DatasetRecord 列表
            top_n: 返回前 N 个结果

        Returns:
            包含排序结果的 SearchResultSchema
        """
        # 转换为 Schema 列表
        schemas = [self.convert(r) for r in records]

        # 创建搜索结果
        result = SearchResultSchema(
            query=self.query,
            total_found=len(records),
            results=schemas,
            expanded_queries=[],
        )

        # 排序
        result.sort_results(top_n=top_n)

        # 计算统计
        result.compute_stats()

        return result


def record_to_schema(record: DatasetRecord, query: str = "") -> DatasetSchema:
    """便捷函数：单条转换"""
    converter = SchemaConverter(query)
    return converter.convert(record)


def records_to_search_result(
    records: list[DatasetRecord],
    query: str = "",
    top_n: int = 50,
) -> SearchResultSchema:
    """便捷函数：批量转换并排序（V1 关键词模式）"""
    converter = SchemaConverter(query)
    return converter.convert_batch(records, top_n=top_n)


async def records_to_search_result_with_llm(
    records: list[DatasetRecord],
    query: str = "",
    top_n: int = 50,
    llm_client: LLMClient | None = None,
    enable_ranking: bool = True,
    enable_summary: bool = False,
    enable_query_analysis: bool = True,
    llm_top_k: int = 20,
    llm_concurrency: int = 5,
    llm_min_relevance: float = 0.0,
    llm_score_all: bool = False,
) -> SearchResultSchema:
    """V2 增强版：批量转换 + LLM 语义评分（可选）+ 摘要（可选）

    当 LLM 不可用时，自动回退到 V1 `records_to_search_result()`。

    Args:
        records: DatasetRecord 列表
        query: 用户查询词
        top_n: 返回前 N 个结果
        llm_client: LLM 客户端，None 则从配置初始化
        enable_ranking: 是否启用 LLM 语义评分（默认启用，前提是 LLM 可用）
        enable_summary: 是否生成 LLM 摘要（默认不生成）
        enable_query_analysis: 是否启用 LLM 查询意图分析（默认启用）
        llm_top_k: LLM 评分的 top_k（只对前 N 个通过 min_relevance 过滤的数据集评分）
        llm_concurrency: 并发请求数
        llm_min_relevance: relevance_score >= 此值才送 LLM 评分（默认 0，即全部送评）
        llm_score_all: 若 True，忽略 top_k 限制，对所有通过 min_relevance 的数据集评分

    Returns:
        包含排序结果（可能包含 LLM 摘要）的 SearchResultSchema
    """
    from loguru import logger

    # ── Step 1: V1 转换（关键词评分 + 基础排序）───────────────────────────
    logger.info("[Step 2.1] V1 关键词转换开始...")
    converter = SchemaConverter(query)
    raw_count = len(records)
    result = converter.convert_batch(records, top_n=top_n * 3)  # 先拿更多，LLM 重排后截取
    logger.info(f"  └─ V1 转换完成: {raw_count} 条记录 → {len(result.results)} 条 Schema")

    # ── Step 2: 获取 LLM 客户端 ──────────────────────────────────────────
    if llm_client is None:
        from sra_search.llm.client import LLMClient
        llm_client = LLMClient.from_config()

    llm_available = llm_client.is_available()

    if not llm_available:
        # 回退到 V1：直接截取 top_n 返回
        logger.warning("[Step 2.2-2.5] LLM 不可用，自动降级为 V1 模式")
        result.results = result.results[:top_n]
        return result

    # ── Step 3: LLM 查询意图分析（可选）──────────────────────────────────
    if enable_query_analysis:
        logger.info("[Step 2.2] LLM 查询意图分析...")
        from sra_search.llm.query_analyzer import LLMQueryAnalyzer
        analyzer = LLMQueryAnalyzer(llm_client)
        intent = await analyzer.analyze(query)
        if intent:
            result.llm_query_intent = intent.to_dict()
            logger.info(f"  └─ 查询意图: {intent.intent_summary}")

    # ── Step 4: LLM 语义评分（可选）──────────────────────────────────────
    llm_scored = 0
    if enable_ranking:
        logger.info("[Step 2.3] LLM 语义评分...")
        from sra_search.llm.ranker import LLMRanker
        settings = None
        try:
            from sra_search.config import get_settings
            settings = get_settings()
        except Exception:
            pass

        ttl = settings.llm_cache_ttl_hours if settings else 168
        ranker = LLMRanker(client=llm_client, cache_ttl_hours=ttl)

        logger.info(f"  ├─ 参数: top_k={llm_top_k}, concurrency={llm_concurrency}, min_relevance={llm_min_relevance}")
        logger.info(f"  ├─ 待评分数据集: {len(result.results)} 条")

        scored_results = await ranker.score_batch(
            datasets=result.results,
            query=query,
            top_k=llm_top_k,
            concurrency=llm_concurrency,
            min_relevance=llm_min_relevance,
            score_all=llm_score_all,
        )

        if scored_results:
            # 用 LLM 分数覆盖 relevance_score，重新排序
            score_map = {ds.gse_id: score for ds, score in scored_results}
            for ds in result.results:
                if ds.gse_id in score_map:
                    ds.relevance_score = score_map[ds.gse_id]

            # 重新计算 total_score 并排序
            weights = {"relevance": 0.5, "recency": 0.2, "quality": 0.15, "sample_size": 0.15}
            for ds in result.results:
                sample_score = min(ds.sample_count / 1000, 1.0) if ds.sample_count > 0 else 0
                ds.total_score = (
                    weights["relevance"] * ds.relevance_score
                    + weights["recency"] * ds.recency_score
                    + weights["quality"] * ds.quality_score
                    + weights["sample_size"] * sample_score
                )
            result.results.sort(key=lambda x: x.total_score, reverse=True)

            llm_scored = min(llm_top_k, len(result.results))
            # 计算分数提升统计
            top_score = result.results[0].relevance_score if result.results else 0
            avg_score = sum(r.relevance_score for r in result.results[:llm_scored]) / llm_scored if llm_scored > 0 else 0
            logger.info(f"  └─ LLM 评分完成: {llm_scored} 条记录完成评分")
            logger.info(f"     - Top1 relevance: {top_score:.3f}")
            logger.info(f"     - 平均 relevance: {avg_score:.3f}")
        else:
            logger.warning("  └─ LLM 评分返回空，保持 V1 排序")

    # 截取 top_n
    original_count = len(result.results)
    result.results = result.results[:top_n]
    result.llm_model = llm_client.__class__.__name__  # 记录使用的模型类名
    result.llm_scored_count = llm_scored

    # ── Step 5: LLM 摘要生成（可选）──────────────────────────────────────
    if enable_summary and result.results:
        logger.info("[Step 2.4] LLM 摘要生成...")
        from sra_search.llm.summarizer import LLMSummarizer
        summarizer = LLMSummarizer(llm_client)
        result.llm_summary = await summarizer.summarize(
            query=query,
            datasets=result.results,
            total_found=result.total_found,
            top_n=min(5, len(result.results)),
        )
        logger.info("  └─ 摘要生成完成")

    logger.info(f"[Step 2.5] Schema 转换完成: {original_count} → {len(result.results)} 条 (top={top_n})")

    return result

