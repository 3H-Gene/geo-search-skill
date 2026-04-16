"""SRA_search 命令行工具

基于 Click 的 CLI 入口，整合所有功能模块。

命令列表：
  sra-search search    关键词搜索数据集
  sra-search topic     主题式搜索
  sra-search list      列表浏览已存储的数据集
  sra-search convert   编号转换
  sra-search export    按需导出到文件
  sra-search show      查看单条数据集详情
  sra-search review    数据集审核操作
  sra-search check     数据集可用性检查
  sra-search update    手动触发更新
  sra-search config    配置管理
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import click
from loguru import logger

from sra_search.config import get_settings
from sra_search.utils.logger import setup_logger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_async(coro):
    """在同步 Click 上下文中运行异步协程"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


# CSV 输出字段顺序（模块级常量，供 search 命令使用）
_CSV_FIELDS = [
    "rank", "gse_id", "title", "organism", "tissue", "disease",
    "data_type", "single_cell", "granularity", "sample_count",
    "platform", "has_processed_matrix", "raw_only",
    "series_matrix_available",
    "has_perturbation", "perturbation_types",
    "pubmed_ids", "sra_ids",
    "publication_date", "relevance_score", "total_score",
    "llm_one_sentence_summary", "llm_sample_grouping",
    "llm_cell_count", "llm_relevance_reason",
]


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="详细日志输出")
@click.option("--config", "-c", type=click.Path(), help="配置文件路径")
@click.version_option(package_name="sra-search")
def main(verbose: bool = False, config: str | None = None):
    """SRA_search - 多组学数据集智能检索与管理工具"""
    if config:
        from sra_search.config import load_settings_from_file
        load_settings_from_file(config)

    level = "DEBUG" if verbose else "INFO"
    setup_logger(level=level)

    # ── NCBI 配置强制校验 ──────────────────────────────────────────────
    from sra_search.utils.validator import validate_ncbi_config

    settings = get_settings()
    validation = validate_ncbi_config(settings.ncbi_email, settings.ncbi_api_key)

    if validation.errors:
        # 校验失败：打印错误并退出
        error_msg = click.style("配置校验失败:\n", bold=True, fg="red")
        error_msg += validation.format_message()
        raise SystemExit(click.echo(error_msg, err=True))

    if validation.warnings:
        for warning in validation.warnings:
            logger.warning(warning)


@main.command()
@click.argument("keyword")
@click.option("--sources", "-s", multiple=True, type=click.Choice(["geo", "sra", "pubmed", "bioproject"]),
              help="数据源筛选（可多选）")
@click.option("--retmax", "-n", default=None, type=int, help="每源最大返回数")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json", "id-list", "csv"]),
              default="table", help="输出格式：table(表格)/json(JSON结构化)/id-list(ID列表)/csv(CSV文件)")
@click.option("--top", "-t", default=50, type=int, help="返回前 N 个结果（排序后）")
@click.option("--organism", "-o", multiple=True,
              help="生物体过滤（可多选），如 --organism human --organism mouse")
@click.option("--since", default=None, help="最早发表日期（YYYY/MM/DD），如 --since 2020/01/01")
@click.option("--until", default=None, help="最晚发表日期（YYYY/MM/DD），如 --until 2024/12/31")
@click.option("--strict-scrna", is_flag=True, default=False,
              help="启用严格 scRNA-seq 过滤（仅 SRA 源，排除 Smart-seq 等低通量方法）")
@click.option("--save/--no-save", default=True, help="是否保存到数据库")
# ── LLM 参数（V2 新增）──
@click.option("--llm/--no-llm", "use_llm", default=None, is_flag=True,
              help="V1+LLM 模式：V1 预过滤后 LLM 重排（需配置 API Key）。"
                   "需与 --llm-only 互斥）")
@click.option("--llm-only", is_flag=True, default=False,
              help="纯 LLM 模式：对所有结果做 LLM 语义评分（不经过 V1 预过滤，不限 top_k）")
@click.option("--llm-min-relevance", default=None, type=float,
              help="LLM 评分最低相关性阈值（默认 0.0，仅 relevance_score >= 此值的结果送 LLM）")
@click.option("--llm-provider", default=None, type=str,
              help="LLM 提供商：openai / anthropic / local（覆盖配置）")
@click.option("--llm-model", default=None, type=str,
              help="LLM 模型名称，如 gpt-4o-mini / claude-3-5-haiku-20241022")
@click.option("--llm-api-key", default=None, type=str, envvar="SRA_SEARCH_LLM_API_KEY",
              help="LLM API Key（也可通过 SRA_SEARCH_LLM_API_KEY 环境变量设置）")
@click.option("--llm-base-url", default=None, type=str,
              help="LLM API 代理地址（如 Ollama 或 OpenAI-compatible endpoint）")
@click.option("--llm-top-k", default=None, type=int,
              help="LLM 评分的 top_k（默认 20，只对前 N 个结果做 LLM 评分）")
@click.option("--summarize", is_flag=True, default=False,
              help="生成 LLM 自然语言摘要（需要 --llm 或配置 API Key）")
@click.option("--analyze-query", is_flag=True, default=False,
              help="显示 LLM 解析的查询意图（调试用）")
@click.option("--debug-prompts", is_flag=True, default=False,
              help="明文打印 LLM 发送的原始提示词和模型返回内容（无需 --verbose）")
