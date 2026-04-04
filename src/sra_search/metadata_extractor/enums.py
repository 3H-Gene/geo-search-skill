"""结构化元数据枚举模型

参考 ArcInstitute/SRAgent — SRAgent/workflows/metadata.py
定义 scRNA-seq 元数据的所有可能取值，便于规则匹配和结构化存储。
"""
from __future__ import annotations

import re
from enum import Enum


class YesNo(str, Enum):
    """三态布尔值"""
    YES = "yes"
    NO = "no"
    UNSURE = "unsure"


class LibPrepEnum(str, Enum):
    """scRNA-seq 建库方法"""
    TENX = "10x_Genomics"
    SMART_SEQ = "Smart-seq"
    SMART_SEQ2 = "Smart-seq2"
    SMART_SEQ3 = "Smart-seq3"
    CEL_SEQ = "CEL-seq"
    CEL_SEQ2 = "CEL-seq2"
    DROP_SEQ = "Drop-seq"
    IN_DROPS = "indrops"
    SCALE_BIO = "Scale Bio"
    PARSE = "Parse"
    PARSE_EVERCODE = "Parse_evercode"
    PARSE_SPLIT_SEQ = "Parse_split-seq"
    FLUENT = "Fluent"
    PLEXWELL = "plexWell"
    MARS_SEQ = "MARS-seq"
    BD_RHAPSODY = "BD_Rhapsody"
    BULK = "bulk_rna_seq"        # 非单细胞（补充）
    MICROARRAY = "microarray"    # 芯片数据（补充）
    OTHER = "other"
    NA = "not_applicable"
    UNKNOWN = "unknown"


class Tech10XEnum(str, Enum):
    """10X Genomics 技术细分"""
    THREE_PRIME_GEX = "3_prime_gex"
    FIVE_PRIME_GEX = "5_prime_gex"
    ATAC = "atac"
    MULTIOME = "multiome"
    FLEX = "flex"
    VDJ = "vdj"
    FIXED_RNA = "fixed_rna"
    CELLPLEX = "cellplex"
    CNV = "cnv"
    FEATURE_BARCODING = "feature_barcoding"
    OTHER = "other"
    NA = "not_applicable"


class CellPrepEnum(str, Enum):
    """单细胞 vs 单细胞核"""
    SINGLE_NUCLEUS = "single_nucleus"
    SINGLE_CELL = "single_cell"
    UNSURE = "unsure"
    NA = "not_applicable"


class GranularityEnum(str, Enum):
    """数据粒度"""
    SINGLE_CELL = "single_cell"
    BULK = "bulk"
    SPATIAL = "spatial"
    UNKNOWN = "unknown"


# ──────────────────────────────────────────────
# 关键词匹配规则
# ──────────────────────────────────────────────

# LibPrep 关键词映射（顺序重要：更具体的排前面）
_LIB_PREP_PATTERNS: list[tuple[LibPrepEnum, list[str]]] = [
    (LibPrepEnum.TENX, [
        "10x genomics", "10xgenomics", "chromium",
        "10x 3'", "10x 5'", "10x chromium",
        "single cell 3'", "single cell 5'",
    ]),
    (LibPrepEnum.SMART_SEQ3, ["smart-seq3", "smartseq3", "smart seq3"]),
    (LibPrepEnum.SMART_SEQ2, ["smart-seq2", "smartseq2", "smart seq2"]),
    (LibPrepEnum.SMART_SEQ, ["smart-seq", "smartseq", "smart seq"]),
    (LibPrepEnum.CEL_SEQ2, ["cel-seq2", "celseq2", "cel seq2"]),
    (LibPrepEnum.CEL_SEQ, ["cel-seq", "celseq"]),
    (LibPrepEnum.DROP_SEQ, ["drop-seq", "dropseq", "drop seq"]),
    (LibPrepEnum.IN_DROPS, ["indrops", "in-drops", "in drops"]),
    (LibPrepEnum.SCALE_BIO, ["scale bio", "scalebio"]),
    (LibPrepEnum.PARSE_EVERCODE, ["parse evercode", "parse_evercode"]),
    (LibPrepEnum.PARSE_SPLIT_SEQ, ["parse split-seq", "split-seq"]),
    (LibPrepEnum.PARSE, ["parse", "parsebio"]),
    (LibPrepEnum.FLUENT, ["fluent bio", "fluentbio"]),
    (LibPrepEnum.PLEXWELL, ["plexwell"]),
    (LibPrepEnum.MARS_SEQ, ["mars-seq", "marsseq", "mars seq"]),
    (LibPrepEnum.BD_RHAPSODY, ["bd rhapsody", "bd_rhapsody", "rhapsody"]),
    (LibPrepEnum.MICROARRAY, ["microarray", "affymetrix", "illumina bead", "agilent"]),
    (LibPrepEnum.BULK, ["bulk rna", "bulk-rna", "rna-seq bulk", "total rna"]),
]

