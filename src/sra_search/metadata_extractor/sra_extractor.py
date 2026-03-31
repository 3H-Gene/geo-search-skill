"""SRA 元数据提取器

从 SRA 搜索结果（SRAResult）中提取并丰富 DatasetRecord 的元数据字段。
支持通过 pysradb 获取更详细的 SRA 元数据。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from loguru import logger

from sra_search.metadata_extractor.models import DatasetRecord
from sra_search.metadata_extractor.normalizer import (
    extract_disease_from_text,
    extract_organ_from_text,
    normalize_organism,
    normalize_platform,
)


# library_strategy 到粗分类组学类型的映射
_LIBRARY_STRATEGY_MAP: Dict[str, str] = {
    "RNA-Seq": "RNA-seq",
    "RNA-SEQ": "RNA-seq",
    "WGS": "WGS",
    "WXS": "WXS",
    "WES": "WES",
    "Whole Genome Sequencing": "WGS",
    "Whole Exome Sequencing": "WES",
    "ChIP-Seq": "ChIP-seq",
    "CHIP-SEQ": "ChIP-seq",
    "ATAC-Seq": "ATAC-seq",
    "ATAC-SEQ": "ATAC-seq",
    "Bisulfite-Seq": "Bisulfite-seq",
    "Methylation-Seq": "Methylation-seq",
    "Ribo-Seq": "Ribo-seq",
    "RIBO-SEQ": "Ribo-seq",
    "CLIP-Seq": "CLIP-seq",
    "miRNA-Seq": "miRNA-seq",
    "MIRNA-SEQ": "miRNA-seq",
    "ssRNA-seq": "ssRNA-seq",
    "DNase-Hypersensitivity": "DNase-seq",
    "MNase-Seq": "MNase-seq",
    "Synthetic Long-Read Sequencing": "Synthetic Long-Read Sequencing",
    "Ampliseq": "Amplicon-seq",
    "Targeted Capture": "Targeted Sequencing",
    "OTHER": "Other",
    "Pool-Seq": "Pool-seq",
    "EST": "EST",
    "FL-cDNA": "FL-cDNA",
    "CTS": "CTS",
    "Hi-C": "Hi-C",
    "RACE": "RACE",
    "Validation Sequencing": "Validation Sequencing",
    "ncRNA-Seq": "ncRNA-seq",
    "PROT-SEQ": "Proteomics",
    "Proteomics": "Proteomics",
}

# library_source 到粒度的映射
_LIBRARY_SOURCE_GRANULARITY: Dict[str, str] = {
    "TRANSCRIPTOMIC SINGLE CELL": "single_cell",
    "TRANSCRIPTOMIC": "bulk",
    "GENOMIC SINGLE CELL": "single_cell",
    "GENOMIC": "bulk",
    "EPIGENOMIC": "bulk",
    "METAGENOMIC": "bulk",
    "METATRANSCRIPTOMIC": "bulk",
    "OTHER": "unknown",
    "SYNTHETIC": "unknown",
    "PROTEOMIC": "bulk",
    "VIRAL RNA": "bulk",
}


class SRAExtractor:
    """SRA 元数据提取器

    功能：
    1. 将 SRAResult 转换为 DatasetRecord（可能无 GSE）
    2. 从 library_strategy 推断组学类型
    3. 从 library_source 推断数据粒度
    4. 检测 dbGaP 受控访问
    5. 通过 pysradb 获取更详细元数据（可选）
    """

    def __init__(self):
        pass

    def extract(self, sra_result) -> DatasetRecord:
        """从 SRAResult 提取 DatasetRecord

        Args:
            sra_result: SRASearcher 返回的 SRAResult 对象

        Returns:
            填充了丰富元数据的 DatasetRecord
        """
        # 确定主键
        gse_ids = list(sra_result.gse_ids) if sra_result.gse_ids else []
        has_gse = bool(gse_ids)
        if has_gse:
            primary_key = gse_ids[0]
        else:
            primary_key = f"SRP:{sra_result.srp_id}"

        # 基础字段
        title = sra_result.title or ""
        organism_raw = sra_result.organism or ""
        instrument = sra_result.instrument or ""
        library_strategy = sra_result.library_strategy or ""
        library_source = sra_result.library_source or ""
        library_selection = sra_result.library_selection or ""

        # 标准化
        organism = normalize_organism(organism_raw)
        platform = normalize_platform(instrument)

        # 推断组学类型
        omics_type = self._infer_omics_type(
            library_strategy=library_strategy,
            title=title,
            platform=platform,
        )
        omics_type_json = f'["{omics_type}"]' if omics_type else ""

        # 推断组学粒度
        omics_granularity = self._infer_granularity(
            library_source=library_source,
            title=title,
            sample_count=sra_result.sample_count,
        )

        # 提取疾病和器官
        disease = extract_disease_from_text(title) or ""
        organ = extract_organ_from_text(title) or ""

        # dbGaP 受控访问检测
        access_type = self._detect_access_type(sra_result)

        # 构建 record
        record = DatasetRecord(
            gse_id=primary_key,
            title=title,
            organism=organism,
            disease=disease,
            organ=organ,
            omics_type=omics_type_json,
            omics_granularity=omics_granularity,
            sample_count=sra_result.sample_count,
            platform=platform,
            sra_ids=[sra_result.srp_id] if sra_result.srp_id else [],
            bioproject_ids=list(sra_result.bioproject_ids) if sra_result.bioproject_ids else [],
            has_gse=has_gse,
            access_type=access_type,
        )

        record.update_hash()
        return record

    def enrich_from_pysradb(
        self,
        record: DatasetRecord,
        metadata_df=None,
    ) -> DatasetRecord:
        """通过 pysradb 获取的详细 SRA 元数据丰富 DatasetRecord

        Args:
            record: 已有的 DatasetRecord
            metadata_df: pysradb.sra_metadata() 返回的 DataFrame

        Returns:
            丰富后的 DatasetRecord
        """
        if metadata_df is None or metadata_df.empty:
            return record

        # 查找对应行
        srp_id = record.sra_ids[0] if record.sra_ids else ""
        if not srp_id:
            return record

        # 清理 SRP ID 格式 (SRP123 -> SRP000123)
        mask = metadata_df["study_accession"].str.lower() == srp_id.lower()
        rows = metadata_df[mask]
        if rows.empty:
            # 尝试去除 SRP: 前缀
            clean_srp = srp_id.replace("SRP:", "")
            mask = metadata_df["study_accession"].str.lower() == clean_srp.lower()
            rows = metadata_df[mask]

        if rows.empty:
            return record

        row = rows.iloc[0]

        # 补充字段（不覆盖已有值）
        if not record.title:
            study_title = row.get("study_title", "")
            if study_title and str(study_title) != "nan":
                record.title = str(study_title)

        if not record.organism:
            org = row.get("organism", "")
            if org and str(org) != "nan":
                record.organism = normalize_organism(str(org))

        if not record.abstract:
            abstract = row.get("study_abstract", "")
            if abstract and str(abstract) != "nan":
                record.abstract = str(abstract)

        # 补充 platform
        instrument = row.get("instrument", "")
        if instrument and str(instrument) != "nan" and not record.platform:
            record.platform = normalize_platform(str(instrument))

        # 补充 library_strategy / library_source
        lib_strategy = row.get("library_strategy", "")
        if lib_strategy and str(lib_strategy) != "nan" and not record.omics_type:
            omics_type = self._infer_omics_type(
                library_strategy=str(lib_strategy),
                title=record.title,
                platform=record.platform,
            )
            if omics_type:
                record.omics_type = f'["{omics_type}"]'

        lib_source = row.get("library_source", "")
        if lib_source and str(lib_source) != "nan" and record.omics_granularity == "unknown":
            record.omics_granularity = self._infer_granularity(
                library_source=str(lib_source),
                title=record.title,
                sample_count=record.sample_count,
            )

        # 补充样本数（pysradb 通常提供更准确的值）
        total_runs = row.get("total_runs", "")
        if total_runs and str(total_runs) != "nan":
            run_count = int(float(str(total_runs)))
            if run_count > record.sample_count:
                record.sample_count = run_count

        # 补充 BioProject
        bioproject = row.get("bioproject", "")
        if bioproject and str(bioproject) != "nan":
            bp = str(bioproject)
            if bp not in record.bioproject_ids:
                record.bioproject_ids.append(bp)

        # 重新提取疾病和器官
        text = f"{record.title} {record.abstract}"
        if not record.disease:
            record.disease = extract_disease_from_text(text) or ""
        if not record.organ:
            record.organ = extract_organ_from_text(text) or ""

        record.update_hash()
        return record

    def _infer_omics_type(
        self,
        library_strategy: str = "",
        title: str = "",
        platform: str = "",
    ) -> str:
        """根据 library_strategy 和上下文推断组学类型

        优先级：library_strategy > 标题关键词 > 平台关键词

        Returns:
            标准化的组学类型字符串，如 "RNA-seq", "ChIP-seq" 等
        """
        if library_strategy:
            normalized = _LIBRARY_STRATEGY_MAP.get(library_strategy, "")
            if normalized:
                return normalized

        # 从标题关键词推断
        title_lower = (title or "").lower()
        title_patterns = {
            "RNA-seq": ["rna-seq", "rna seq", "transcriptom"],
            "scRNA-seq": ["single-cell rna", "scrna", "single cell rna"],
            "spatial transcriptomics": ["spatial transcriptom", "visium", "merfish", "slide-seq", "stereo-seq"],
            "ATAC-seq": ["atac-seq", "atac seq", "chromatin access"],
            "ChIP-seq": ["chip-seq", "chip seq", "chromatin immunoprecip"],
            "WGS": ["whole genome sequencing", "wgs"],
            "WES": ["whole exome sequencing", "wes", "exome seq"],
            "proteomics": ["proteomic", "mass spectrometry", "lc-ms"],
            "methylation": ["methylation", "bisulfite", "epigenetic"],
            "Hi-C": ["hi-c", "chromatin conformation", "3d genome"],
            "CRISPR": ["crispr", "sgrna", "gene editing"],
        }

        for omics_type, patterns in title_patterns.items():
            for pattern in patterns:
                if pattern in title_lower:
                    return omics_type

        # 从平台推断
        plat_lower = (platform or "").lower()
        platform_patterns = {
            "ATAC-seq": ["atac"],
            "spatial transcriptomics": ["visium", "xenium", "geomx"],
        }
        for omics_type, patterns in platform_patterns.items():
            for pattern in patterns:
                if pattern in plat_lower:
                    return omics_type

        return ""

    def _infer_granularity(
        self,
        library_source: str = "",
        title: str = "",
        sample_count: int = 0,
    ) -> str:
        """推断组学数据粒度

        Returns:
            "single_cell" / "bulk" / "spatial" / "unknown"
        """
        # 1. library_source 直接映射
        if library_source:
            upper = library_source.upper()
            for key, granularity in _LIBRARY_SOURCE_GRANULARITY.items():
                if key in upper:
                    return granularity

        # 2. 标题关键词
        title_lower = (title or "").lower()
        spatial_keywords = [
            "spatial transcriptom", "visium", "merfish", "slide-seq",
            "stereo-seq", "spatially resolved", "xenium", "geomx",
            "spatial omics", "spatial gene expression",
        ]
        for kw in spatial_keywords:
            if kw in title_lower:
                return "spatial"

        sc_keywords = [
            "single-cell", "single cell", "scrna", "scRNA-seq",
            "10x genomics", "chromium", "drop-seq", "smart-seq",
            "cite-seq", "cellranger", "umi",
        ]
        for kw in sc_keywords:
            if kw in title_lower:
                return "single_cell"

        # 3. 样本数启发式
        if sample_count > 1000:
            # 极大样本数暗示单细胞
            return "single_cell"

        return "unknown"

    @staticmethod
    def _detect_access_type(sra_result) -> str:
        """检测 SRA 数据集的访问权限类型

        通过 accession_auth、study_alias 等字段判断是否为 dbGaP 受控数据。

        Args:
            sra_result: SRAResult 对象

        Returns:
            "controlled" / "public" / "unknown"
        """
        # 检查 accession_auth 字段
        auth = getattr(sra_result, "accession_auth", "") or ""
        if auth and "controlled" in auth.lower():
            return "controlled"
        if auth and "dbgap" in auth.lower():
            return "controlled"

        # 检查 study_alias 是否包含 dbGaP 标识
        alias = getattr(sra_result, "study_alias", "") or ""
        if "dbgap" in alias.lower() or "phs" in alias.lower():
            return "controlled"

        # 检查 SRP 前缀 (常规 SRP 为公开数据)
        srp_id = getattr(sra_result, "srp_id", "") or ""
        if srp_id.startswith("SRP"):
            return "public"

        # 其他前缀（ERP/DRP）也可能是公开的
        if srp_id:
            return "public"

        return "unknown"

    @staticmethod
    def extract_srp_from_text(text: str) -> List[str]:
        """从文本中提取所有 SRA Study 编号

        支持格式: SRP/ERP/DRP/SRPX + 数字

        Args:
            text: 自由文本

        Returns:
            SRP 编号列表（去重）
        """
        if not text:
            return []
        pattern = r"\b(?:SRP|ERP|DRP|SRPX)\d{4,}\b"
        return list(dict.fromkeys(re.findall(pattern, text, re.IGNORECASE)))

    @staticmethod
    def extract_srr_from_text(text: str) -> List[str]:
        """从文本中提取所有 SRA Run 编号"""
        if not text:
            return []
        pattern = r"\bSRR\d{4,}\b"
        return list(dict.fromkeys(re.findall(pattern, text, re.IGNORECASE)))