def search(
    keyword: str,
    sources: tuple,
    retmax: int | None,
    fmt: str,
    top: int,
    organism: tuple,
    since: str | None,
    until: str | None,
    strict_scrna: bool,
    save: bool,
    use_llm: bool | None,
    llm_only: bool,
    llm_min_relevance: float | None,
    llm_provider: str | None,
    llm_model: str | None,
    llm_api_key: str | None,
    llm_base_url: str | None,
    llm_top_k: int | None,
    summarize: bool,
    analyze_query: bool,
    debug_prompts: bool,
):
    """关键词搜索数据集

    支持四种输出格式：
    - table: 表格形式（默认）
    - json: 标准 JSON Schema 输出（与 gse-downloader 解耦）
    - id-list: 仅 GSE ID 列表（适合管道处理）
    - csv: CSV 文件（含全量字段，适合 Excel 筛选分析）

    V2 LLM 辅助功能（三种模式）：
    - (default, 无 flag): 纯 V1 关键词模式
    - --llm: V1 预过滤 + LLM 重排（推荐，默认 top_k=20）
    - --llm-only: 纯 LLM 模式，对所有结果评分（忽略 V1 预过滤，不限 top_k）
    - --summarize: 生成自然语言摘要
    - --llm: 启用 LLM 语义评分，提升结果相关性
    - --summarize: 生成自然语言摘要
    - --analyze-query: 显示 LLM 解析的查询意图

    示例：
      sra-search search "breast cancer scRNA-seq" --format json --top 20
      sra-search search "gout single cell" --organism human --since 2022/01/01
      sra-search search "liver fibrosis" --sources geo --format id-list
      sra-search search "single cell" --organism mouse --strict-scrna
      sra-search search "gout single cell" --llm --summarize
      sra-search search "liver fibrosis" --llm --llm-provider openai --llm-model gpt-4o
      sra-search search "covid single cell" --llm-only  # 纯 LLM 模式，不限 top_k
      sra-search search "gout" --llm --llm-min-relevance 0.05  # 跳过 V1 零相关结果
      sra-search search "gout single cell" --format csv > results.csv  # 导出 CSV（Excel 可直接打开）
      sra-search search "gout single cell" --llm --format csv > results_llm.csv  # 含 LLM 分析的 CSV
    """
    import json

    # ── 输入校验与清理 ──────────────────────────────────────────────────
    from sra_search.utils.validator import validate_query_input, sanitize_query

    validation = validate_query_input(keyword)
    if validation.errors:
        error_msg = click.style("输入校验失败:\n", bold=True, fg="red")
        error_msg += validation.format_message()
        raise SystemExit(click.echo(error_msg, err=True))

    if validation.warnings:
        for warning in validation.warnings:
            logger.warning(warning)

    # 自动清理问题字符
    original_keyword = keyword
    keyword = sanitize_query(keyword)
    if keyword != original_keyword:
        logger.info(f"查询词已自动清理: {original_keyword!r} → {keyword!r}")

    # 设置 LLM prompt 调试标志
    from sra_search.llm import client as llm_client_module
    llm_client_module.llm_debug_prompts = debug_prompts

    from sra_search.converter import (
        records_to_search_result,
        records_to_search_result_with_llm,
    )
    from sra_search.search_engine.aggregator import SearchAggregator

    organisms_list = list(organism) if organism else None
    sources_list = list(sources) if sources else None
    aggregator = SearchAggregator()

    async def _do_search():
        from sra_search.search_engine.base import get_entrez_client
        client = get_entrez_client()
        try:
            results = await aggregator.search(
                keyword=keyword,
                sources=sources_list,
                retmax=retmax,
                organisms=organisms_list,
                min_date=since,
                max_date=until,
                strict_scrna=strict_scrna,
            )
            return results
        finally:
            await client.close()

    # ── 阶段 1: 多源检索 ─────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("阶段 1/3: 多源检索")
    logger.info("=" * 60)

    search_results = run_async(_do_search())

    if not search_results:
        click.echo(f"No datasets found for '{keyword}'")
        return

    # 统计各源数据
    source_stats = {}
    for r in search_results:
        src = r.match_source
        source_stats[src] = source_stats.get(src, 0) + 1

    logger.info(
        f"[检索完成] 共获取 {len(search_results)} 条唯一记录，"
        f"来源分布: {source_stats}"
    )

    records = [r.dataset for r in search_results]

    # ── 判断运行模式 ────────────────────────────────────────────────────────
    settings = get_settings()
    should_use_llm = False

    # CLI --llm/--no-llm 优先，其次看 settings.llm_enabled
    if use_llm is True:
        should_use_llm = True
    elif use_llm is False:
        should_use_llm = False
    elif settings.llm_enabled:
        should_use_llm = True

    # 如果传入了 api_key / provider，隐式启用 LLM
    if (llm_api_key or llm_provider) and use_llm is not False:
        should_use_llm = True

    # ── 阶段 2: Schema 转换与排序 ──────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("阶段 2/3: Schema 转换与排序")
    logger.info("=" * 60)

    # 输出模式提示
    if should_use_llm or llm_only:
        # 构建 LLM 客户端（CLI 参数 > 环境变量 > settings）
        _provider = llm_provider or settings.llm_provider or "openai"
        _api_key = llm_api_key or settings.llm_api_key or ""
        _model = llm_model or settings.llm_model or ""
        _base_url = llm_base_url or settings.llm_base_url or ""
        _top_k = llm_top_k or settings.llm_top_k
        _min_rel = llm_min_relevance if llm_min_relevance is not None else 0.0

        if not _api_key:
            logger.warning(
                "[模式提示] LLM 模式请求但未配置 API Key，"
                "自动降级为 V1 关键词模式"
            )
            logger.info(f"[V1 模式] 转换 {len(records)} 条记录为 Schema...")
            schema_result = records_to_search_result(records, query=keyword, top_n=top)
        else:
            from sra_search.llm.client import LLMClient
            llm_client_obj = LLMClient.from_params(
                provider=_provider,
                api_key=_api_key,
                model=_model,
                base_url=_base_url,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
            )

            _effective_model = _model or llm_client_obj.__class__.DEFAULT_MODEL if hasattr(llm_client_obj.__class__, "DEFAULT_MODEL") else (_model or "default")

            # 模式描述
            if llm_only:
                _mode_desc = f"LLM-only (min_relevance={_min_rel}, score_all=True)"
                _score_all = True
                logger.info(
                    f"[模式提示] 运行模式: 纯 LLM 模式\n"
                    f"  - 提供商: {_provider} | 模型: {_effective_model}\n"
                    f"  - 参数: min_relevance={_min_rel}, score_all=True\n"
                    f"  - 特点: 对所有结果做 LLM 语义评分（忽略 V1 预过滤）"
                )
            else:
                _mode_desc = f"V1+LLM (top_k={_top_k}, min_relevance={_min_rel})"
                _score_all = False
                logger.info(
                    f"[模式提示] 运行模式: V1 + LLM 辅助模式\n"
                    f"  - 提供商: {_provider} | 模型: {_effective_model}\n"
                    f"  - 参数: top_k={_top_k}, min_relevance={_min_rel}, concurrency={settings.llm_concurrency}\n"
                    f"  - 特点: V1 关键词预过滤 → LLM 语义重排（推荐）"
                )

            logger.info(f"  - 输入记录数: {len(records)}")
            logger.info("-" * 60)

            # 记录输入数量
            input_count = len(records)
            logger.info(f"[Step 2.1] V1 关键词转换: {input_count} 条记录 → Schema")

            schema_result = run_async(
                records_to_search_result_with_llm(
                    records=records,
                    query=keyword,
                    top_n=top,
                    llm_client=llm_client_obj,
                    enable_ranking=True,
                    enable_summary=summarize,
                    enable_query_analysis=analyze_query,
                    llm_top_k=_top_k,
                    llm_concurrency=settings.llm_concurrency,
                    llm_min_relevance=_min_rel,
                    llm_score_all=_score_all,
                )
            )

            # 输出 LLM 处理结果统计
            llm_scored = schema_result.llm_scored_count
            logger.info(f"[Step 2.2] LLM 语义评分: {llm_scored}/{input_count} 条记录完成评分")
            logger.info(f"[Step 2.3] 最终排序: {len(schema_result.results)} 条记录 (top={top})")
    else:
        logger.info(f"[V1 模式] 转换 {len(records)} 条记录为 Schema...")
        schema_result = records_to_search_result(records, query=keyword, top_n=top)
        logger.info(f"[V1 模式] 完成: {len(schema_result.results)} 条记录排序完成")

    # ── 输出查询意图（调试模式）──────────────────────────────────────────────
    if analyze_query and schema_result.llm_query_intent:
        intent = schema_result.llm_query_intent
        click.echo("\n[LLM Query Intent]", err=True)
        click.echo(f"  Disease:    {intent.get('disease', [])}", err=True)
        click.echo(f"  Technology: {intent.get('technology', [])}", err=True)
        click.echo(f"  Organism:   {intent.get('organism', [])}", err=True)
        click.echo(f"  Tissue:     {intent.get('tissue', [])}", err=True)
        click.echo(f"  Intent:     {intent.get('intent_summary', '')}", err=True)
        click.echo("", err=True)

    # ── LLM 摘要输出 ──────────────────────────────────────────────────────
    if schema_result.llm_summary:
        click.echo("\n" + "=" * 60)
        click.echo("[LLM Summary]")
        click.echo("=" * 60)
        click.echo(schema_result.llm_summary)
        click.echo("=" * 60 + "\n")

    # ── 阶段 3: 结果输出 ──────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("阶段 3/3: 结果输出")
    logger.info("=" * 60)

    # 计算统计信息
    stats = schema_result.compute_stats()
    sc_count = stats.get("scRNA_seq", 0)
    pert_count = stats.get("with_perturbation", 0)

    logger.info("[输出统计]")
    logger.info(f"  - 输出格式: {fmt}")
    logger.info(f"  - 单细胞数据集: {sc_count} 条 ({sc_count/len(schema_result.results)*100:.1f}%）")
    logger.info(f"  - 含扰动实验: {pert_count} 条")
    logger.info(f"  - 平均 relevance score: {sum(r.relevance_score for r in schema_result.results)/len(schema_result.results):.3f}" if schema_result.results else "  - 平均 relevance score: N/A")

    # ── 主要输出 ─────────────────────────────────────────────────────────
    if fmt == "json":
        # JSON 结构化输出（标准 Schema）
        output = schema_result.to_dict()
        click.echo(json.dumps(output, ensure_ascii=False, indent=2))
    elif fmt == "id-list":
        # 仅 ID 列表
        for ds in schema_result.results:
            click.echo(ds.gse_id)
    elif fmt == "csv":
        # CSV 格式输出（方便在 Excel 中筛选）
        import csv
        import io
        import sys

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for i, ds in enumerate(schema_result.results, 1):
            acc = ds.gse_id
            row = {
                "rank": i,
                "gse_id": acc,
                "title": ds.title,
                "organism": ds.organism,
                "tissue": ds.tissue,
                "disease": ds.disease,
                "data_type": ds.data_type,
                "single_cell": ds.single_cell,
                "granularity": ds.granularity,
                "sample_count": ds.sample_count,
                "platform": ds.platform,
                "has_processed_matrix": ds.has_processed_matrix,
                "raw_only": ds.raw_only,
                "series_matrix_available": ds.series_matrix_available,
                "has_perturbation": ds.has_perturbation,
                "perturbation_types": "|".join(ds.perturbation_types),
                "pubmed_ids": "|".join(ds.pubmed_ids),
                "sra_ids": "|".join(ds.sra_ids),
                "publication_date": ds.publication_date,
                "relevance_score": f"{ds.relevance_score:.4f}",
                "total_score": f"{ds.total_score:.4f}",
                "llm_one_sentence_summary": ds.llm_one_sentence_summary,
                "llm_sample_grouping": ds.llm_sample_grouping,
                "llm_cell_count": ds.llm_cell_count,
                "llm_relevance_reason": ds.llm_relevance_reason,
            }
            writer.writerow(row)

        csv_content = buf.getvalue()
        # 输出到 stdout（使用 UTF-8-sig 编码使 Excel 可正确识别中文）
        if sys.stdout.isatty():
            # 终端直接打印（UTF-8）
            click.echo(csv_content)
        else:
            # 管道/重定向：输出 UTF-8-sig（便于 Excel 打开）
            sys.stdout.buffer.write(csv_content.encode("utf-8-sig"))
    else:
        # 表格形式（默认）- 检查是否有 LLM 分析结果
        has_llm_analysis = any(ds.llm_one_sentence_summary for ds in schema_result.results)

        if has_llm_analysis:
            # LLM 分析表格
            click.echo(f"\n{'='*120}")
            click.echo(f"数据集分析报告 - '{keyword}'")
            click.echo(f"{'='*120}")
            click.echo(
                f"{'#':<3} {'Accession':<12} {'物种':<8} {'组织':<8} {'分组':<14} {'细胞':<6} "
                f"{'平台':<8} {'一句话总结':<40} {'相关性理由':<30}"
            )
            click.echo("-" * 120)

            for i, ds in enumerate(schema_result.results, 1):
                # 规范化 ID 展示
                acc = ds.gse_id
                if acc.startswith("SRP:") or acc.startswith("ERP:") or acc.startswith("DRP:"):
                    acc = acc.split(":")[1] if ":" in acc else acc

                # LLM 分析字段
                organism = ds.organism[:7] + "…" if len(ds.organism) > 8 else (ds.organism or "NA")
                tissue = ds.tissue[:7] + "…" if len(ds.tissue) > 8 else (ds.tissue or "NA")
                sample_group = ds.llm_sample_grouping[:13] + "…" if len(ds.llm_sample_grouping) > 14 else (ds.llm_sample_grouping or "未提取")
                cell_count = ds.llm_cell_count or "NA"
                platform = ds.platform[:7] + "…" if len(ds.platform) > 8 else (ds.platform or "NA")
                summary = ds.llm_one_sentence_summary[:38] + "…" if len(ds.llm_one_sentence_summary) > 39 else (ds.llm_one_sentence_summary or "NA")
                reason = ds.llm_relevance_reason[:29] + "…" if len(ds.llm_relevance_reason) > 30 else (ds.llm_relevance_reason or "-")

                click.echo(
                    f"{i:<3} {acc:<12} {organism:<8} {tissue:<8} {sample_group:<14} {cell_count:<6} "
                    f"{platform:<8} {summary:<40} {reason:<30}"
                )
        else:
            # 原始表格（无 LLM 分析）
            click.echo(f"\nFound {len(schema_result.results)} datasets for '{keyword}' (sorted by score):\n")
            click.echo(f"{'Accession':<12} {'Type':<12} {'SC':<4} {'Pert':<5} {'Samples':<8} {'Title':<50}")
            click.echo("-" * 97)
            for ds in schema_result.results:
                title = ds.title[:47] + "..." if len(ds.title) > 50 else ds.title
                sc = "Y" if ds.single_cell else "N"
                pert = "Y" if ds.has_perturbation else "N"
                # 规范化 ID 展示
                acc = ds.gse_id
                if acc.startswith("SRP:") or acc.startswith("ERP:") or acc.startswith("DRP:"):
                    acc = acc.split(":")[1] if ":" in acc else acc
                # Samples 为 0 显示为 —
                samples = str(ds.sample_count) if ds.sample_count > 0 else "—"
                click.echo(f"{acc:<12} {ds.data_type:<12} {sc:<4} {pert:<5} {samples:<8} {title:<50}")

        # 统计摘要（区分来源）
        stats = schema_result.compute_stats()
        click.echo("\n--- Summary ---")
        click.echo(f"Total: {stats['total_found']} | scRNA-seq: {stats['scRNA_seq']} | with perturbation: {stats['with_perturbation']}")
        # 说明各源合并情况（当有多个数据源时）
        click.echo("\nNote: Results are deduplicated across GEO, SRA, and PubMed sources.")
        click.echo("PubMed branch links publications to GEO; not all papers have linked datasets.")

    if save:
        from sra_search.data_store.database import get_database

        db = get_database()
        try:
            # 使用同步批量写入代替异步队列，确保数据立即落盘
            conn = db.get_connection()
            cursor = conn.cursor()
            flushed = 0
            errors = 0
            for r in records:
                try:
                    row = r.to_db_row()
                    cursor.execute("""
                        INSERT INTO datasets (gse_id, title, pubmed_ids, sra_ids, bioproject_ids,
                            organism, disease, organ, omics_type, omics_granularity, sample_count,
                            platform, publication_date, journal, abstract, keywords,
                            first_seen_at, last_updated, version, change_log,
                            availability_status, availability_note, availability_checked_at,
                            access_type, has_gse, metadata_hash)
                        VALUES (:gse_id, :title, :pubmed_ids, :sra_ids, :bioproject_ids,
                            :organism, :disease, :organ, :omics_type, :omics_granularity, :sample_count,
                            :platform, :publication_date, :journal, :abstract, :keywords,
                            :first_seen_at, :last_updated, :version, :change_log,
                            :availability_status, :availability_note, :availability_checked_at,
                            :access_type, :has_gse, :metadata_hash)
                        ON CONFLICT(gse_id) DO UPDATE SET
                            title = COALESCE(NULLIF(:title, ''), title),
                            pubmed_ids = CASE WHEN :pubmed_ids != '[]' THEN :pubmed_ids ELSE pubmed_ids END,
                            sra_ids = CASE WHEN :sra_ids != '[]' THEN :sra_ids ELSE sra_ids END,
                            bioproject_ids = CASE WHEN :bioproject_ids != '[]' THEN :bioproject_ids ELSE bioproject_ids END,
                            organism = COALESCE(NULLIF(:organism, ''), organism),
                            disease = COALESCE(NULLIF(:disease, ''), disease),
                            organ = COALESCE(NULLIF(:organ, ''), organ),
                            omics_type = CASE WHEN :omics_type != '' THEN :omics_type ELSE omics_type END,
                            omics_granularity = CASE WHEN :omics_granularity != 'unknown' THEN :omics_granularity ELSE omics_granularity END,
                            sample_count = CASE WHEN :sample_count > 0 THEN :sample_count ELSE sample_count END,
                            platform = COALESCE(NULLIF(:platform, ''), platform),
                            publication_date = COALESCE(NULLIF(:publication_date, ''), publication_date),
                            journal = COALESCE(NULLIF(:journal, ''), journal),
                            abstract = CASE WHEN LENGTH(:abstract) > LENGTH(abstract) THEN :abstract ELSE abstract END,
                            keywords = CASE WHEN :keywords != '[]' THEN :keywords ELSE keywords END,
                            last_updated = :last_updated,
                            version = :version,
                            change_log = :change_log,
                            availability_status = :availability_status,
                            availability_note = :availability_note,
                            availability_checked_at = :availability_checked_at,
                            access_type = :access_type,
                            has_gse = :has_gse,
                            metadata_hash = :metadata_hash
                    """, row)
                    flushed += 1
                except Exception as e:
                    errors += 1
                    logger.error(f"Save error for {r.gse_id}: {e}")
            conn.commit()
            click.echo(f"\n[SAVED] {flushed} datasets to database" + (f" ({errors} errors)" if errors else ""))
            logger.info(f"[数据存储] 写入数据库: {flushed} 条记录")
        except Exception as e:
            logger.error(f"Database save failed: {e}")
            click.echo(f"\n[ERROR] Failed to save to database: {e}")

        # ── 保存搜索报告 ───────────────────────────────────────────────
        try:
            from sra_search.data_store.search_report_service import (
                SearchReportItem,
                SearchReportService,
            )
            report_service = SearchReportService(db)

            # 确定模式
            if llm_only:
                mode = "llm-only"
            elif should_use_llm:
                mode = "v1+llm"
            else:
                mode = "v1"

            # 构建报告项
            report_items = []
            for i, ds in enumerate(schema_result.results, 1):
                item = SearchReportItem(
                    rank=i,
                    gse_id=ds.gse_id,
                    relevance_score=ds.relevance_score,
                    one_sentence_summary=ds.llm_one_sentence_summary,
                    sample_grouping=ds.llm_sample_grouping,
                    cell_count=ds.llm_cell_count,
                    relevance_reason=ds.llm_relevance_reason,
                    data_type=ds.data_type,
                    sample_count=ds.sample_count,
                    organism=ds.organism,
                    tissue=ds.tissue,
                    platform=ds.platform,
                    title=ds.title,
                )
                report_items.append(item)

            # 保存报告
            report_id = report_service.save_report(
                query=keyword,
                mode=mode,
                sources=sources_list or ["geo", "sra", "pubmed"],
                total_found=len(search_results),
                returned_count=len(schema_result.results),
                llm_model=schema_result.llm_model,
                items=report_items,
            )
            logger.info(f"[搜索报告] 已保存: {report_id}")
        except Exception as e:
            logger.warning(f"[搜索报告] 保存失败: {e}")

    # ── 任务完成汇总 ──────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("[任务完成] 搜索结果汇总")
    logger.info("=" * 60)
    logger.info(f"  查询词: {keyword}")
    logger.info(f"  模式: {'V1 关键词模式' if not should_use_llm and not llm_only else ('V1+LLM 模式' if not llm_only else 'LLM-only 模式')}")
    logger.info(f"  检索数据源: {sources_list or ['geo', 'sra', 'pubmed']}")
    logger.info(f"  获取记录数: {len(search_results)}")
    logger.info(f"  输出记录数: {len(schema_result.results)} (top={top})")
    logger.info(f"  单细胞数据集: {sc_count} 条 ({sc_count/len(schema_result.results)*100:.1f}%)" if schema_result.results else "  单细胞数据集: 0 条")
    logger.info("=" * 60)


