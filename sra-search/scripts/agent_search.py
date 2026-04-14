#!/usr/bin/env python3
"""
SRA Search Agent Script

用于 Agent 调用的 SRA Search 接口脚本。
支持通过命令行参数调用 sra-search 并返回结构化结果。

Usage:
    python agent_search.py "gout scRNA-seq" --llm --format json
    python agent_search.py "diabetes RNA-seq" --sources geo --retmax 30
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from sra_search.config import Settings
from sra_search.search_engine.aggregator import Aggregator
from sra_search.converter import records_to_search_result_with_llm
from sra_search.schema import SearchResult, DatasetSchema


async def search(
    query: str,
    sources: list[str] | None = None,
    retmax: int = 50,
    organism: str | None = None,
    llm: bool = False,
    llm_top_k: int = 20,
    summarize: bool = False,
    analyze_query: bool = False,
    format: str = "table",
    top: int | None = None,
) -> dict:
    """
    执行搜索并返回结果

    Args:
        query: 搜索关键词
        sources: 数据源列表 ['geo', 'sra', 'pubmed', 'bioproject']
        retmax: 最大返回数量
        organism: 物种过滤
        llm: 是否使用 LLM 排序
        llm_top_k: LLM 评分的前 N 个结果
        summarize: 是否生成摘要
        analyze_query: 是否显示查询意图分析
        format: 输出格式 ['table', 'json', 'id-list']
        top: 只返回前 N 个结果

    Returns:
        包含搜索结果的字典
    """
    settings = Settings()
    aggregator = Aggregator(settings)

    # 执行搜索
    records = await aggregator.search(
        query=query,
        sources=sources,
        retmax=retmax,
        organism=organism,
        strict_scrna=False,
    )

    if not records:
        return {
            "query": query,
            "count": 0,
            "results": [],
            "message": "No results found",
        }

    # LLM 处理
    if llm and settings.llm_api_key:
        records = await records_to_search_result_with_llm(
            records=records,
            query=query,
            top_k=llm_top_k,
            summarize=summarize,
            analyze_query=analyze_query,
        )
    else:
        # 转换为标准格式
        from sra_search.converter import records_to_search_result
        records = records_to_search_result(records, query=query)

    # 限制返回数量
    if top:
        records = records[:top]

    # 格式化输出
    if format == "json":
        result = {
            "query": query,
            "count": len(records),
            "sources": sources or ["geo", "sra", "pubmed", "bioproject"],
            "organism": organism,
            "llm_enabled": llm and bool(settings.llm_api_key),
            "results": [
                {
                    "gse_id": r.gse_id,
                    "title": r.title,
                    "summary": r.summary[:200] + "..." if r.summary and len(r.summary) > 200 else r.summary,
                    "organism": r.organism,
                    "omics_type": r.omics_type,
                    "samples": r.samples,
                    "pubmed_id": r.pubmed_id,
                    "bioproject_id": r.bioproject_id,
                    "relevance_score": r.relevance_score,
                    "single_cell": r.single_cell,
                }
                for r in records
            ],
        }
    elif format == "id-list":
        gse_ids = [r.gse_id for r in records if r.gse_id]
        result = {
            "query": query,
            "count": len(gse_ids),
            "gse_ids": gse_ids,
        }
    else:
        # table 格式返回原始 records
        result = {
            "query": query,
            "count": len(records),
            "results": records,
        }

    return result


def main():
    parser = argparse.ArgumentParser(
        description="SRA Search Agent Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python agent_search.py "gout scRNA-seq" --llm --format json
    python agent_search.py "diabetes RNA-seq" --sources geo --retmax 30
    python agent_search.py "liver fibrosis" --format id-list
        """,
    )

    parser.add_argument("query", type=str, help="搜索关键词")
    parser.add_argument(
        "--sources",
        type=str,
        action="append",
        choices=["geo", "sra", "pubmed", "bioproject"],
        help="指定数据源（可多次指定）",
    )
    parser.add_argument("--retmax", type=int, default=50, help="最大返回数量 (default: 50)")
    parser.add_argument("--organism", type=str, default=None, help="物种过滤 (e.g., human, mouse)")
    parser.add_argument("--llm", action="store_true", help="启用 LLM 语义排序")
    parser.add_argument("--llm-top-k", type=int, default=20, help="LLM 评分的前 N 个结果 (default: 20)")
    parser.add_argument("--summarize", action="store_true", help="生成数据集摘要")
    parser.add_argument("--analyze-query", action="store_true", help="显示查询意图分析（调试用）")
    parser.add_argument(
        "--format",
        type=str,
        default="json",
        choices=["table", "json", "id-list"],
        help="输出格式 (default: json)",
    )
    parser.add_argument("--top", type=int, default=None, help="只返回前 N 个结果")

    args = parser.parse_args()

    # 执行搜索
    result = asyncio.run(search(
        query=args.query,
        sources=args.sources,
        retmax=args.retmax,
        organism=args.organism,
        llm=args.llm,
        llm_top_k=args.llm_top_k,
        summarize=args.summarize,
        analyze_query=args.analyze_query,
        format=args.format,
        top=args.top,
    ))

    # 输出结果
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