# 10X 技术关键词映射
_TECH_10X_PATTERNS: list[tuple[Tech10XEnum, list[str]]] = [
    (Tech10XEnum.MULTIOME, ["multiome", "atac+gex", "atac + gex", "atac gex", "arc 2"]),
    (Tech10XEnum.FIVE_PRIME_GEX, [
        "5' gene expression", "5' gex", "5prime", "5' library",
        "v(d)j + 5", "5' feature",
    ]),
    (Tech10XEnum.THREE_PRIME_GEX, [
        "3' gene expression", "3' gex", "3prime", "3' library",
        "3' v3", "3' v2", "3' feature",
    ]),
    (Tech10XEnum.VDJ, ["v(d)j", "vdj", "tcr", "bcr", "immune profiling"]),
    (Tech10XEnum.ATAC, ["atac-seq", "atac seq", "chromatin accessibility"]),
    (Tech10XEnum.FLEX, ["flex", "fixed rna profiling"]),
    (Tech10XEnum.FIXED_RNA, ["fixed rna", "fixation"]),
    (Tech10XEnum.CELLPLEX, ["cellplex", "cell multiplexing"]),
    (Tech10XEnum.CNV, ["cnv", "copy number"]),
    (Tech10XEnum.FEATURE_BARCODING, ["feature barcoding", "antibody capture", "citeseq", "cite-seq"]),
]

# 单细胞核关键词
_SINGLE_NUCLEUS_PATTERNS = [
    "single nucleus", "single-nucleus", "snrna-seq", "snrna seq",
    "sn-rna", "snseq", "nucleus rna", "nuclei rna",
]

# 单细胞关键词
_SINGLE_CELL_PATTERNS = [
    "single cell", "single-cell", "scrna-seq", "scrna seq",
    "sc-rna", "scseq", "single cell rna",
]

# 空间转录组关键词
_SPATIAL_PATTERNS = [
    "spatial", "visium", "slideseq", "slide-seq", "seqfish",
    "merfish", "codex", "stereo-seq", "stereoseq",
]

# Illumina 平台关键词
_ILLUMINA_PATTERNS = [
    "illumina", "nextseq", "novaseq", "hiseq", "miseq",
    "miniseq", "iseq",
]


# ──────────────────────────────────────────────
# 分类器函数
# ──────────────────────────────────────────────

def _normalize(text: str) -> str:
    """统一小写、移除多余空白"""
    return re.sub(r"\s+", " ", text.lower().strip())


def classify_lib_prep(text: str) -> LibPrepEnum:
    """从文本中识别建库方法

    Args:
        text: 元数据文本（title/summary/platform/library_strategy 等）

    Returns:
        最匹配的 LibPrepEnum 值
    """
    t = _normalize(text)
    for enum_val, patterns in _LIB_PREP_PATTERNS:
        if any(p in t for p in patterns):
            return enum_val
    return LibPrepEnum.UNKNOWN