@main.command("list")
@click.option("--topic", "-t", help="主题名称筛选")
@click.option("--all", "list_all", is_flag=True, help="查看全部数据集")
@click.option("--status", type=click.Choice(["pending", "approved", "irrelevant", "deleted"]),
              help="审核状态筛选")
@click.option("--availability", type=click.Choice(["available", "unavailable", "unverified", "restricted"]),
              help="可用性筛选")
@click.option("--limit", "-n", default=20, type=int, help="展示数量")
@click.option("--offset", default=0, type=int, help="偏移量（分页）")
@click.option("--format", "fmt", type=click.Choice(["short", "wide"]), default="short",
              help="展示格式")
def list_cmd(topic: str | None, list_all: bool, status: str | None,
             availability: str | None, limit: int, offset: int, fmt: str):
    """列表浏览已存储的数据集"""
    from sra_search.data_store.database import get_database

    db = get_database()
    topic_id = None
    if topic:
        t = db.get_topic_by_name(topic)
        if t is None:
            click.echo(f"Topic '{topic}' not found")
            return
        topic_id = t.topic_id

    datasets = db.list_datasets(
        topic_id=topic_id,
        review_status=status,
        availability=availability,
        limit=limit,
        offset=offset,
    )

    if not datasets:
        click.echo("No datasets found")
        return

    total = db.count_datasets(topic_id=topic_id, review_status=status, availability=availability)
    click.echo(f"\nShowing {len(datasets)} of {total} datasets:\n")

    if fmt == "wide":
        click.echo(f"{'GSE ID':<15} {'Title':<50} {'Organism':<15} {'Type':<20} {'Samples':<8} {'Avail':<10}")
        click.echo("-" * 118)
        for ds in datasets:
            title = ds.title[:47] + "..." if len(ds.title) > 50 else ds.title
            ot = ds.omics_type[:17] if ds.omics_type else "-"
            click.echo(f"{ds.gse_id:<15} {title:<50} {ds.organism:<15} {ot:<20} {ds.sample_count:<8} {ds.availability_status:<10}")
    else:
        click.echo(f"{'GSE ID':<15} {'Title':<60} {'Avail':<10}")
        click.echo("-" * 85)
        for ds in datasets:
            title = ds.title[:57] + "..." if len(ds.title) > 60 else ds.title
            click.echo(f"{ds.gse_id:<15} {title:<60} {ds.availability_status:<10}")


