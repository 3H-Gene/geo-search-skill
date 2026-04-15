"""规则引擎 - 从文本中推断结构化信息

核心功能：
- 疾病推断（整合 disease_ontology 和 keywords.json）
- 器官推断（整合 organ_ontology）
- 组学类型推断（整合 omics_types ontology）
- 粒度推断（bulk / single-cell / spatial）
- 平台映射（platform → 技术名）
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 加载本体数据
_ONTOLOGY_DIR = Path(__file__).parent.parent / "data" / "ontologies"
_KEYWORDS_FILE = Path(__file__).parent.parent / "data" / "keywords.json"

# ============ 疾病关键词库（整合 keywords.json）============

_DISEASE_TERMS: list[tuple[str, str, float]] = []  # (pattern, canonical_name, weight)


def _load_disease_terms() -> list[tuple[str, str, float]]:
    """加载疾病关键词库"""
    global _DISEASE_TERMS
    if _DISEASE_TERMS:
        return _DISEASE_TERMS

    terms: list[tuple[str, str, float]] = []

    # 1. 加载 keywords.json 中的 disease_terms（按长度降序）
    if _KEYWORDS_FILE.exists():
        try:
            with open(_KEYWORDS_FILE, "r", encoding="utf-8") as f:
                kw_data = json.load(f)
            for term in kw_data.get("disease_terms", {}).get("terms", []):
                # 映射到规范名
                canonical = _map_disease_canonical(term)
                terms.append((term.lower(), canonical, 1.0))
        except Exception:
            pass

    # 2. 加载 doid_hierarchy.json 中的疾病
    doid_file = _ONTOLOGY_DIR / "doid_hierarchy.json"
    if doid_file.exists():
        try:
            with open(doid_file, "r", encoding="utf-8") as f:
                doid_data = json.load(f)
            for disease, data in doid_data.get("diseases", {}).items():
                canonical = data.get("canonical", disease)
                # 添加 canonical 名称
                terms.append((disease.lower(), canonical, 0.9))
                # 添加同义词
                for syn in data.get("synonyms", []):
                    terms.append((syn.lower(), canonical, 0.8))
                # 添加 search_terms
                for st in data.get("search_terms", []):
                    terms.append((st.lower(), canonical, 0.7))
        except Exception:
            pass

    # 按模式长度降序排序（确保长词优先匹配）
    terms.sort(key=lambda x: len(x[0]), reverse=True)
    _DISEASE_TERMS = terms
    return terms


def _map_disease_canonical(term: str) -> str:
    """将疾病关键词映射到规范名"""
    mapping = {
        "gout": "Gout",
        "gouty": "Gout",
        "gouty arthritis": "Gout",
        "hyperuricemia": "Hyperuricemia",
        "hyperuricemic": "Hyperuricemia",
        "uric acid": "Hyperuricemia",
        "monosodium urate": "Hyperuricemia",
        "msu": "Hyperuricemia",
        "tophus": "Gout",
        "tophi": "Gout",
        "podagra": "Gout",
        "urate": "Hyperuricemia",
        "uric": "Hyperuricemia",
        "cancer": "Cancer",
        "tumor": "Cancer",
        "carcinoma": "Cancer",
        "melanoma": "Cancer",
        "lymphoma": "Cancer",
        "leukemia": "Leukemia",
    }
    return mapping.get(term.lower(), term.title())


# ============ 器官关键词库 ============

_ORGAN_TERMS: list[tuple[str, str, float]] = []


def _load_organ_terms() -> list[tuple[str, str, float]]:
    """加载器官关键词库"""
    global _ORGAN_TERMS
    if _ORGAN_TERMS:
        return _ORGAN_TERMS

    terms: list[tuple[str, str, float]] = []

    # 加载 uberon_organs.json
    organ_file = _ONTOLOGY_DIR / "uberon_organs.json"
    if organ_file.exists():
        try:
            with open(organ_file, "r", encoding="utf-8") as f:
                organ_data = json.load(f)
            for organ, data in organ_data.get("organs", {}).items():
                canonical = data.get("canonical", organ)
                # 添加 canonical
                terms.append((organ.lower(), canonical, 1.0))
                # 添加同义词
                for syn in data.get("synonyms", []):
                    terms.append((syn.lower(), canonical, 0.9))
                # 添加 search_terms
                for st in data.get("search_terms", []):
                    terms.append((st.lower(), canonical, 0.7))
                # 添加形容词
                adj = data.get("adjective", "")
                if adj:
                    terms.append((adj.lower(), canonical, 0.6))
        except Exception:
            pass

    # 按长度降序排序
    terms.sort(key=lambda x: len(x[0]), reverse=True)
    _ORGAN_TERMS = terms
    return terms


# ============ 组学类型关键词库 ============

_OMICS_TERMS: list[tuple[str, str, str, float]] = []  # (pattern, canonical, granularity, weight)


def _load_omics_terms() -> list[tuple[str, str, str, float]]:
    """加载组学类型关键词库"""
    global _OMICS_TERMS
    if _OMICS_TERMS:
        return _OMICS_TERMS

    terms: list[tuple[str, str, str, float]] = []

    # 加载 omics_types.json
    omics_file = _ONTOLOGY_DIR / "omics_types.json"
    if omics_file.exists():
        try:
            with open(omics_file, "r", encoding="utf-8") as f:
                omics_data = json.load(f)
            for omics_name, data in omics_data.get("omics_types", {}).items():
                canonical = data.get("canonical", omics_name)
                category = data.get("category", "")
                # 判断 granularity
                if "single" in category.lower() or "single-cell" in omics_name.lower():
                    granularity = "single-cell"
                elif "spatial" in category.lower():
                    granularity = "spatial"
                else:
                    granularity = "bulk"

                # 添加 canonical
                terms.append((omics_name.lower(), canonical, granularity, 1.0))
                # 添加 aliases
                for alias in data.get("aliases", []):
                    terms.append((alias.lower(), canonical, granularity, 0.9))
                # 添加 keywords
                for kw in data.get("keywords", []):
                    terms.append((kw.lower(), canonical, granularity, 0.8))
        except Exception:
            pass

    # 按长度降序排序
    terms.sort(key=lambda x: len(x[0]), reverse=True)
    _OMICS_TERMS = terms
    return terms


# ============ 单细胞技术关键词 ============

_SC_TERMS: list[tuple[str, float]] = []  # (pattern, weight)


def _load_sc_terms() -> list[tuple[str, float]]:
    """加载单细胞技术关键词库"""
    global _SC_TERMS
    if _SC_TERMS:
        return _SC_TERMS

    terms: list[tuple[str, float]] = []

    # 从 keywords.json 加载
    if _KEYWORDS_FILE.exists():
        try:
            with open(_KEYWORDS_FILE, "r", encoding="utf-8") as f:
                kw_data = json.load(f)
            for term in kw_data.get("sc_method_terms", {}).get("terms", []):
                terms.append((term.lower(), 1.0))
        except Exception:
            pass

    # 额外的单细胞关键词
    extra_sc_terms = [
        ("single-cell rna sequencing", 1.0),
        ("single cell rna sequencing", 1.0),
        ("single-cell transcriptome", 1.0),
        ("single-cell proteomics", 1.0),
        ("single-cell epigenomics", 1.0),
        ("single-cell atac-seq", 1.0),
        ("scrnaseq", 0.9),
        ("snrnaseq", 0.9),
        ("scRNA", 0.8),
        ("snRNA", 0.8),
        ("10x genomics", 0.9),
        ("10x chromium", 0.9),
        ("cellranger", 0.7),
        ("multiome", 1.0),
        ("cite-seq", 0.9),
        ("cell hashing", 0.8),
        ("nuclei isolation", 0.7),
        ("droplet", 0.6),
        ("smart-seq", 0.8),
        ("smartseq2", 0.8),
        ("drop-seq", 0.7),
        ("indrop", 0.7),
    ]
    terms.extend(extra_sc_terms)

    # 按长度降序排序
    terms.sort(key=lambda x: len(x[0]), reverse=True)
    _SC_TERMS = terms
    return terms


# ============ 平台映射 ============

_PLATFORM_MAP: dict[str, tuple[str, str]] = {}  # platform_code -> (mapped_name, category)


def _load_platform_map() -> dict[str, tuple[str, str]]:
    """加载平台映射表"""
    global _PLATFORM_MAP
    if _PLATFORM_MAP:
        return _PLATFORM_MAP

    mapping: dict[str, tuple[str, str]] = {}

    # Illumina 平台
    illumina_patterns = {
        "hiseq": ("Illumina HiSeq", "Sequencing"),
        "novaseq": ("Illumina NovaSeq", "Sequencing"),
        "nextseq": ("Illumina NextSeq", "Sequencing"),
        "miseq": ("Illumina MiSeq", "Sequencing"),
        "iseq": ("Illumina iSeq", "Sequencing"),
        "hiseq x": ("Illumina HiSeq X", "Sequencing"),
        "novaseq 6000": ("Illumina NovaSeq 6000", "Sequencing"),
        "nextseq 2000": ("Illumina NextSeq 2000", "Sequencing"),
        "nextseq 1000": ("Illumina NextSeq 1000", "Sequencing"),
    }

    # 其他测序平台
    other_platforms = {
        "ion torrent": ("Ion Torrent", "Sequencing"),
        "ion proton": ("Ion Proton", "Sequencing"),
        "pgm": ("Ion PGM", "Sequencing"),
        " Roche 454": ("Roche 454", "Sequencing"),
        "solid": ("AB SOLiD", "Sequencing"),
        "pacbio": ("PacBio", "Sequencing"),
        " Sequel": ("PacBio Sequel", "Sequencing"),
        "nanopore": ("Oxford Nanopore", "Sequencing"),
        "minion": ("Oxford Nanopore MinION", "Sequencing"),
        "promethion": ("Oxford Nanopore PromethION", "Sequencing"),
    }

    # 芯片平台
    microarray_platforms = {
        "affymetrix": ("Affymetrix Microarray", "Microarray"),
        "agilent": ("Agilent Microarray", "Microarray"),
        "illumina beadchip": ("Illumina BeadChip", "Microarray"),
        "nimblegen": ("NimbleGen Microarray", "Microarray"),
    }

    # 合并所有映射
    for patterns, (name, cat) in [
        *[(p, (n, c)) for p, (n, c) in illumina_patterns.items()],
        *[(p, (n, c)) for p, (n, c) in other_platforms.items()],
        *[(p, (n, c)) for p, (n, c) in microarray_platforms.items()],
    ]:
        mapping[patterns.lower()] = (name, cat)

    _PLATFORM_MAP = mapping
    return mapping


# ============ 核心推断函数 ============


def normalize_text(text: str) -> str:
    """标准化文本：小写 + 去除多余空格"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower()).strip()