def classify_tech_10x(text: str) -> Tech10XEnum:
    """从文本中识别 10X Genomics 技术细分

    仅在 lib_prep == TENX 时调用。

    Args:
        text: 元数据文本

    Returns:
        Tech10XEnum 值
    """
    t = _normalize(text)
    for enum_val, patterns in _TECH_10X_PATTERNS:
        if any(p in t for p in patterns):
            return enum_val
    return Tech10XEnum.OTHER


def classify_cell_prep(text: str) -> CellPrepEnum:
    """区分单细胞 vs 单细胞核

    Args:
        text: 元数据文本

    Returns:
        CellPrepEnum 值
    """
    t = _normalize(text)
    # 细胞核优先（因为关键词更具体）
    if any(p in t for p in _SINGLE_NUCLEUS_PATTERNS):
        return CellPrepEnum.SINGLE_NUCLEUS
    if any(p in t for p in _SINGLE_CELL_PATTERNS):
        return CellPrepEnum.SINGLE_CELL
    return CellPrepEnum.UNSURE


def classify_granularity(text: str) -> GranularityEnum:
    """识别数据粒度

    Args:
        text: 元数据文本

    Returns:
        GranularityEnum 值
    """
    t = _normalize(text)
    if any(p in t for p in _SPATIAL_PATTERNS):
        return GranularityEnum.SPATIAL
    if any(p in t for p in _SINGLE_CELL_PATTERNS + _SINGLE_NUCLEUS_PATTERNS):
        return GranularityEnum.SINGLE_CELL
    if any(p in t for p in ["bulk rna", "bulk-rna", "microarray", "array"]):
        return GranularityEnum.BULK
    return GranularityEnum.UNKNOWN


def is_illumina(text: str) -> bool | None:
    """检测是否为 Illumina 平台

    Returns:
        True / False / None（无法判断）
    """
    t = _normalize(text)
    if any(p in t for p in _ILLUMINA_PATTERNS):
        return True
    if any(p in t for p in ["pacbio", "oxford nanopore", "ont ", "minion", "promethion"]):
        return False
    return None


def is_single_cell(text: str) -> bool | None:
    """检测是否为单细胞数据

    Returns:
        True / False / None（无法判断）
    """
    t = _normalize(text)
    if any(p in t for p in _SINGLE_CELL_PATTERNS + _SINGLE_NUCLEUS_PATTERNS + _SPATIAL_PATTERNS):
        return True
    return None


def classify_all(text: str) -> dict[str, str]:
    """从元数据文本中提取所有分类信息

    Args:
        text: 合并后的元数据文本（title + summary + platform 等）

    Returns:
        字典，包含所有分类字段
    """
    lib_prep = classify_lib_prep(text)
    granularity = classify_granularity(text)
    cell_prep = classify_cell_prep(text)

    # 10X 技术细分仅在确认是 10X 时执行
    tech_10x = Tech10XEnum.NA
    if lib_prep == LibPrepEnum.TENX:
        tech_10x = classify_tech_10x(text)

    # 逻辑约束（参考 SRAgent metadata.py）
    sc_result = is_single_cell(text)
    single_cell = sc_result is True

    if not single_cell:
        tech_10x = Tech10XEnum.NA
        cell_prep = CellPrepEnum.NA
        if lib_prep == LibPrepEnum.TENX:
            lib_prep = LibPrepEnum.OTHER  # 10X 但不是单细胞（可能是 ATAC）

    if lib_prep != LibPrepEnum.TENX and tech_10x != Tech10XEnum.NA:
        tech_10x = Tech10XEnum.NA

    illumina = is_illumina(text)

    return {
        "is_illumina": YesNo.YES.value if illumina is True else (
            YesNo.NO.value if illumina is False else YesNo.UNSURE.value
        ),
        "is_single_cell": YesNo.YES.value if single_cell else (
            YesNo.NO.value if sc_result is False else YesNo.UNSURE.value
        ),
        "lib_prep": lib_prep.value,
        "tech_10x": tech_10x.value,
        "cell_prep": cell_prep.value,
        "granularity": granularity.value,
    }