@main.command()
@click.argument("gse_id")
@click.option("--changelog", is_flag=True, help="显示变更日志")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json"]),
              default="table", help="输出格式：table(表格)/json(JSON Schema)")
def show(gse_id: str, changelog: bool, fmt: str):
    """查看单条数据集完整详情

    支持 JSON 输出（标准 Schema 格式），GSM 样本名从本地数据库读取（搜索阶段已获取）。
    """
    import json as json_mod
    import re

    if not re.match(r"^GSE\d+$", gse_id.upper()):
        raise click.BadParameter(
            f"无效的 GSE ID 格式: {gse_id!r}\n"
            "期望格式: GSE + 数字，如 GSE123456"
        )

    from sra_search.converter import record_to_schema
    from sra_search.data_store.database import get_database

    db = get_database()
    ds = db.get_dataset(gse_id)
    if ds is None:
        click.echo(f"Dataset '{gse_id}' not found")
        return

    # 使用 record_to_schema 转换（record.gsm_sample_names 已在搜索阶段获取）
    schema = record_to_schema(ds, query="")

    # GSM 样本名从本地记录读取（搜索阶段已联网获取并存入数据库）
    gsm_names: list[str] = getattr(ds, "gsm_sample_names", []) or []

    if fmt == "json":
        # JSON Schema 输出
        click.echo(json_mod.dumps(schema.to_dict(), ensure_ascii=False, indent=2))
    else:
        # 表格形式 - 使用 schema 中的增强信息
        click.echo(f"\n{'='*60}")
        click.echo(f"  {ds.gse_id}")
        click.echo(f"{'='*60}")
        click.echo(f"  Title:           {schema.title or '-'}")
        click.echo(f"  Organism:        {schema.organism or '-'}")
        click.echo(f"  Disease:         {schema.disease or '-'}")
        click.echo(f"  Organ:           {schema.organ or '-'}")
        click.echo(f"  Tissue:          {schema.tissue or '-'}")
        click.echo(f"  Omics Type:      {schema.data_type or '-'}")
        click.echo(f"  Granularity:     {schema.granularity or '-'}")
        click.echo(f"  Sample Count:    {schema.sample_count or 0}")
        if gsm_names:
            # 展示存储在数据库中的 GSM 样本名（最多 20 个）
            display = gsm_names[:20]
            suffix = f" ... (+{len(gsm_names)-20})" if len(gsm_names) > 20 else ""
            click.echo(f"  GSM Samples:     {', '.join(display)}{suffix}")
        click.echo(f"  Platform:        {schema.platform or '-'}")
        click.echo(f"  Journal:         {schema.journal or '-'}")
        click.echo(f"  Publication:     {schema.publication_date or '-'}")
        click.echo(f"  PubMed IDs:      {', '.join(schema.pubmed_ids) or '-'}")
        click.echo(f"  SRA IDs:         {', '.join(schema.sra_ids) or '-'}")
        click.echo(f"  BioProject IDs:  {', '.join(schema.bioproject_ids) or '-'}")
        click.echo(f"  Availability:    {ds.availability_status}")
        if ds.availability_note:
            click.echo(f"  Avail Note:      {ds.availability_note}")
        click.echo(f"  Access Type:     {ds.access_type}")
        click.echo(f"  Has GSE:         {'Yes' if ds.has_gse else 'No'}")
        click.echo(f"  Version:         {ds.version}")
        click.echo(f"  First Seen:      {ds.first_seen_at}")
        click.echo(f"  Last Updated:    {ds.last_updated}")

        # LLM/Inference 增强信息
        if schema.summary:
            click.echo(f"\n  --- LLM Summary ---")
            click.echo(f"  {schema.summary[:200]}{'...' if len(schema.summary) > 200 else ''}")
        if hasattr(schema, '_inference_summary') and schema._inference_summary:
            click.echo(f"\n  --- Inference Summary ---")
            click.echo(f"  {schema._inference_summary[:200]}{'...' if len(schema._inference_summary) > 200 else ''}")

        click.echo(f"{'='*60}")


