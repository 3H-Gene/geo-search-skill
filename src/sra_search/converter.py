"""Schema 转换器

将 DatasetRecord 转换为标准 Schema (DatasetSchema)，
支持 perturbation 检测、single-cell 识别、排序评分计算。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from sra_search.metadata_extractor.models import DatasetRecord, OmicsGranularity, _now_iso
from sra_search.schema import (
    DatasetSchema,
    DataType,
    GranularityType,
    SearchResultSchema,
)

# Inference 模块 - 增强的元数据推断
try:
    from sra_search.inference import build_dataset_inference, InferenceSchema
    HAS_INFERENCE = True
except ImportError:
    HAS_INFERENCE = False

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
    "single-cell rna sequencing", "single cell rna sequencing",
    "single-cell transcriptome", "single cell transcriptome",
    "single-cell proteomics", "single-cell epigenomics",
    "single-cell rna", "single cell rna",
    "single-cell", "single cell",
    "scRNA-seq", "scRNAseq", "snRNA-seq",
    # 单关键词（按长度降序）
    "snrnaseq", "scrnaseq", "singlecell", "single-nucleus", "single-nuclei",
    "scseq", "snrna", "scrna",
    "cellranger", "cell ranger", "multiome", "multi-ome",
    "cite-seq", "cite-seq2",
    "10x genomics", "10x chromium", "10x",
    "chromium connect", "chromium",
    "drop-seq", "droplet-seq", "droplet",
    "smart-seq", "smartseq2", "smart-seq3",
    "cel-seq",
    "atac-seq", "snATAC",
    "nuclei", "nucleus",
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


# scRNA-seq 特有文件标识（补充文件中出现则强烈暗示是单细胞数据）
_SCRNA_FILE_PATTERNS = [
    "barcodes", "features", "genes", "matrix.mtx", "matrix.tar",
    ".h5", ".h5ad", ".loom", ".zarr",
    "10x", "cellranger", "count_matrix", "filtered_gene_bc",
    "raw_feature", "processed_matrix",
]
# scRNA-seq 特有平台标识
_SCRNA_PLATFORM_KEYWORDS = ["10x", "chromium", "drop-seq", "smart-seq2", "smart-seq3", "cel-seq"]
# bulk RNA-seq / microarray 文件标识（排除项）
_BULK_FILE_PATTERNS = ["cel", "chp", "chrpt", "idat", "MAS5", "RMA", "microarray"]


def analyze_data_format(dataset: DatasetSchema) -> dict[str, Any]:
    """分析数据集的文件格式特征，用于辅助判断数据类型和 LLM 审核优先级。

    Args:
        dataset: 数据集 Schema

    Returns:
        格式分析结果字典:
        - scRNA_signal: float  [0,1]，scRNA 特征信号强度
        - is_bulk_by_files: bool，通过文件类型判断为 bulk 数据
        - file_format_summary: str，文件格式摘要
        - should_skip_llm: bool，是否应跳过 LLM 审核（明显不符合时节省 token）
        - skip_reason: str，跳过原因
    """
    result: dict[str, Any] = {
        "scRNA_signal": 0.0,
        "is_bulk_by_files": False,
        "file_format_summary": "",
        "should_skip_llm": False,
        "skip_reason": "",
    }

    supp = dataset.supplementary_files or []
    supp_text = " ".join(
        f.get("name", "") + " " + f.get("type", "")
        for f in supp
    ).lower()

    # ── 1. scRNA 文件特征打分 ──────────────────────────────────────────
    sc_score = 0.0
    for pattern in _SCRNA_FILE_PATTERNS:
        if pattern.lower() in supp_text:
            sc_score += 0.25
    result["scRNA_signal"] = min(sc_score, 1.0)

    # ── 2. 平台关键字 ──────────────────────────────────────────────────
    platform_lower = (dataset.platform or "").lower()
    if any(kw in platform_lower for kw in _SCRNA_PLATFORM_KEYWORDS):
        result["scRNA_signal"] = max(result["scRNA_signal"], 0.5)

    # ── 3. 通过文件类型判断 bulk ──────────────────────────────────────
    is_bulk = any(p in supp_text for p in _BULK_FILE_PATTERNS)
    result["is_bulk_by_files"] = is_bulk

    # ── 4. 文件格式摘要 ───────────────────────────────────────────────
    if supp:
        file_names = [f.get("name", "unknown") for f in supp[:5]]
        sizes = [f.get("size", 0) for f in supp[:5]]
        fmt_parts = []
        for name, size in zip(file_names, sizes):
            size_str = ""
            if isinstance(size, (int, float)) and size > 0:
                if size > 1e9:
                    size_str = f"({size/1e9:.1f}GB)"
                elif size > 1e6:
                    size_str = f"({size/1e6:.1f}MB)"
                elif size > 1e3:
                    size_str = f"({size/1e3:.1f}KB)"
            fmt_parts.append(f"{name[:40]}{size_str}")
        result["file_format_summary"] = "; ".join(fmt_parts)
        if len(supp) > 5:
            result["file_format_summary"] += f" ... 等{len(supp)}个文件"
    else:
        result["file_format_summary"] = "无补充文件"

    # ── 5. 决定是否跳过 LLM 审核 ──────────────────────────────────────
    # 规则：bulk 数据集 + scRNA 查询 → 直接跳过（节省 token）
    text_for_check = (
        (dataset.title or "") + " " +
        (dataset.summary or "") + " " +
        (dataset.overall_design or "") + " " +
        supp_text
    ).lower()

    # 检查数据集本身是否含 scRNA 信号
    has_sc_signal = (
        result["scRNA_signal"] > 0.1 or
        "scrna" in text_for_check or
        "single-cell" in text_for_check or
        "single cell" in text_for_check
    )

    # 如果查询包含 scRNA 关键词，但数据集文件格式显示是 bulk
    if result["is_bulk_by_files"] and has_sc_signal:
        result["should_skip_llm"] = True
        result["skip_reason"] = "查询scRNA但文件格式为bulk（可能误报）"
    elif result["is_bulk_by_files"] and not has_sc_signal:
        result["should_skip_llm"] = True
        result["skip_reason"] = "bulk microarray/array数据，非目标类型"

    return result


# ── 处理矩阵文件模式（认为"已处理"） ──────────────────────────────────────────
_PROCESSED_MATRIX_PATTERNS = [
    # 表达矩阵常见格式
    "count_matrix", "counts.tsv", "counts.csv", "counts.txt",
    "expression_matrix", "expression.tsv", "expression.csv",
    "tpm", "fpkm", "rpkm", "cpm",
    # 单细胞格式
    "matrix.mtx", "barcodes.tsv", "features.tsv", "genes.tsv",
    ".h5", ".h5ad", ".loom", ".zarr",
    "filtered_gene_bc", "raw_feature_bc",
    # 通用处理矩阵
    "normalized", "processed", "count.csv", "count.txt",
    "gene_expression", "feature_counts",
]
_RAW_ONLY_PATTERNS = [
    ".fastq", ".fastq.gz", ".fq.gz", ".bam", ".cram", ".sra",
]


def infer_file_metadata(supplementary_files: list[dict]) -> dict[str, bool]:
    """从 supplementary_files 列表推断 has_processed_matrix / raw_only。

    Args:
        supplementary_files: DatasetSchema.supplementary_files 列表，
                             每项为 {name, type, size}

    Returns:
        {"has_processed_matrix": bool, "raw_only": bool}
    """
    if not supplementary_files:
        return {"has_processed_matrix": False, "raw_only": False}

    all_names = " ".join(
        f.get("name", "").lower() for f in supplementary_files
    )

    has_processed = any(p in all_names for p in _PROCESSED_MATRIX_PATTERNS)
    has_raw = any(p in all_names for p in _RAW_ONLY_PATTERNS)

    # raw_only = 有原始文件 但没有处理矩阵
    raw_only = has_raw and not has_processed

    return {"has_processed_matrix": has_processed, "raw_only": raw_only}


# ── Tissue 关键词自动提取 ──────────────────────────────────────────────────────
# 常见组织/器官词表（英文小写）—— 按优先级排序（特异性高→低）
_TISSUE_PATTERNS: list[tuple[str, str]] = [
    # 血液/免疫
    ("peripheral blood", "PBMC"), ("pbmc", "PBMC"), ("whole blood", "whole blood"),
    ("bone marrow", "bone marrow"), ("lymph node", "lymph node"),
    ("spleen", "spleen"), ("thymus", "thymus"),
    # 关节/痛风相关
    ("synovial", "synovial tissue"), ("synovium", "synovium"),
    ("joint", "joint"), ("cartilage", "cartilage"),
    ("tophus", "tophus"), ("tophi", "tophus"),
    # 肾/肝
    ("kidney", "kidney"), ("renal", "kidney"),
    ("liver", "liver"), ("hepat", "liver"),
    # 肺
    ("lung", "lung"), ("pulmonary", "lung"), ("bronchial", "bronchial"),
    ("alveolar", "alveolar"),
    # 肠道
    ("colon", "colon"), ("intestin", "intestine"), ("rectum", "rectum"),
    ("ileum", "ileum"), ("cecum", "cecum"),
    # 皮肤
    ("skin", "skin"), ("dermis", "dermis"), ("epidermis", "epidermis"),
    # 脑
    ("brain", "brain"), ("cortex", "cortex"), ("hippocampus", "hippocampus"),
    ("cerebral", "brain"), ("neural", "neural"),
    # 心脏
    ("heart", "heart"), ("cardiac", "heart"), ("myocardium", "heart"),
    # 胰腺
    ("pancreas", "pancreas"), ("pancreatic", "pancreas"), ("islet", "islet"),
    # 脂肪
    ("adipose", "adipose tissue"), ("fat tissue", "adipose tissue"),
    # 肌肉
    ("muscle", "muscle"), ("skeletal muscle", "skeletal muscle"),
    # 乳腺
    ("breast", "breast"), ("mammary", "mammary"),
    # 前列腺
    ("prostate", "prostate"),
    # 卵巢/宫颈
    ("ovary", "ovary"), ("ovarian", "ovary"), ("cervical", "cervix"),
    # 眼
    ("retina", "retina"), ("cornea", "cornea"),
]


def extract_tissue_from_text(
    summary: str,
    overall_design: str = "",
    title: str = "",
) -> str:
    """从 summary / overall_design / title 文本中启发式提取组织/器官信息。

    Args:
        summary: 数据集摘要（GEO summary 字段）
        overall_design: 实验设计描述（GEO overall_design 字段）
        title: 数据集标题

    Returns:
        规范化的组织名称字符串；若无法识别则返回 ""
    """
    # 拼接所有文本，按优先级加权（summary 最重要）
    combined = (
        (summary or "") + " " +
        (overall_design or "") + " " +
        (title or "")
    ).lower()

    if not combined.strip():
        return ""

    for keyword, canonical in _TISSUE_PATTERNS:
        if keyword in combined:
            return canonical

    return ""


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

    def convert(
        self,
        record: DatasetRecord,
        sample_names: list[str] | None = None,
        gsm_attributes: list[dict] | None = None,
    ) -> DatasetSchema:
        """将 DatasetRecord 转换为 DatasetSchema

        Args:
            record: 原始数据集记录
            sample_names: 可选，手动传入的样本名列表（通常来自 record.gsm_sample_names）
            gsm_attributes: 可选，手动传入的GSM属性列表（通常来自 record.gsm_attributes）

        优先级：sample_names 参数 > record.gsm_sample_names > None（走文本推断）
        """
        # 优先使用传入参数，其次使用 record 中存储的 GSM 样本名和属性
        effective_sample_names = sample_names or record.gsm_sample_names or None
        effective_gsm_attributes = gsm_attributes or record.gsm_attributes or []

        # ── Inference 模块增强推断 ───────────────────────────────────────
        inference_result: InferenceSchema | None = None
        if HAS_INFERENCE:
            try:
                inference_result = build_dataset_inference(
                    dataset_id=record.gse_id,
                    title=record.title,
                    summary=record.abstract or "",
                    overall_design=record.overall_design or "",
                    platform=record.platform,
                    sample_names=effective_sample_names,
                    sample_count=record.sample_count,
                )
            except Exception as e:
                logger.debug(f"Inference failed for {record.gse_id}: {e}")

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

        # supplementary_files 列表
        supp_files = record.supplementary_files if isinstance(record.supplementary_files, list) else []

        # 推断文件元数据（has_processed_matrix / raw_only）
        file_meta = infer_file_metadata(supp_files)


        # ── 使用 Inference 模块增强字段推断 ───────────────────────────────
        inferred_disease = ""
        inferred_organ = ""
        inferred_omics_type = ""
        platform_mapped = ""

        if inference_result:
            # disease 推断
            inferred_disease = inference_result.biological_context.get("disease", "")
            if not inferred_disease or inferred_disease == "unknown":
                inferred_disease = ""

            # organ 推断
            inferred_organ = inference_result.biological_context.get("organ", "")
            if not inferred_organ or inferred_organ == "unknown":
                inferred_organ = ""

            # omics_type 推断
            inferred_omics_type = inference_result.omics.get("omics_type", "")
            if not inferred_omics_type or inferred_omics_type == "unknown":
                inferred_omics_type = ""

            # platform 映射
            platform_mapped = inference_result.platform.get("mapped", "")

            # tissue 字段：优先 record.organ > inference > extract_tissue
            tissue_val = record.organ or inferred_organ
            if not tissue_val:
                tissue_val = extract_tissue_from_text(
                    summary=record.abstract or "",
                    overall_design=record.overall_design or "",
                    title=record.title or "",
                )

            # granularity 增强：使用 inference 结果
            inferred_granularity = inference_result.omics.get("granularity", "")
            if inferred_granularity and inferred_granularity != "unknown":
                granularity = inferred_granularity
                # 统一比较：inference 返回 "single-cell"，schema 用 "single_cell"
                single_cell = "single" in granularity.lower()

            # 更新 data_type（如果 inference 提供了 omics_type）
            if inferred_omics_type and data_type == DataType.OTHER.value:
                data_type = infer_data_type(
                    omics_type=inferred_omics_type,
                    title="",
                    platform="",
                )
        else:
            # tissue 字段：优先使用 record.organ，若为空则从文本中提取
            tissue_val = record.organ or ""
            if not tissue_val:
                tissue_val = extract_tissue_from_text(
                    summary=record.abstract or "",
                    overall_design=record.overall_design or "",
                    title=record.title or "",
                )

        # disease 字段：优先 record.disease > inference
        disease_val = record.disease or inferred_disease

        # organ 字段：优先 record.organ > inference
        organ_val = record.organ or inferred_organ

        # 创建 Schema
        schema = DatasetSchema(
            gse_id=record.gse_id,
            title=record.title,
            organism=record.organism,
            data_type=data_type,
            sample_count=record.sample_count,
            platform=platform_mapped or record.platform,
            single_cell=single_cell,
            granularity=granularity,
            has_perturbation=has_perturbation,
            perturbation_types=perturbation_types,
            disease=disease_val,
            tissue=tissue_val,
            organ=organ_val,
            summary=record.abstract,
            overall_design=record.overall_design or "",
            keywords=keywords,
            supplementary_files=supp_files,
            series_matrix_available=record.series_matrix_available,
            ftp_link=record.ftplink or "",
            has_processed_matrix=file_meta["has_processed_matrix"],
            raw_only=file_meta["raw_only"],
            pubmed_ids=record.pubmed_ids if isinstance(record.pubmed_ids, list) else [],
            sra_ids=record.sra_ids if isinstance(record.sra_ids, list) else [],
            bioproject_ids=record.bioproject_ids if isinstance(record.bioproject_ids, list) else [],
            publication_date=record.publication_date,
            journal=record.journal,
            gsm_sample_names=effective_sample_names or [],
            gsm_attributes=effective_gsm_attributes,
        )

        # 保存 inference 结果到 schema 扩展字段（如果有）
        if inference_result and inference_result.summary_text:
            schema._inference_summary = inference_result.summary_text

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


def record_to_schema(
    record: DatasetRecord,
    query: str = "",
    sample_names: list[str] | None = None,
    gsm_attributes: list[dict] | None = None,
) -> DatasetSchema:
    """便捷函数：单条转换

    Args:
        record: 数据集记录（优先从 record.gsm_sample_names 获取样本名）
        query: 查询词（用于相关性评分）
        sample_names: 可选，手动传入的样本名（覆盖 record.gsm_sample_names）
        gsm_attributes: 可选，手动传入的GSM属性（覆盖 record.gsm_attributes）
    """
    converter = SchemaConverter(query)
    return converter.convert(record, sample_names=sample_names, gsm_attributes=gsm_attributes)


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
    db: Database | None = None,
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

    # ── Step 2.5: V1 数据格式预过滤（节省 LLM token）────────────────────
    logger.info("[Step 2.3] V1 数据格式预过滤...")
    prefilter_passed: list[DatasetSchema] = []
    prefilter_skipped: list[tuple[DatasetSchema, str]] = []

    for ds in result.results:
        fmt = analyze_data_format(ds)
        if fmt["should_skip_llm"]:
            prefilter_skipped.append((ds, fmt["skip_reason"]))
        else:
            prefilter_passed.append(ds)

    if prefilter_skipped:
        logger.info(
            f"  ├─ 预过滤通过: {len(prefilter_passed)} 条，"
            f"跳过 LLM: {len(prefilter_skipped)} 条（节省 token）"
        )
        for ds, reason in prefilter_skipped[:5]:
            logger.debug(f"     - {ds.gse_id}: {reason}")
        if len(prefilter_skipped) > 5:
            logger.debug(f"     ... 还有 {len(prefilter_skipped) - 5} 条")
    else:
        logger.info(f"  └─ 所有 {len(prefilter_passed)} 条均通过预过滤")

    # ── Step 2.4: V1 相关性阈值过滤（跳过明显不相关的数据集）────────────
    # 设定相关性阈值，低于阈值的数据集直接跳过，不调用 LLM
    relevance_threshold = 0.15  # V1 relevance_score 低于此值视为不相关
    relevance_skipped: list[tuple[DatasetSchema, str]] = []

    still_candidates: list[DatasetSchema] = []
    for ds in prefilter_passed:
        if ds.relevance_score < relevance_threshold:
            relevance_skipped.append((ds, f"V1相关性分数 {ds.relevance_score:.2f} < {relevance_threshold}"))
        else:
            still_candidates.append(ds)

    if relevance_skipped:
        logger.info(
            f"  ├─ 相关性过滤通过: {len(still_candidates)} 条，"
            f"跳过 LLM: {len(relevance_skipped)} 条（V1相关性过低）"
        )
        for ds, reason in relevance_skipped[:5]:
            logger.debug(f"     - {ds.gse_id}: {reason}")
        if len(relevance_skipped) > 5:
            logger.debug(f"     ... 还有 {len(relevance_skipped) - 5} 条")
    else:
        logger.info(f"  └─ 所有 {len(still_candidates)} 条均通过相关性过滤")

    # 合并所有跳过项：数据格式过滤 + 相关性过滤
    all_skipped = prefilter_skipped + relevance_skipped
    skipped_ds = [ds for ds, _ in all_skipped]
    skipped_ds.sort(key=lambda x: x.relevance_score, reverse=True)

    # 预过滤后的候选集（用于 LLM 评分和摘要生成）
    candidates_for_llm = still_candidates

    # ── Step 3: LLM 查询意图分析（可选）──────────────────────────────────
    if enable_query_analysis:
        logger.info("[Step 2.4] LLM 查询意图分析...")
        from sra_search.llm.query_analyzer import LLMQueryAnalyzer
        analyzer = LLMQueryAnalyzer(llm_client)
        intent = await analyzer.analyze(query)
        if intent:
            result.llm_query_intent = intent.to_dict()
            logger.info(f"  └─ 查询意图: {intent.intent_summary}")

    # ── Step 4: LLM 语义评分（可选）──────────────────────────────────────
    llm_scored = 0
    if enable_ranking:
        logger.info("[Step 2.5] LLM 语义评分...")
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
        logger.info(f"  ├─ 预过滤后待评分: {len(candidates_for_llm)} 条（跳过 {len(prefilter_skipped)} 条格式不符）")

        scored_results = await ranker.score_batch(
            datasets=candidates_for_llm,
            query=query,
            top_k=llm_top_k,
            concurrency=llm_concurrency,
            min_relevance=llm_min_relevance,
            score_all=llm_score_all,
        )

        if scored_results:
            # 用 LLM 分数覆盖 relevance_score
            score_map = {ds.gse_id: score for ds, score in scored_results}
            for ds in candidates_for_llm:
                if ds.gse_id in score_map:
                    ds.relevance_score = score_map[ds.gse_id]

            # 重新计算 total_score 并排序
            weights = {"relevance": 0.5, "recency": 0.2, "quality": 0.15, "sample_size": 0.15}
            for ds in candidates_for_llm:
                sample_score = min(ds.sample_count / 1000, 1.0) if ds.sample_count > 0 else 0
                ds.total_score = (
                    weights["relevance"] * ds.relevance_score
                    + weights["recency"] * ds.recency_score
                    + weights["quality"] * ds.quality_score
                    + weights["sample_size"] * sample_score
                )
            candidates_for_llm.sort(key=lambda x: x.total_score, reverse=True)

            llm_scored = min(llm_top_k, len(candidates_for_llm))
            # 计算分数提升统计
            top_score = candidates_for_llm[0].relevance_score if candidates_for_llm else 0
            avg_score = sum(r.relevance_score for r in candidates_for_llm[:llm_scored]) / llm_scored if llm_scored > 0 else 0
            logger.info(f"  ├─ LLM 评分完成: {llm_scored} 条完成评分（预过滤跳过 {len(all_skipped)} 条）")
            logger.info(f"  │   - Top1 relevance: {top_score:.3f}")
            logger.info(f"  │   - 平均 relevance: {avg_score:.3f}")

            # 将排序后的 LLM 评分候选作为最终结果
            # 方案A：低于V1相关性的数据完全不展示，避免用户看到无关结果
            result.results = candidates_for_llm
        else:
            logger.warning("  ├─ LLM 评分返回空，保持 V1 排序（仅通过V1过滤的数据）")
            result.results = candidates_for_llm

    # 截取 top_n
    original_count = len(result.results)
    result.results = result.results[:top_n]
    result.llm_model = llm_client.__class__.__name__  # 记录使用的模型类名
    result.llm_scored_count = llm_scored

    # ── Step 5: 补全缺失元数据（对摘要为空的 SRA-only 记录做 GEO 丰富化）────
    logger.info("[Step 2.5] 元数据补全...")
    try:
        from sra_search.retriever.geo_api import GeoRetriever
        settings_obj = None
        try:
            from sra_search.config import get_settings
            settings_obj = get_settings()
        except Exception:
            pass
        email = settings_obj.ncbi_email if settings_obj else "sra-search@example.com"
        api_key = getattr(settings_obj, "ncbi_api_key", None) if settings_obj else None
        geo_api = GeoRetriever(email=email, api_key=api_key)

        # 找出需要补全的记录：摘要为空 且 有 sra_ids（可能是 SRA-only 记录）
        needs_enrichment: list[tuple[int, DatasetSchema]] = []
        for idx, ds in enumerate(result.results):
            if not ds.summary and ds.sra_ids:
                needs_enrichment.append((idx, ds))

        if needs_enrichment:
            logger.info(f"  ├─ 发现 {len(needs_enrichment)} 条记录需要元数据补全...")
            # 通过 ELink 从 BioProject 找关联 GSE，再用 esummary 取元数据
            import aiohttp as _aiohttp

            # 用 SRP ID 作为 BioProject ID 查 ELink
            gse_ids_found: dict[str, str] = {}  # srp_id → gse_id

            async with _aiohttp.ClientSession(
                connector=_aiohttp.TCPConnector(ssl=False)
            ) as sess:
                for _, ds in needs_enrichment:
                    srp = ds.sra_ids[0] if ds.sra_ids else ""
                    if not srp:
                        continue
                    try:
                        params = {
                            "db": "gds",        # 目标库：GEO
                            "dbfrom": "bioproject",  # 源库：BioProject（SRP 编号也是 BioProject ID）
                            "id": srp,
                            "retmode": "json",
                            "email": email,
                        }
                        if api_key:
                            params["api_key"] = api_key
                        await asyncio.sleep(0.2)  # NCBI rate limit
                        async with sess.get(
                            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi",
                            params=params,
                        ) as resp:
                            if resp.status == 200:
                                import xml.etree.ElementTree as ET
                                text = await resp.text()
                                root = ET.fromstring(text)
                                for link_set_db in root.iter("LinkSetDb"):
                                    for link in link_set_db.iter("Link"):
                                        gse = (link.find("Id") or type(link, (), {"text": ""})()).text or ""
                                        if gse.startswith("GSE"):
                                            gse_ids_found[srp] = gse
                                            break
                    except Exception:
                        pass

                # 获取 GEO esummary 批量元数据
                gse_list = list(set(gse_ids_found.values()))
                if gse_list:
                    geo_records = await geo_api._fetch_summaries(gse_list, sess)
                    geo_map = {rec.gse_id: rec for rec in geo_records}

                    for _, ds in needs_enrichment:
                        srp = ds.sra_ids[0] if ds.sra_ids else ""
                        gse = gse_ids_found.get(srp, "")
                        if gse and gse in geo_map:
                            rec = geo_map[gse]
                            # 补全缺失字段
                            if not ds.summary and rec.summary:
                                ds.summary = rec.summary
                            if not ds.overall_design and rec.overall_design:
                                ds.overall_design = rec.overall_design
                            if not ds.organism and rec.organism:
                                ds.organism = rec.organism
                            if not ds.platform and rec.platform:
                                ds.platform = rec.platform
                            if ds.sample_count == 0 and rec.sample_count > 0:
                                ds.sample_count = rec.sample_count
                            if not ds.publication_date and rec.publication_date:
                                ds.publication_date = rec.publication_date
                            if rec.keywords:
                                ds.keywords = rec.keywords
                            # 数据文件信息（优先用 GEO 补充文件信息）
                            if rec.supplementary_files and not ds.supplementary_files:
                                ds.supplementary_files = rec.supplementary_files
                                ds.series_matrix_available = rec.series_matrix_available
                            if rec.ftplink and not ds.ftplink:
                                ds.ftplink = rec.ftplink

            enriched_count = sum(
                1 for idx, ds in needs_enrichment
                if ds.summary
            )
            logger.info(f"  └─ 元数据补全完成: {enriched_count}/{len(needs_enrichment)} 条成功")
        else:
            logger.info("  └─ 所有记录均有元数据，无需补全")
    except Exception as e:
        logger.warning(f"  └─ 元数据补全失败（不影响后续流程）: {e}")

    # ── Step 6: LLM 数据集分析（逐条一句话总结）─────────────────────────
    # 注意：只对预过滤通过的候选集调用 LLM，避免浪费 token
    logger.info("[Step 2.6] LLM 数据集分析...")
    from sra_search.llm.dataset_summarizer import LLMDatasetSummarizer
    from sra_search.data_store.database import Database as DB

    summarizer = LLMDatasetSummarizer(llm_client)

    # 获取需要调用 LLM 的候选集（预过滤后的 + 未被预过滤的数据集）
    # candidates_for_llm 是经过数据格式+相关性预过滤的候选集
    summarizer_candidates = candidates_for_llm if candidates_for_llm else result.results

    # ── LLM 缓存查询（避免重复分析同一数据集）──────────────────────────
    # 从数据库读取已有 LLM 分析结果，只对无缓存的数据集调用 LLM
    cached_map: dict[str, dict] = {}
    if db is not None:
        try:
            all_gse_ids = [ds.gse_id for ds in summarizer_candidates]
            cached_map = db.get_llm_cache(all_gse_ids)
            if cached_map:
                logger.info(f"  ├─ LLM 缓存命中: {len(cached_map)} 条，跳过 LLM 调用")
        except Exception as e:
            logger.warning(f"  ├─ LLM 缓存查询失败（继续调用 LLM）: {e}")

    # 分离有缓存和无缓存的候选集
    uncached_candidates = [ds for ds in summarizer_candidates if ds.gse_id not in cached_map]

    logger.info(
        f"  ├─ 预过滤跳过: {len(all_skipped)} 条，"
        f"缓存命中: {len(cached_map)} 条，"
        f"需 LLM 分析: {len(uncached_candidates)} 条"
    )

    # 只对无缓存的数据集调用 LLM
    analyses: list = []
    if uncached_candidates:
        try:
            analyses = await summarizer.summarize_batch_async(
                datasets=uncached_candidates,
                query=query,
                concurrency=settings.llm_concurrency if settings else 5,
            )
        except Exception as e:
            # LLM 调用完全失败时，降级处理：不生成摘要，但不影响排序结果
            logger.warning(f"[Step 2.6] LLM 批量分析失败（已缓存 {len(cached_map)} 条，降级处理）: {e}")
            analyses = []
            for ds in uncached_candidates:
                from dataclasses import dataclass
                @dataclass
                class _FallbackAnalysis:
                    one_sentence_summary: str = ""
                    sample_grouping: str = ""
                    cell_count: str = ""
                    relevance_reason: str = ""
                analyses.append(_FallbackAnalysis())
        # 逐条保存 LLM 结果到数据库（缓存）
        if db is not None:
            try:
                model_name = settings.llm_model if settings else ""
                for ds, analysis in zip(uncached_candidates, analyses):
                    db.update_llm_cache(ds.gse_id, {
                        "llm_summary": analysis.one_sentence_summary,
                        "llm_sample_grouping": analysis.sample_grouping,
                        "llm_cell_count": analysis.cell_count,
                        "llm_relevance_reason": analysis.relevance_reason,
                        "llm_model": model_name,
                    })
                logger.info(f"  ├─ LLM 结果已缓存: {len(uncached_candidates)} 条")
            except Exception as e:
                logger.warning(f"  ├─ LLM 缓存保存失败: {e}")
    else:
        logger.info("  ├─ 全部候选集已缓存，无需调用 LLM")

    # 建立分析结果映射 = 缓存结果 + 新分析结果
    analysis_map: dict[str, object] = {}

    # 添加缓存结果（转换为 DatasetAnalysis 兼容格式）
    from dataclasses import dataclass
    @dataclass
    class _CachedAnalysis:
        one_sentence_summary: str
        sample_grouping: str
        cell_count: str
        relevance_reason: str
        llm_analyzed_at: str
        llm_model: str

    for gse_id, cache in cached_map.items():
        analysis_map[gse_id] = _CachedAnalysis(
            one_sentence_summary=cache.get("llm_summary", ""),
            sample_grouping=cache.get("llm_sample_grouping", ""),
            cell_count=cache.get("llm_cell_count", ""),
            relevance_reason=cache.get("llm_relevance_reason", ""),
            llm_analyzed_at=cache.get("llm_analyzed_at", ""),
            llm_model=cache.get("llm_model", ""),
        )

    # 添加新分析结果
    for ds, analysis in zip(uncached_candidates, analyses):
        analysis_map[ds.gse_id] = analysis

    # 建立跳过原因映射
    skip_reason_map: dict[str, str] = {ds.gse_id: reason for ds, reason in all_skipped}

    # 将分析结果填充到 result.results（包含预过滤跳过的数据集）
    _now = _now_iso()
    _model = settings.llm_model if settings else ""
    for ds in result.results:
        if ds.gse_id in analysis_map:
            analysis = analysis_map[ds.gse_id]
            ds.llm_one_sentence_summary = analysis.one_sentence_summary
            ds.llm_sample_grouping = analysis.sample_grouping
            ds.llm_cell_count = analysis.cell_count
            ds.llm_relevance_reason = analysis.relevance_reason
            # 缓存结果包含 llm_analyzed_at / llm_model，来自数据库
            ds.llm_analyzed_at = getattr(analysis, "llm_analyzed_at", _now)
            ds.llm_model = getattr(analysis, "llm_model", _model)
        elif ds.gse_id in skip_reason_map:
            # 预过滤跳过的数据集使用跳过原因作为摘要
            skip_reason = skip_reason_map[ds.gse_id]
            if "V1相关性" in skip_reason:
                ds.llm_one_sentence_summary = f"[V1相关性过低] {ds.title[:50]}..."
            else:
                ds.llm_one_sentence_summary = f"[数据格式不符] {ds.title[:50]}..."
            ds.llm_sample_grouping = "NA"
            ds.llm_cell_count = "NA"
            ds.llm_relevance_reason = skip_reason
    # 实际调用 LLM 条目数（不含缓存）
    llm_called = len(uncached_candidates)
    logger.info(f"  └─ 数据集分析完成（LLM 调用: {llm_called} 条，缓存命中: {len(cached_map)} 条）")

    # ── Step 7: LLM 整体摘要生成（可选）──────────────────────────────────
    if enable_summary and result.results:
        logger.info("[Step 2.7] LLM 整体摘要生成...")
        from sra_search.llm.summarizer import LLMSummarizer
        summary_summarizer = LLMSummarizer(llm_client)
        result.llm_summary = await summary_summarizer.summarize(
            query=query,
            datasets=result.results,
            total_found=result.total_found,
            top_n=min(5, len(result.results)),
        )
        logger.info("  └─ 摘要生成完成")

    logger.info(f"[Step 2.8] Schema 转换完成: {original_count} → {len(result.results)} 条 (top={top_n})")

    return result