def infer_disease(text: str) -> tuple[str | None, float]:
    """从文本推断疾病

    Returns:
        (canonical_disease_name, confidence) 或 (None, 0.0)
    """
    if not text:
        return None, 0.0

    normalized = normalize_text(text)
    terms = _load_disease_terms()

    for pattern, canonical, weight in terms:
        # 使用词边界匹配
        if re.search(r"\b" + re.escape(pattern) + r"\b", normalized):
            return canonical, weight

    return None, 0.0


def infer_organ(text: str) -> tuple[str | None, float]:
    """从文本推断器官

    Returns:
        (canonical_organ_name, confidence) 或 (None, 0.0)
    """
    if not text:
        return None, 0.0

    normalized = normalize_text(text)
    terms = _load_organ_terms()

    for pattern, canonical, weight in terms:
        if re.search(r"\b" + re.escape(pattern) + r"\b", normalized):
            return canonical, weight

    return None, 0.0


def infer_omics(text: str) -> tuple[str | None, str, float]:
    """从文本推断组学类型和粒度

    Returns:
        (canonical_omics_name, granularity, confidence) 或 (None, "unknown", 0.0)
    """
    if not text:
        return None, "unknown", 0.0

    normalized = normalize_text(text)
    terms = _load_omics_terms()

    for pattern, canonical, granularity, weight in terms:
        if re.search(r"\b" + re.escape(pattern) + r"\b", normalized):
            return canonical, granularity, weight

    return None, "unknown", 0.0