@main.command()
def config():
    """查看当前配置"""
    settings = get_settings()
    click.echo(f"\n{'='*55}")
    click.echo("  SRA_search Configuration")
    click.echo(f"{'='*55}")
    click.echo(f"  NCBI Email:      {'[OK] ' + settings.ncbi_email if settings.ncbi_email else '[NOT SET]'}")
    click.echo(f"  NCBI API Key:    {'[OK] ' + settings.ncbi_api_key[:8] + '...' if settings.ncbi_api_key else '[NOT SET]'}")
    click.echo(f"  Rate Limit:      {settings.effective_rate_limit} req/s")
    click.echo(f"  Database:        {settings.db_path_resolved}")
    click.echo(f"  WAL Mode:        {'Enabled' if settings.db_wal_enabled else 'Disabled'}")
    click.echo(f"  Min Samples:     {settings.availability_min_samples}")
    click.echo(f"  Log Level:       {settings.log_level}")
    click.echo(f"  {'─'*51}")
    click.echo(f"  LLM Provider:    {settings.llm_provider or '[NOT SET]'}")
    click.echo(f"  LLM Model:       {settings.llm_model or '(default)'}")
    click.echo(f"  LLM Enabled:     {'Yes' if settings.llm_enabled else 'No'}")
    if settings.llm_api_key:
        masked = settings.llm_api_key[:8] + "..." + settings.llm_api_key[-4:]
        click.echo(f"  LLM API Key:     [OK] {masked}")
    else:
        click.echo("  LLM API Key:     [NOT SET]  ← set SRA_SEARCH_LLM_API_KEY to enable")
    if settings.llm_base_url:
        click.echo(f"  LLM Base URL:    {settings.llm_base_url}")
    click.echo(f"  LLM Top-K:       {settings.llm_top_k}")
    click.echo(f"{'='*55}\n")

    if not settings.ncbi_email:
        click.echo("To configure NCBI, set environment variables:")
        click.echo("   export SRA_SEARCH_NCBI_EMAIL=your@email.com")
        click.echo("   export SRA_SEARCH_NCBI_API_KEY=your_api_key")
        click.echo("")
    if not settings.llm_api_key:
        click.echo("To enable LLM features (V2), set:")
        click.echo("   export SRA_SEARCH_LLM_API_KEY=sk-...")
        click.echo("   export SRA_SEARCH_LLM_PROVIDER=openai   # openai / anthropic / local")
        click.echo("   export SRA_SEARCH_LLM_MODEL=gpt-4o-mini")
        click.echo("")
        click.echo("Then use: sra-search search 'query' --llm --summarize")


# ── Topic 子命令组 ──────────────────────────────────────────────────────────

@main.group("topic")
def topic_group():
    """主题管理：创建、查看、删除研究主题"""
    pass


@topic_group.command("new")
@click.argument("name")
@click.option("--description", "-d", default="", help="主题描述")
@click.option("--keywords", "-k", default="", help="额外关键词（逗号分隔）")
@click.option("--species", "-s", multiple=True, help="物种筛选（可多选），如 --species human")
@click.option("--omics", "-o", multiple=True,
              type=click.Choice(["scRNA-seq", "bulk RNA-seq", "spatial transcriptomics",
                                 "ATAC-seq", "ChIP-seq", "proteomics", "GWAS",
                                 "WGS", "WES", "Ribo-seq", "single-cell multi-omics"]),
              help="组学类型筛选（可多选）")
def topic_new(name: str, description: str, keywords: str, species: tuple, omics: tuple):
    """创建新主题（自动解析疾病/器官/组学维度）"""
    from datetime import datetime, timezone

    from sra_search.data_store.database import Database
    from sra_search.metadata_extractor.models import TopicRecord
    from sra_search.topic_manager.keyword_generator import KeywordGenerator
    from sra_search.topic_manager.topic import TopicParser

    click.echo(f"[*] Creating topic: {name}")

    # 解析主题
    parser = TopicParser()
    extra_kw = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []

    # 如果用户指定了物种/组学，优先使用
    if species or omics:
        definition = parser.parse_from_dimensions(
            name=name,
            diseases=[name],  # 把 name 当作疾病关键词
            description=description or name,
            omics_types=list(omics) if omics else None,
            species=list(species) if species else ["Homo sapiens"],
        )
    else:
        definition = parser.parse(name, extra_kw)

    # 生成关键词
    kg = KeywordGenerator()
    queries = kg.generate(definition, max_queries=50)

    # 保存到数据库
    db = Database()
    now = datetime.now(timezone.utc).isoformat()
    topic_record = TopicRecord(
        topic_id=definition.topic_id,
        name=definition.name,
        description=description or definition.description,
        keywords_used=[q[0] for q in queries],
        created_at=now,
        last_searched_at=None,
    )

    async def save_topic():
        await db.insert_topic(topic_record)
        await db.stop_write_queue()

    asyncio.run(save_topic())

    # 输出摘要
    click.echo("\n[+] Topic created successfully!")
    click.echo(f"\n=== Topic: {name} ===")
    click.echo(f"  ID:        {definition.topic_id[:8]}...")
    click.echo(f"  Diseases:  {', '.join(definition.diseases) or '-'}")
    click.echo(f"  Organs:    {', '.join(definition.organs) or '-'}")
    click.echo(f"  Omics:     {', '.join(definition.omics_types) or '-'}")
    click.echo(f"  Species:   {', '.join(definition.species)}")
    click.echo(f"\n  Keywords generated: {len(queries)}")
    click.echo("\n  Top 10 search queries:")
    for q, weight in queries[:10]:
        bar = "█" * int(weight * 10)
        click.echo(f"    [{bar:<10}] {q}")

    if len(queries) > 10:
        click.echo(f"    ... and {len(queries) - 10} more (use 'sra-search topic show {name}' to see all)")

    click.echo(f"\n  Next: sra-search topic search {name}  # 执行主题搜索")


@topic_group.command("list")
def topic_list():
    """列出所有已保存的主题"""
    from sra_search.data_store.database import Database

    db = Database()
    topics = db.list_topics()

    if not topics:
        click.echo("[*] No topics found. Create one with: sra-search topic new <name>")
        return

    click.echo(f"\n=== Topics ({len(topics)} total) ===\n")
    table = [["#", "Name", "Created", "Last Searched", "Keywords"]]
    for i, t in enumerate(topics, 1):
        created = t.created_at[:10] if t.created_at else "-"
        last = t.last_searched_at[:10] if t.last_searched_at else "Never"
        kw_count = len(t.keywords_used) if t.keywords_used else 0
        table.append([
            str(i),
            t.name,
            created,
            last,
            f"{kw_count} keywords",
        ])

    col_widths = [max(len(str(row[i])) for row in table) for i in range(len(table[0]))]
    for row in table:
        click.echo("  ".join(str(cell).ljust(w) for cell, w in zip(row, col_widths)))


@topic_group.command("show")
@click.argument("name")
@click.option("--keywords", "-k", is_flag=True, help="显示所有生成的关键词")
def topic_show(name: str, keywords: bool):
    """显示主题详情"""
    from sra_search.data_store.database import Database
    from sra_search.topic_manager.keyword_generator import KeywordGenerator
    from sra_search.topic_manager.topic import TopicParser

    db = Database()
    topic_record = db.get_topic_by_name(name)

    if not topic_record:
        click.echo(f"[!] Topic '{name}' not found.")
        return

    # 重新解析获取最新状态
    parser = TopicParser()
    definition = parser.parse(name)

    kg = KeywordGenerator()
    queries = kg.generate(definition, max_queries=200)
    kw_list = [q[0] for q in queries]

    click.echo(f"\n=== Topic: {name} ===")
    click.echo(f"  ID:          {topic_record.topic_id}")
    click.echo(f"  Description: {topic_record.description or '-'}")
    click.echo(f"  Diseases:    {', '.join(definition.diseases) or '-'}")
    click.echo(f"  Organs:      {', '.join(definition.organs) or '-'}")
    click.echo(f"  Omics:       {', '.join(definition.omics_types) or '-'}")
    click.echo(f"  Species:     {', '.join(definition.species)}")
    click.echo(f"  Created:     {topic_record.created_at[:19] if topic_record.created_at else '-'}")
    click.echo(f"  Last search: {topic_record.last_searched_at[:19] if topic_record.last_searched_at else 'Never'}")

    # 统计
    from sra_search.review_manager.filters import ReviewFilters
    filters = ReviewFilters(db)
    summary = filters.get_review_summary(topic_record.topic_id)
    total = summary["pending"] + summary["approved"] + summary["irrelevant"]

    click.echo("\n  === Statistics ===")
    click.echo(f"  Datasets found:  {total}")
    click.echo(f"  Pending review:  {summary['pending']}")
    click.echo(f"  Approved:         {summary['approved']}")
    click.echo(f"  Irrelevant:       {summary['irrelevant']}")

    if keywords:
        click.echo(f"\n  === Keywords ({len(kw_list)}) ===")
        for q, weight in queries:
            bar = "█" * int(weight * 10)
            click.echo(f"    [{bar:<10}] {q}")
    else:
        click.echo(f"\n  Keywords generated: {len(kw_list)}")
        click.echo("  Top 10:")
        for q, weight in queries[:10]:
            click.echo(f"    [{weight:.2f}] {q}")
        if len(queries) > 10:
            click.echo(f"    ... use --keywords to see all {len(queries)} keywords")


