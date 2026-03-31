"""主题搜索报告生成器

生成 Markdown 格式的主题搜索报告，包含统计摘要和数据集列表。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from sra_search.data_store.database import Database
from sra_search.topic_manager.topic import TopicDefinition


class ReportBuilder:
    """主题搜索报告生成器"""

    def __init__(self, db: Database):
        self.db = db

    def build_topic_report(
        self,
        topic: TopicDefinition,
        output_path: Optional[str] = None,
    ) -> str:
        """生成主题搜索报告

        Args:
            topic: 主题定义
            output_path: 输出文件路径（可选）

        Returns:
            Markdown 格式的报告内容
        """
        # 获取主题下的数据集
        datasets = self.db.get_topic_datasets(topic.topic_id)
        total_datasets = len(datasets)

        # 统计
        from sra_search.review_manager.filters import ReviewFilters
        filters = ReviewFilters(self.db)
        summary = filters.get_review_summary(topic.topic_id)

        # 构建报告
        lines = []

        # 标题
        lines.append(f"# Topic Report: {topic.name}")
        lines.append("")

        # 描述
        if topic.description:
            lines.append(f"**Description:** {topic.description}")
            lines.append("")

        # 搜索参数
        lines.append("## Search Parameters")
        lines.append("")
        lines.append(f"- **Diseases:** {', '.join(topic.diseases) or 'Not specified'}")
        lines.append(f"- **Organs:** {', '.join(topic.organs) or 'Not specified'}")
        lines.append(f"- **Omics types:** {', '.join(topic.omics_types) or 'All'}")
        lines.append(f"- **Species:** {', '.join(topic.species) or 'All'}")
        lines.append(f"- **Keywords used:** {len(topic.keywords_used)} keyword combinations")
        lines.append(f"- **Report generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # 统计摘要
        lines.append("## Statistics")
        lines.append("")
        lines.append(f"| Metric | Count |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total datasets found | {total_datasets} |")
        lines.append(f"| Pending review | {summary['pending']} |")
        lines.append(f"| Approved | {summary['approved']} |")
        lines.append(f"| Marked irrelevant | {summary['irrelevant']} |")
        lines.append(f"| Deleted | {summary['deleted']} |")
        lines.append("")

        # 可用性统计
        available_count = sum(1 for d in datasets if d.get("availability_status") == "available")
        unavailable_count = sum(1 for d in datasets if d.get("availability_status") == "unavailable")
        restricted_count = sum(1 for d in datasets if d.get("availability_status") == "restricted")
        controlled_count = sum(1 for d in datasets if d.get("access_type") == "controlled")

        lines.append("## Availability")
        lines.append("")
        lines.append(f"| Status | Count |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Available | {available_count} |")
        lines.append(f"| Unavailable | {unavailable_count} |")
        lines.append(f"| Restricted | {restricted_count} |")
        lines.append(f"| dbGaP controlled | {controlled_count} |")
        lines.append(f"| Unverified | {total_datasets - available_count - unavailable_count - restricted_count} |")
        lines.append("")

        # 数据集列表
        lines.append("## Dataset List")
        lines.append("")
        lines.append("| GSE ID | Title | Organism | Omics Type | Samples | Platform | Availability | Review |")
        lines.append("|--------|-------|----------|------------|---------|----------|-------------|--------|")

        for ds in datasets:
            gse_id = ds.get("gse_id", "")
            title = (ds.get("title") or "")[:50]
            organism = ds.get("organism", "")
            omics = ds.get("omics_type", "")
            samples = ds.get("sample_count", 0) or ""
            platform = (ds.get("platform") or "")[:20]
            avail = ds.get("availability_status", "unverified")
            review = ds.get("review_status", "pending")

            # 状态标记
            avail_mark = {"available": "[OK]", "unavailable": "[X]", "restricted": "[!!]"}.get(avail, "[?]")
            review_mark = {"approved": "[OK]", "irrelevant": "[--]", "deleted": "[DEL]", "pending": "[...]"}.get(review, "[?]")

            lines.append(f"| {gse_id} | {title} | {organism} | {omics} | {samples} | {platform} | {avail_mark} | {review_mark} |")

        lines.append("")

        # 关键词列表
        if topic.keywords_used:
            lines.append("## Keywords Used")
            lines.append("")
            lines.append(f"Total: {len(topic.keywords_used)} combinations")
            lines.append("")
            lines.append("<details>")
            lines.append("<summary>Click to expand</summary>")
            lines.append("")
            for kw in topic.keywords_used:
                lines.append(f"- {kw}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        report = "\n".join(lines)

        # 写入文件
        if output_path:
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(report)
                logger.info(f"Report saved to {output_path}")
            except Exception as e:
                logger.error(f"Failed to save report: {e}")

        return report

    def build_summary_report(self) -> str:
        """生成全局数据集摘要报告"""
        conn = self.db.get_connection()
        cursor = conn.cursor()

        lines = []
        lines.append("# SRA_search Dataset Summary")
        lines.append("")
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # 总体统计
        cursor.execute("SELECT COUNT(*) FROM datasets")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM topics")
        topic_count = cursor.fetchone()[0]

        lines.append("## Overview")
        lines.append("")
        lines.append(f"- Total datasets: {total}")
        lines.append(f"- Total topics: {topic_count}")
        lines.append("")

        # 按可用性
        cursor.execute("""
            SELECT availability_status, COUNT(*) as cnt
            FROM datasets GROUP BY availability_status
        """)
        lines.append("## By Availability")
        lines.append("")
        lines.append("| Status | Count |")
        lines.append("|--------|-------|")
        for row in cursor.fetchall():
            lines.append(f"| {row['availability_status']} | {row['cnt']} |")
        lines.append("")

        # 按物种
        cursor.execute("""
            SELECT organism, COUNT(*) as cnt
            FROM datasets WHERE organism IS NOT NULL AND organism != ''
            GROUP BY organism ORDER BY cnt DESC LIMIT 10
        """)
        lines.append("## Top Organisms")
        lines.append("")
        lines.append("| Organism | Count |")
        lines.append("|----------|-------|")
        for row in cursor.fetchall():
            lines.append(f"| {row['organism']} | {row['cnt']} |")
        lines.append("")

        return "\n".join(lines)