def infer_granularity(text: str) -> tuple[str, float]:
    """从文本推断粒度（bulk / single-cell / spatial）

    优先级：
    1. 单细胞关键词（最高优先级）
    2. 空间组学关键词
    3. bulk 关键词

    Returns:
        (granularity, confidence)
    """
    if not text:
        return "unknown", 0.0

    normalized = normalize_text(text)

    # 1. 检查单细胞关键词
    sc_terms = _load_sc_terms()
    for pattern, weight in sc_terms:
        if re.search(r"\b" + re.escape(pattern) + r"\b", normalized):
            return "single-cell", weight

    # 2. 检查空间组学
    spatial_patterns = [
        "spatial transcriptomics",
        "spatial proteomics",
        "spatial metabolomics",
        "visium",
        "stereo-seq",
        "slide-seq",
        "cosmx",
        "xenium",
        "merfish",
        "seqfish",
        "imaging mass cytometry",
        "mibi",
    ]
    for pattern in spatial_patterns:
        if pattern in normalized:
            return "spatial", 0.9

    # 3. 检查 bulk 标记
    bulk_patterns = ["bulk rna", "bulk tissue", "whole tissue", "bulk sequencing"]
    for pattern in bulk_patterns:
        if pattern in normalized:
            return "bulk", 0.8

    # 4. 默认 bulk（大多数 RNA-seq 是 bulk）
    if "rna-seq" in normalized or "rna sequencing" in normalized:
        return "bulk", 0.5

    return "unknown", 0.0