@topic_group.command("search")
@click.argument("name")
@click.option("--top", "-t", default=30, type=int, help="返回前 N 个结果")
@click.option("--organism", "-o", multiple=True, help="生物体过滤（可多选）")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json", "id-list"]),
              default="table", help="输出格式")
@click.option("--llm/--no-llm", "use_llm", default=None, is_flag=True,
              help="使用 LLM 语义排序")
def topic_search(name: str, top: int, organism: tuple, fmt: str, use_llm: bool | None):
    """对指定主题执行搜索（使用主题生成的关键词）"""
    import uuid
    from datetime import datetime, timezone

    from sra_search.data_store.database import Database
    from sra_search.metadata_extractor.models import (
        SearchHistoryRecord,
        TopicDatasetRelation,
    )
    from sra_search.search_engine.aggregator import Aggregator
    from sra_search.topic_manager.keyword_generator import KeywordGenerator
    from sra_search.topic_manager.topic import TopicParser

    db = Database()
    topic_record = db.get_topic_by_name(name)

    if not topic_record:
        click.echo(f"[!] Topic '{name}' not found.")
        return

    # 生成关键词
    parser = TopicParser()
    definition = parser.parse(name)
    kg = KeywordGenerator()
    queries = kg.generate(definition, max_queries=100)

    click.echo(f"[*] Topic: {name}")
    click.echo(f"[*] Generated {len(queries)} search queries")

    # 执行聚合搜索（使用第一个高权重关键词作为主查询）
    # 后续可以改为批量执行所有关键词
    top_query = queries[0][0] if queries else name
    click.echo(f"[*] Running search with: '{top_query}' ...\n")

    async def run_search():
        agg = Aggregator()
        organisms_filter = list(organism) if organism else definition.species
        results = await agg.search(
            query=top_query,
            top_k=top,
            organisms=organisms_filter,
        )
        await agg.close()
        return results

    results = asyncio.run(run_search())

    if not results:
        click.echo("[*] No results found.")
        return

    click.echo(f"[+] Found {len(results)} results")

    # 转换并保存到数据库（同步批量写入）
    async def save_results():
        for rec in results:
            ds_record = rec.to_dataset_record()
            await db.upsert_dataset(ds_record)

            td_relation = TopicDatasetRelation(
                id=str(uuid.uuid4()),
                topic_id=topic_record.topic_id,
                gse_id=ds_record.gse_id,
                match_keyword=top_query,
                match_source="topic_search",
                match_score=rec.relevance_score,
                review_status="pending",
                review_note="",
                reviewed_at=None,
                added_at=datetime.now(timezone.utc).isoformat(),
            )
            await db.insert_topic_dataset(td_relation)

        await db.insert_search_history(SearchHistoryRecord(
            id=str(uuid.uuid4()),
            topic_id=topic_record.topic_id,
            search_time=datetime.now(timezone.utc).isoformat(),
            keyword_used=top_query,
            results_count=len(results),
        ))

        conn = db.get_connection()
        conn.execute(
            "UPDATE topics SET last_searched_at = ? WHERE topic_id = ?",
            (datetime.now(timezone.utc).isoformat(), topic_record.topic_id),
        )
        conn.commit()

        await db.stop_write_queue()

    asyncio.run(save_results())

    # 输出结果
    if fmt == "json":
        import json
        click.echo(json.dumps([r.model_dump() for r in results], indent=2, ensure_ascii=False))
    elif fmt == "id-list":
        for r in results:
            click.echo(r.gse_id)
    else:
        _print_search_table(results, top)


@topic_group.command("delete")
@click.argument("name")
@click.option("--force", "-f", is_flag=True, help="跳过确认直接删除")
def topic_delete(name: str, force: bool):
    """删除主题（不删除关联的数据集）"""
    from sra_search.data_store.database import Database

    db = Database()
    topic_record = db.get_topic_by_name(name)

    if not topic_record:
        click.echo(f"[!] Topic '{name}' not found.")
        return

    if not force:
        click.confirm(f"Delete topic '{name}'? (Datasets will be kept)", abort=True)

    conn = db.get_connection()
    # 删除主题-数据集关联
    conn.execute("DELETE FROM topic_datasets WHERE topic_id = ?", (topic_record.topic_id,))
    # 删除主题
    conn.execute("DELETE FROM topics WHERE topic_id = ?", (topic_record.topic_id,))
    conn.commit()

    click.echo(f"[+] Topic '{name}' deleted.")


@main.command()
@click.argument("accession")
@click.option("--to", "-t", "target_db",
              type=click.Choice(["sra", "gds", "bioproject", "biosample", "pubmed"]),
              default="sra",
              help="目标数据库（默认: sra）")
@click.option("--all-targets", is_flag=True, help="查询所有可用目标类型")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json"]),
              default="table", help="输出格式")
def convert(accession: str, target_db: str, all_targets: bool, fmt: str):
    """NCBI 编号转换（GSE ↔ SRP ↔ BioProject 等）"""
    from sra_search.id_converter import NCBIConverter, detect_accession_type

    acc_type = detect_accession_type(accession)
    if acc_type is None:
        click.echo(f"[!] Unknown accession format: {accession}", err=True)
        click.echo("    Supported: GSE, GSM, SRP/ERP/DRP, SRX, SRR, PRJNA, PRJEB, BioSample")
        return

    click.echo(f"[*] Detected: {accession} ({acc_type})\n")

    if all_targets:
        # 查询所有目标类型
        targets_to_query = ["sra", "gds", "bioproject", "biosample", "pubmed"]
    else:
        targets_to_query = [target_db]

    async def run_conversion():
        import aiohttp
        converter = NCBIConverter()
        results = []

        async with aiohttp.ClientSession() as session:
            for tdb in targets_to_query:
                result = await converter.convert(accession, tdb, session)
                results.append(result)

        return results

    conversion_results = asyncio.run(run_conversion())

    if fmt == "json":
        import json
        click.echo(json.dumps([{
            "source": r.source,
            "source_type": r.source_type,
            "target_type": r.target_type,
            "targets": r.targets,
            "note": r.note,
        } for r in conversion_results], indent=2, ensure_ascii=False))
    else:
        for r in conversion_results:
            color = "green" if r.targets else "yellow"
            click.echo(click.style(f"  → {r.target_type.upper():12} ", fg=color) +
                       (f"{', '.join(r.targets)}" if r.targets else click.style(r.note, fg="red")))
        click.echo("")
        click.echo("Usage examples:")
        click.echo("  sra-search convert GSE123456 --to sra         # GSE → SRA Study")
        click.echo("  sra-search convert SRP123456 --to gds         # SRP → GEO Datasets")
        click.echo("  sra-search convert SRR789012 --to sra         # SRR → parent SRA Study")
        click.echo("  sra-search convert GSE123456 --all-targets    # 查询所有目标类型")


@main.command()
@click.argument("gse_id", required=False)
@click.option("--topic", "-t", help="检查某主题下所有数据集")
@click.option("--all", "check_all", is_flag=True, help="检查全部未验证数据集")
@click.option("--recheck", is_flag=True, help="重新检查已有状态的数据集")
@click.option("--min-samples", default=3, help="最小样本数阈值（默认3）")
@click.option("--format", "-f", "fmt", default="table", type=click.Choice(["table", "json"]), help="输出格式")
def check(gse_id: str | None, topic: str | None, check_all: bool, recheck: bool, min_samples: int, fmt: str):
    """数据集可用性检查（SRA/BioProject）"""
    # GSE ID 格式校验（如果提供了）
    if gse_id:
        import re
        if not re.match(r"^GSE\d+$", gse_id.upper()):
            raise click.BadParameter(
                f"无效的 GSE ID 格式: {gse_id!r}\n"
                "期望格式: GSE + 数字，如 GSE123456"
            )
    asyncio.run(_check_async(gse_id, topic, check_all, recheck, min_samples, fmt))


