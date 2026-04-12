"""Schema 转换器

将 DatasetRecord 转换为标准 Schema (DatasetSchema)，
支持 perturbation 检测、single-cell 识别、排序评分计算。
"""
from __future__ import annotations

from sra_search.metadata_extractor.models import DatasetRecord, OmicsGranularity
from sra_search.schema import (
    DatasetSchema,
    DataType,
    GranularityType,
    SearchResultSchema,
)

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


def _extract_disease_terms(query: str) -> list[str]:
    """从查询中提取疾病相关关键词（去掉括号等干扰字符）

    扩展查询如 "(gout OR hyperuricemia OR...)" 中的 "(gout" 无法直接匹配标题里的 "gout"，
    因此需要提取干净的疾病词。
    """
    import re

    terms = query.lower().split()
    disease_terms: list[str] = []
    for t in terms:
        # 跳过 AND/OR 等逻辑词和常见方法论词
        if t in {"and", "or", "(", ")", "[", "]"}:
            continue
        # 跳过过于宽泛的组学方法论词
        if t in {"single", "cell", "rna", "seq", "rna-seq", "cell-seq"}:
            continue
        # 提取干净词（去掉首尾非字母字符）
        cleaned = re.sub(r"^[^a-z]+|[^a-z]+$", "", t)
        if len(cleaned) >= 4 and cleaned not in {"with", "from", "human", "mouse", "study", "using", "based", "tissue", "cells"}:
            disease_terms.append(cleaned)
    return disease_terms


def compute_relevance_score(query: str, dataset: DatasetSchema) -> float:
    """计算相关性分数

    Args:
        query: 原始查询词（可能是扩展后的查询词）
        dataset: 数据集 Schema

    Returns:
        0-1 之间的相关性分数
    """
    import re

    # ── 0. 清理查询词（去掉括号、OR/AND 等）──────────────────────────────────
    raw_terms = query.lower().split()
    # 清理每个词（去掉首尾非字母字符）
    clean_terms = [re.sub(r"^[^a-z]+|[^a-z]+$", "", t) for t in raw_terms]
    # 过滤掉空词和逻辑词
    logic_words = {"and", "or", ""}
    query_clean = [t for t in clean_terms if t and t not in logic_words]

    if not query_clean:
        return 0.0

    # ── 1. 提取疾病关键词（用于疾病上下文检查）───────────────────────────────
    disease_terms = _extract_disease_terms(query)

    title_lower = dataset.title.lower()
    summary_lower = dataset.summary.lower() if dataset.summary else ""
    keywords_text = " ".join(dataset.keywords).lower()
    disease_field_lower = (dataset.disease or "").lower()
    tissue_lower = (dataset.tissue or "").lower()
    text_lower = title_lower + " " + summary_lower + " " + keywords_text + " " + disease_field_lower + " " + tissue_lower

    # ── 2. 疾病上下文检查 ───────────────────────────────────────────────────
    # 如果查询包含疾病关键词，但数据集的文本中没有任何疾病词 → 惩罚
    has_disease_context = False
    if disease_terms:
        for dt in disease_terms:
            if dt in text_lower:
                has_disease_context = True
                break

    # ── 3. 计算基础相关性分数 ──────────────────────────────────────────────
    score = 0.0

    # 标题全词精确匹配（权重最高）
    title_matched = sum(1 for t in query_clean if t in title_lower)
    score += 0.5 * (title_matched / len(query_clean))

    # 短语匹配加成（查询作为整体在标题中）
    query_clean_str = " ".join(query_clean)
    if query_clean_str in title_lower:
        score += 0.15

    # 摘要/关键词匹配（较低权重）
    summary_matched = sum(1 for t in query_clean if t in summary_lower or t in keywords_text)
    score += 0.2 * (summary_matched / len(query_clean))

    # 疾病/组织字段匹配加成
    disease_matched = sum(1 for t in query_clean if t in disease_field_lower)
    score += 0.1 * min(disease_matched, 2) / 2  # 最多加成 0.1
    tissue_matched = sum(1 for t in query_clean if t in tissue_lower)
    score += 0.05 * min(tissue_matched, 2) / 2  # 最多加成 0.05

    # ── 4. 疾病上下文惩罚 ───────────────────────────────────────────────────
    # 如果查询有疾病关键词，但数据集完全没有疾病上下文 → 惩罚
    # 这防止了仅方法论匹配（如 COVID scRNA-seq）排名高于有疾病上下文的数据集
    if disease_terms and not has_disease_context:
        score *= 0.2  # 疾病上下文缺失 → 分数降至 1/5

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
    """便捷函数：批量转换并排序"""
    converter = SchemaConverter(query)
    return converter.convert_batch(records, top_n=top_n)
