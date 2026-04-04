"""灵活导出引擎

支持按主题、字段、审核状态、可用性、访问权限、组学粒度等多维度筛选导出。
导出格式：JSON / TSV / CSV / plain / script（下载脚本）
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from loguru import logger

from sra_search.data_store.database import Database, get_database
from sra_search.metadata_extractor.models import DatasetRecord

# 可导出字段列表
EXPORTABLE_FIELDS = [
    "gse_id", "title", "pubmed_ids", "sra_ids", "bioproject_ids",
    "organism", "disease", "organ", "omics_type", "omics_granularity",
    "sample_count", "platform", "publication_date", "journal", "abstract",
    "keywords", "availability_status", "availability_note", "access_type",
    "has_gse",
]


class Exporter:
    """灵活导出引擎"""

    def __init__(self, db: Database | None = None):
        self.db = db or get_database()

    def export(
        self,
        output_path: str,
        format: str = "tsv",
        fields: list[str] | None = None,
        topic: str | None = None,
        review_status: str | None = None,
        availability: str | None = None,
        access_type: str | None = None,
        min_samples: int | None = None,
        granularity: str | None = None,
        organism: str | None = None,
        limit: int = 10000,
    ) -> int:
        """导出数据集到文件

        Args:
            output_path: 输出文件路径
            format: 导出格式 (json/tsv/csv/plain/script)
            fields: 导出字段列表（None=全部）
            topic: 主题名称过滤
            review_status: 审核状态过滤
            availability: 可用性过滤
            access_type: 访问权限过滤
            min_samples: 最小样本数过滤
            granularity: 组学粒度过滤
            organism: 物种过滤
            limit: 最大导出数

        Returns:
            导出的记录数
        """
        # 解析 topic_id
        topic_id = None
        if topic:
            topic_id = self._resolve_topic_name(topic)
            if topic_id is None:
                logger.warning(f"Topic '{topic}' not found, exporting all datasets")

        # 获取数据
        datasets = self.db.list_datasets(
            topic_id=topic_id,
            review_status=review_status,
            availability=availability,
            access_type=access_type,
            granularity=granularity,
            organism=organism,
            limit=limit,
        )

        # min_samples 过滤
        if min_samples is not None:
            datasets = [d for d in datasets if d.sample_count >= min_samples]

        if not datasets:
            logger.warning("No datasets to export")
            return 0

        # 确定导出字段
        export_fields = fields or EXPORTABLE_FIELDS

        # 根据格式导出
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            self._export_json(datasets, export_fields, out_path)
        elif format in ("tsv", "csv"):
            self._export_tabular(datasets, export_fields, out_path, format)
        elif format == "plain":
            self._export_plain(datasets, export_fields, out_path)
        elif format == "script":
            self._export_script(datasets, out_path)
        else:
            raise ValueError(f"Unsupported export format: {format}")

        logger.info(f"Exported {len(datasets)} datasets to {output_path} (format={format})")
        return len(datasets)

    def _resolve_topic_name(self, topic_name: str) -> str | None:
        """根据主题名称查找 topic_id"""
        topics = self.db.list_topics()
        for t in topics:
            if t.name == topic_name:
                return t.topic_id
        return None

    def _export_json(
        self,
        datasets: list[DatasetRecord],
        fields: list[str],
        output_path: Path,
    ) -> None:
        """导出为 JSON"""
        records = []
        for ds in datasets:
            row = {}
            for f in fields:
                val = getattr(ds, f, "")
                # JSON 数组字段已经是列表
                row[f] = val
            records.append(row)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    def _export_tabular(
        self,
        datasets: list[DatasetRecord],
        fields: list[str],
        output_path: Path,
        format: str,
    ) -> None:
        """导出为 TSV 或 CSV"""
        delimiter = "\t" if format == "tsv" else ","

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            # Header
            writer.writerow(fields)
            # Data
            for ds in datasets:
                row = []
                for field_name in fields:
                    val = getattr(ds, field_name, "")
                    if isinstance(val, list):
                        val = ";".join(str(v) for v in val)
                    row.append(val)
                writer.writerow(row)

    def _export_plain(
        self,
        datasets: list[DatasetRecord],
        fields: list[str],
        output_path: Path,
    ) -> None:
        """导出为纯文本（每行一个值，仅适用于单字段）"""
        if len(fields) != 1:
            logger.warning("Plain format supports only one field, using first field")
        field_name = fields[0] if fields else "gse_id"

        with open(output_path, "w", encoding="utf-8") as f:
            for ds in datasets:
                val = getattr(ds, field_name, "")
                if isinstance(val, list):
                    for v in val:
                        f.write(f"{v}\n")
                else:
                    f.write(f"{val}\n")

    def _export_script(
        self,
        datasets: list[DatasetRecord],
        output_path: Path,
    ) -> None:
        """导出为下载脚本（prefetch + fasterq-dump + wget）

        生成可直接在服务器上运行的 .sh 脚本。
        """
        lines = [
            "#!/bin/bash",
            "# Auto-generated download script by SRA_search",
            f"# Generated: {self._now_iso()}",
            f"# Total datasets: {len(datasets)}",
            "#",
            "# Prerequisites:",
            "#   - sra-tools (prefetch, fasterq-dump): conda install -c bioconda sra-tools",
            "#   - wget (for GEO supplementary files)",
            "#",
            "# Usage: bash download_data.sh",
            "#",
            "",
            "set -euo pipefail",
            "",
            'WORKDIR="./sra_download"',
            'mkdir -p "$WORKDIR"',
            'cd "$WORKDIR"',
            "",
        ]

        public_datasets = []
        controlled_datasets = []

        for ds in datasets:
            if ds.access_type == "controlled":
                controlled_datasets.append(ds)
            else:
                public_datasets.append(ds)

        # Public SRA data
        if public_datasets:
            lines.append("# ===== Public SRA Data (fasterq-dump) =====")
            for ds in public_datasets:
                sra_ids = ds.sra_ids
                if not sra_ids:
                    lines.append(f"# {ds.gse_id}: No SRA IDs found")
                    continue

                lines.append(f"\n# {ds.gse_id}: {ds.title}")
                for srp in sra_ids:
                    lines.append(f"echo 'Downloading {srp}...'")
                    lines.append(f"prefetch {srp}")
                    lines.append(f"fasterq-dump --split-files --outdir {srp} {srp}")

        # GEO supplementary files
        if public_datasets:
            lines.append("\n\n# ===== GEO Supplementary Files =====")
            for ds in public_datasets:
                if not ds.has_gse or ds.gse_id.startswith("SRP:"):
                    continue
                gse = ds.gse_id
                prefix = gse[:-3]
                lines.append(f"\n# {gse}: {ds.title}")
                lines.append(f"mkdir -p geo/{gse}")
                lines.append(f"wget -r -np -nH --cut-dirs=4 -A '*_RAW*,*processed*,*.gz,*.zip,*.tar' "
                             f"https://ftp.ncbi.nlm.nih.gov/geo/series/{prefix}nnn/{gse}/suppl/ "
                             f"-O geo/{gse}/")

        # Controlled data
        if controlled_datasets:
            lines.append("\n\n# ===== Controlled/Access-Required Data =====")
            lines.append("# NOTE: The following datasets require dbGaP or other authorized access.")
            lines.append("# You need to apply for access before downloading.")
            lines.append("# dbGaP application: https://dbgap.ncbi.nlm.nih.gov/aa/wga.cgi?page=login")
            lines.append("")
            for ds in controlled_datasets:
                lines.append(f"# {ds.gse_id}: {ds.title} [CONTROLLED ACCESS]")
                lines.append(f"# Access type: {ds.access_type}")
                if ds.sra_ids:
                    for srp in ds.sra_ids:
                        lines.append(f"# {srp} - requires dbGaP authorization")
                lines.append("")

        lines.append('\necho "Download complete!"')

        script = "\n".join(lines)
        with open(output_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(script)

        logger.info(f"Download script generated: {output_path}")

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