async def _check_async(
    gse_id: str | None,
    topic: str | None,
    check_all: bool,
    recheck: bool,
    min_samples: int,
    fmt: str,
) -> None:
    """执行可用性检查的异步逻辑"""
    from sra_search.availability_checker.sra_checker import SraChecker
    from sra_search.data_store.database import Database
    from sra_search.utils.rate_limiter import RateLimiter

    db = Database()
    checker = SraChecker(RateLimiter(rate=3.0))
    results: list[dict] = []

    # 统计信息
    stats = {"total": 0, "available": 0, "restricted": 0, "unavailable": 0, "unverified": 0}

    # 收集待检查的数据集
    datasets_to_check: list[tuple[str, str, list[str]]] = []  # (gse_id, title, sra_ids)

    if gse_id:
        # 单个数据集检查
        record = db.get_dataset(gse_id)
        if not record:
            click.echo(f"[!] Dataset '{gse_id}' not found in database.", err=True)
            click.echo("    Run 'sra-search search' first to fetch the dataset.")
            return
        sra_ids = record.sra_ids or []
        if not sra_ids:
            click.echo(f"[!] Dataset '{gse_id}' has no SRA IDs. Nothing to check.")
            return
        datasets_to_check.append((gse_id, record.title or "", sra_ids))
        stats["total"] = 1

    elif topic:
        # 主题下所有数据集
        topic_record = db.get_topic_by_name(topic)
        if not topic_record:
            click.echo(f"[!] Topic '{topic}' not found. Use 'sra-search list' to see available topics.")
            return

        topic_datasets = db.get_topic_datasets(topic_record.topic_id)
        if not topic_datasets:
            click.echo(f"[!] No datasets found under topic '{topic}'.")
            return

        for td in topic_datasets:
            td_gse_id = td.get("gse_id", "")
            record = db.get_dataset(td_gse_id)
            if not record:
                continue
            sra_ids = record.sra_ids or []
            if sra_ids and (not record.availability_status or recheck or record.availability_status == "unverified"):
                datasets_to_check.append((td_gse_id, record.title or "", sra_ids))
        stats["total"] = len(datasets_to_check)

    elif check_all:
        # 全量检查未验证的数据集
        if recheck:
            # 检查所有有 SRA ID 的数据集
            all_datasets = db.list_datasets(limit=10000)
        else:
            # 只检查未验证的
            all_datasets = db.list_datasets(availability="unverified", limit=10000)

        for record in all_datasets:
            sra_ids = record.sra_ids or []
            if sra_ids:
                datasets_to_check.append((record.gse_id, record.title or "", sra_ids))
        stats["total"] = len(datasets_to_check)

    else:
        click.echo("Use: sra-search check <GSE_ID> [--topic TOPIC] [--all] [--recheck]")
        click.echo("")
        click.echo("Examples:")
        click.echo("  sra-search check GSE123456          # 检查单个数据集")
        click.echo("  sra-search check --topic gout      # 检查某主题下所有数据集")
        click.echo("  sra-search check --all             # 检查所有未验证的数据集")
        click.echo("  sra-search check --all --recheck   # 重新检查所有数据集")
        return

    if not datasets_to_check:
        click.echo("[*] No datasets to check.")
        return

    click.echo(f"[*] Checking {stats['total']} dataset(s)...\n")

    # 进度显示
    progress = click.progressbar(
        length=stats["total"],
        label="  Checking",
        show_eta=True,
    )

    import aiohttp
    async with aiohttp.ClientSession() as session:
        for gse_id, title, sra_ids in datasets_to_check:
            progress.update(1)

            # 获取第一个 SRP 进行检查
            srp_id = next((sid for sid in sra_ids if sid.startswith(("SRP", "ERP", "DRP"))), None)
            if not srp_id:
                continue

            # 调用 SRA 检查器
            check_result = await checker.check_srp(srp_id, session, min_samples)

            # 更新数据库
            now = _now_iso()
            conn = db.get_connection()
            conn.execute("""
                UPDATE datasets SET
                    availability_status = ?,
                    availability_note = ?,
                    access_type = ?,
                    availability_checked_at = ?
                WHERE gse_id = ?
            """, (
                check_result.status,
                check_result.note,
                check_result.access_type,
                now,
                gse_id,
            ))
            conn.commit()

            # 记录结果
            result_entry = {
                "gse_id": gse_id,
                "title": title[:50] + "..." if len(title) > 50 else title,
                "srp_id": srp_id,
                "status": check_result.status,
                "access_type": check_result.access_type,
                "sample_count": check_result.sample_count,
                "note": check_result.note,
            }
            results.append(result_entry)

            # 更新统计
            if check_result.status in stats:
                stats[check_result.status] += 1

    progress.finish()
    click.echo("")

    # 输出结果
    if fmt == "json":
        import json
        click.echo(json.dumps({"stats": stats, "results": results}, indent=2, ensure_ascii=False))
    else:
        _print_check_results(results, stats)


def _print_check_results(results: list[dict], stats: dict) -> None:
    """格式化输出检查结果"""
    if not results:
        click.echo("[*] No results to display.")
        return

    # 统计摘要
    click.echo("=== 检查摘要 ===")
    click.echo(f"  Total:      {stats['total']}")
    click.echo(f"  Available: {stats['available']}  \u2713")
    click.echo(f"  Restricted: {stats['restricted']}  \u26d4")
    click.echo(f"  Unavailable: {stats['unavailable']}  \u2717")
    click.echo(f"  Unverified: {stats['unverified']}  \u2317")
    click.echo("")

    # 结果表格
    table = [
        ["GSE ID", "SRP ID", "Status", "Access", "Samples", "Note"]
    ]
    status_colors = {
        "available": "green",
        "restricted": "yellow",
        "unavailable": "red",
        "unverified": "cyan",
    }

    for r in results:
        status_text = r["status"]
        if r["status"] == "available" and r["note"]:
            status_text += "*"  # 带*表示样本数低
        table.append([
            r["gse_id"],
            r["srp_id"],
            status_text,
            r["access_type"],
            str(r["sample_count"]),
            r["note"][:40] if r["note"] else "-",
        ])

    col_widths = [max(len(str(row[i])) for row in table) for i in range(len(table[0]))]
    header = "  ".join(str(h).ljust(w) for h, w in zip(table[0], col_widths))
    click.echo(click.style(header, bold=True))
    click.echo("  ".join("-" * w for w in col_widths))

    for row in table[1:]:
        status_idx = 2
        status_val = row[status_idx]
        color = status_colors.get(status_val.replace("*", ""), None)
        row_str = "  ".join(str(cell).ljust(w) for cell, w in zip(row, col_widths))
        if color:
            click.echo(click.style(row_str, fg=color))
        else:
            click.echo(row_str)

    if any("*" in r["status"] for r in results):
        click.echo("\n* = Low sample count (below --min-samples threshold)")


@main.command()
@click.option("--topic", "-t", help="更新指定主题下的数据集")
@click.option("--all", "update_all", is_flag=True, help="更新所有数据集的元数据")
@click.option("--since", default=None, help="只更新指定日期之后的数据（YYYY-MM-DD）")
@click.option("--dry-run", is_flag=True, help="仅显示将要更新的数据集，不实际执行")
def update(topic: str | None, update_all: bool, since: str | None, dry_run: bool):
    """更新数据集元数据（BioProject / GEO / SRA）"""
    from datetime import datetime

    from sra_search.data_store.database import Database

    if not topic and not update_all:
        click.echo("Use: sra-search update --topic <name>  OR  sra-search update --all")
        click.echo("")
        click.echo("Examples:")
        click.echo("  sra-search update --topic gout        # 更新某主题的数据集")
        click.echo("  sra-search update --all              # 更新所有数据集")
        click.echo("  sra-search update --all --since 2024-01-01  # 只更新2024年以来的")
        click.echo("  sra-search update --all --dry-run    # 预览模式（不实际更新）")
        return

    db = Database()
    datasets_to_update: list[tuple[str, str]] = []  # (gse_id, title)

    if topic:
        topic_record = db.get_topic_by_name(topic)
        if not topic_record:
            click.echo(f"[!] Topic '{topic}' not found.")
            return
        topic_datasets = db.get_topic_datasets(topic_record.topic_id)
        for td in topic_datasets:
            datasets_to_update.append((td.get("gse_id", ""), td.get("title", "")))

    elif update_all:
        # 获取所有数据集
        all_datasets = db.list_datasets(limit=10000)
        for rec in all_datasets:
            # 日期过滤
            if since:
                try:
                    since_dt = datetime.strptime(since, "%Y-%m-%d")
                    if rec.publication_date:
                        pub_dt = datetime.strptime(rec.publication_date[:10], "%Y-%m-%d")
                        if pub_dt < since_dt:
                            continue
                except ValueError:
                    pass
            datasets_to_update.append((rec.gse_id, rec.title or ""))

    if not datasets_to_update:
        click.echo("[*] No datasets to update.")
        return

    click.echo(f"[*] Found {len(datasets_to_update)} dataset(s) to update\n")

    if dry_run:
        click.echo("=== Dry Run - Will update the following ===\n")
        for gse_id, title in datasets_to_update[:20]:
            click.echo(f"  {gse_id}  {title[:50]}")
        if len(datasets_to_update) > 20:
            click.echo(f"\n  ... and {len(datasets_to_update) - 20} more")
        click.echo("\n[*] Run without --dry-run to actually update.")
        return

    # 实际更新逻辑
    async def do_update():
        import aiohttp

        from sra_search.retriever.geo_api import GeoRetriever

        updated = 0
        errors = 0
        total = len(datasets_to_update)

        progress = click.progressbar(
            length=total, label="  Updating", show_eta=True
        )

        async with aiohttp.ClientSession() as session:
            for gse_id, title in datasets_to_update:
                progress.update(1)
                try:
                    # 重新获取 GEO 元数据
                    geo = GeoRetriever(session)
                    metadata = await geo.get_metadata(gse_id)

                    if metadata:
                        now = _now_iso()
                        conn = db.get_connection()
                        conn.execute("""
                            UPDATE datasets SET
                                title = COALESCE(NULLIF(?, ''), title),
                                sample_count = CASE WHEN ? > 0 THEN ? ELSE sample_count END,
                                last_updated = ?,
                                availability_status = 'unverified'
                            WHERE gse_id = ?
                        """, (
                            metadata.get("title") or title,
                            metadata.get("n_samples", 0),
                            metadata.get("n_samples", 0),
                            now,
                            gse_id,
                        ))
                        conn.commit()
                        updated += 1
                except Exception:
                    errors += 1

        progress.finish()
        return updated, errors

    updated, errors = asyncio.run(do_update())

    click.echo("\n[+] Update complete:")
    click.echo(f"    Updated: {updated}")
    click.echo(f"    Errors:  {errors}")
    click.echo("\n[*] Run 'sra-search check' to verify availability status.")