def infer_platform(platform_code: str) -> tuple[str, str]:
    """推断平台对应的技术名称和类别

    Args:
        platform_code: GEO 平台代码（如 GPL12345）或平台名称

    Returns:
        (mapped_name, category)
    """
    if not platform_code:
        return "", ""

    normalized = platform_code.lower()

    # 直接匹配
    platform_map = _load_platform_map()
    for pattern, (name, category) in platform_map.items():
        if pattern in normalized:
            return name, category

    # 提取关键词匹配
    for pattern, (name, category) in platform_map.items():
        if pattern in normalized:
            return name, category

    # 如果无法映射，返回原始值
    return platform_code, "Unknown"


def rule_infer(
    title: str,
    summary: str = "",
    overall_design: str = "",
    platform: str = "",
) -> dict[str, Any]:
    """规则引擎主函数：从文本推断所有字段

    Args:
        title: 数据集标题
        summary: 摘要/描述
        overall_design: 实验设计描述
        platform: 平台信息

    Returns:
        推断结果字典
    """
    # 合并所有文本
    all_text = f"{title} {summary} {overall_design}".strip()

    # 疾病推断
    disease, disease_conf = infer_disease(all_text)

    # 器官推断
    organ, organ_conf = infer_organ(all_text)

    # 组学类型推断
    omics_type, omics_granularity, omics_conf = infer_omics(all_text)

    # 粒度推断（可能覆盖 omics 的粒度）
    granularity, gran_conf = infer_granularity(all_text)
    # 如果 omics 推断的粒度更具体，使用 omics 的
    if omics_type and omics_granularity != "unknown":
        granularity = omics_granularity
        gran_conf = omics_conf

    # 平台映射
    mapped_platform, platform_category = infer_platform(platform)

    # 确定推断来源
    sources: dict[str, str] = {}
    if disease:
        sources["disease"] = "rule_engine"
    if organ:
        sources["organ"] = "rule_engine"
    if omics_type:
        sources["omics_type"] = "rule_engine"
    if granularity != "unknown":
        sources["granularity"] = "rule_engine"
    if mapped_platform and mapped_platform != platform:
        sources["platform"] = "rule_engine"

    return {
        "disease": disease,
        "disease_confidence": disease_conf,
        "organ": organ,
        "organ_confidence": organ_conf,
        "omics_type": omics_type,
        "omics_granularity": granularity,
        "omics_confidence": omics_conf,
        "granularity_confidence": gran_conf,
        "platform_mapped": mapped_platform,
        "platform_category": platform_category,
        "sources": sources,
    }


# ============ GPL 平台查询（通过 NCBI E-utilities）============

_GPL_NAME_CACHE: dict[str, str] = {}  # GPL ID -> platform name


def query_gpl_platform(gpl_id: str) -> str:
    """查询 GPL ID 对应的平台名称

    通过 NCBI E-utilities 查询 GPL 数据库获取平台名称。

    GPL ID 的 UID 规律：GPL{编号} → UID = 1{编号}（7位数字）
    例如：GPL24676 → UID = 100024676

    Args:
        gpl_id: GEO 平台 ID（如 GPL24676、24676）

    Returns:
        平台名称（如 "Illumina NovaSeq 6000"），查询失败则返回原始 ID
    """
    global _GPL_NAME_CACHE

    if not gpl_id:
        return ""

    # 标准化 GPL ID（去除 GPL 前缀，转为纯数字）
    normalized_id = gpl_id.upper().replace("GPL", "")
    # 确保是数字
    try:
        num = int(normalized_id)
    except ValueError:
        return gpl_id

    cache_key = f"GPL{num}"

    # 检查缓存
    if cache_key in _GPL_NAME_CACHE:
        return _GPL_NAME_CACHE[cache_key]

    try:
        import ssl

        # 创建 SSL context（禁用验证）
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # NCBI E-utilities
        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

        # GPL UID 规律：1 + 8位数字（补零到8位）
        # 例如：24676 → 100024676 (1 + 00024676)
        gpl_uid = f"1{num:08d}"
        summary_params = {"db": "gds", "id": gpl_uid, "retmode": "json"}
        request = urllib.request.Request(
            f"{base_url}/esummary.fcgi?{urllib.parse.urlencode(summary_params)}"
        )
        with urllib.request.urlopen(request, timeout=10, context=ssl_context) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("result", {}).get(gpl_uid, {})
            platform_name = result.get("title", "")

            if platform_name:
                _GPL_NAME_CACHE[cache_key] = platform_name
                return platform_name

    except Exception:
        pass

    # 查询失败，返回原始 ID
    _GPL_NAME_CACHE[cache_key] = gpl_id
    return gpl_id