# ── Report 命令组 ──────────────────────────────────────────────────────────

@main.command("report")
@click.option("--query", "-q", help="搜索关键词")
@click.option("--list", "list_all", is_flag=True, help="列出所有报告")
@click.option("--delete", "-d", help="删除指定报告（输入报告 ID）")
@click.option("--limit", "-n", default=20, type=int, help="列出数量")
@click.option("--offset", default=0, type=int, help="偏移量")
def report(query: str | None, list_all: bool, delete: str | None, limit: int, offset: int):
    """查看和管理搜索报告

    搜索报告保存了每次搜索的关键词、结果和分析，便于回顾和定期更新。
    """
    from sra_search.data_store.database import get_database
    from sra_search.data_store.search_report_service import SearchReportService

    db = get_database()
    service = SearchReportService(db)

    # 删除报告
    if delete:
        success = service.delete_report(delete)
        if success:
            click.echo(f"[OK] 已删除报告: {delete}")
        else:
            click.echo(f"[ERROR] 删除失败: {delete}")
        return

    # 列出报告
    if list_all or not query:
        reports = service.list_reports(limit=limit, offset=offset)
        if not reports:
            click.echo("暂无搜索报告")
            click.echo("\n执行搜索时会自动保存报告：")
            click.echo("  sra-search search 'gout single cell' --llm")
            return

        click.echo(f"\n{'='*100}")
        click.echo(f"{'搜索报告列表 (最近 ' + str(len(reports)) + ' 条)'}")
        click.echo(f"{'='*100}")
        click.echo(f"{'#':<4} {'Query':<30} {'模式':<10} {'数据源':<25} {'结果数':<8} {'时间':<20}")
        click.echo("-" * 100)
        for i, r in enumerate(reports, offset + 1):
            query_str = r.query[:28] + "…" if len(r.query) > 29 else r.query
            sources_str = ",".join(r.sources)[:23]
            click.echo(f"{i:<4} {query_str:<30} {r.mode:<10} {sources_str:<25} {r.returned_count:<8} {r.searched_at[:19]:<20}")
        click.echo("=" * 100)
        return

    # 查看指定查询的报告
    saved_report = service.get_report_by_query(query)
    if not saved_report:
        click.echo(f"未找到查询词 '{query}' 的报告")
        click.echo("\n可使用 'sra-search search' 重新搜索来保存报告")
        return

    click.echo(f"\n{'='*120}")
    click.echo(f"搜索报告 - '{saved_report.query}'")
    click.echo(f"{'='*120}")
    click.echo(f"  模式: {saved_report.mode} | 数据源: {', '.join(saved_report.sources)} | "
                f"结果数: {saved_report.returned_count}/{saved_report.total_found} | "
                f"LLM: {saved_report.llm_model or 'N/A'}")
    click.echo(f"  时间: {saved_report.searched_at}")
    click.echo(f"{'='*120}")

    if not saved_report.items:
        click.echo("报告为空")
        return

    # 显示表格
    click.echo(
        f"{'#':<3} {'Accession':<12} {'物种':<8} {'组织':<8} {'分组':<14} {'细胞':<6} "
        f"{'平台':<8} {'一句话总结':<40} {'相关性理由':<25}"
    )
    click.echo("-" * 120)

    for item in saved_report.items:
        organism = item.organism[:7] + "…" if len(item.organism) > 8 else (item.organism or "NA")
        tissue = item.tissue[:7] + "…" if len(item.tissue) > 8 else (item.tissue or "NA")
        sample_group = item.sample_grouping[:13] + "…" if len(item.sample_grouping) > 14 else (item.sample_grouping or "未提取")
        cell_count = item.cell_count or "NA"
        platform = item.platform[:7] + "…" if len(item.platform) > 8 else (item.platform or "NA")
        summary = item.one_sentence_summary[:38] + "…" if len(item.one_sentence_summary) > 39 else (item.one_sentence_summary or "NA")
        reason = item.relevance_reason[:24] + "…" if len(item.relevance_reason) > 25 else (item.relevance_reason or "-")

        click.echo(
            f"{item.rank:<3} {item.gse_id:<12} {organism:<8} {tissue:<8} {sample_group:<14} {cell_count:<6} "
            f"{platform:<8} {summary:<40} {reason:<25}"
        )

    click.echo("=" * 120)
    click.echo(f"\n提示: 使用 'sra-search search \"{saved_report.query}\" --llm' 重新搜索以更新报告")


# ── Cache 命令组 ──────────────────────────────────────────────────────────────

@main.command("cache")
@click.option("--info", "-i", is_flag=True, help="显示缓存统计信息")
@click.option("--clean", "-c", is_flag=True, help="清理过期缓存")
@click.option("--clear", is_flag=True, help="清空所有缓存")
@click.option("--ttl", type=int, help="设置 TTL 小时数")
def cache(info: bool, clean: bool, clear: bool, ttl: int | None):
    """管理查询缓存

    缓存功能：
    - 自动缓存 GEO 检索结果（TTL 默认 24 小时）
    - 避免重复查询，节省 API 配额
    - 支持最大存储限制（默认 500 条 / 100MB）

    示例：
      sra-search cache --info      # 查看缓存状态
      sra-search cache --clean     # 清理过期缓存
      sra-search cache --clear     # 清空所有缓存
      sra-search cache --ttl 48    # 设置 TTL 为 48 小时
    """
    from sra_search.cache import get_cache

    cache_instance = get_cache()

    # 设置 TTL
    if ttl is not None:
        if ttl <= 0:
            raise click.ClickException("TTL 必须大于 0")
        cache_instance.ttl_hours = ttl
        click.echo(f"[OK] TTL 已设置为 {ttl} 小时")

    # 显示统计信息
    if info or (not clean and not clear and ttl is None):
        stats = cache_instance.get_stats()
        click.echo("\n📊 缓存统计:")
        click.echo(f"  缓存目录: {stats['cache_dir']}")
        click.echo(f"  总条目数: {stats['total_entries']}")
        click.echo(f"  有效条目: {stats['valid_entries']}")
        click.echo(f"  过期条目: {stats['expired_entries']}")
        click.echo(f"  总大小:   {stats['total_size_mb']} MB")
        click.echo(f"  TTL:      {stats['ttl_hours']} 小时")
        click.echo(f"  最大条目: {stats['max_entries']}")
        click.echo(f"  最大大小: {stats['max_size_mb']} MB")
        return

    # 清理过期缓存
    if clean:
        if not click.confirm("确定要清理过期缓存吗?"):
            click.echo("已取消")
            return
        result = cache_instance.clean_expired()
        click.echo(f"[OK] 清理完成: 移除 {result['removed']} 条，释放 {result['freed_bytes'] / 1024:.1f} KB")

    # 清空所有缓存
    if clear:
        if not click.confirm("⚠️ 警告：这将清空所有缓存！确定要继续吗?"):
            click.echo("已取消")
            return
        cache_instance.invalidate()  # 清空所有
        click.echo("[OK] 缓存已清空")


if __name__ == "__main__":
    main()
