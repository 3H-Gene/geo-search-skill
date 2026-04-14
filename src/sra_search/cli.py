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

    settings = get_settings()
    if not settings.ncbi_email:
        logger.warning(
            "NCBI email not configured. Set SRA_SEARCH_NCBI_EMAIL env var or "
            "run 'sra-search config' to configure."
        )


@main.command()
@click.argument("keyword")
@click.option("--sources", "-s", multiple=True, type=click.Choice(["geo", "sra", "pubmed", "bioproject"]),
              help="数据源筛选（可多选）")
@click.option("--retmax", "-n", default=None, type=int, help="每源最大返回数")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json", "id-list"]),
              default="table", help="输出格式：table(表格)/json(JSON结构化)/id-list(ID列表)")
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
):
    """关键词搜索数据集

    支持三种输出格式：
    - table: 表格形式（默认）
    - json: 标准 JSON Schema 输出（与 gse-downloader 解耦）
    - id-list: 仅 GSE ID 列表（适合管道处理）

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
    """
    import json

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

    search_results = run_async(_do_search())

    if not search_results:
        click.echo(f"No datasets found for '{keyword}'")
        return

    records = [r.dataset for r in search_results]

    # ── 判断是否启用 LLM ────────────────────────────────────────────────────
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

    # ── 转换为 Schema 并排序 ────────────────────────────────────────────────
    if should_use_llm or llm_only:
        # 构建 LLM 客户端（CLI 参数 > 环境变量 > settings）
        _provider = llm_provider or settings.llm_provider or "openai"
        _api_key = llm_api_key or settings.llm_api_key or ""
        _model = llm_model or settings.llm_model or ""
        _base_url = llm_base_url or settings.llm_base_url or ""
        _top_k = llm_top_k or settings.llm_top_k
        _min_rel = llm_min_relevance if llm_min_relevance is not None else 0.0

        if not _api_key:
            click.echo(
                "\n[WARNING] --llm/--llm-only requested but no API key found. "
                "Set SRA_SEARCH_LLM_API_KEY or use --llm-api-key.\n"
                "Falling back to keyword mode.\n",
                err=True,
            )
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
            else:
                _mode_desc = f"V1+LLM (top_k={_top_k}, min_relevance={_min_rel})"
                _score_all = False

            click.echo(
                f"\n[LLM] Provider={_provider!r} model={_effective_model!r} | "
                f"API key OK | Mode: {_mode_desc}...",
                err=True,
            )

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
            click.echo(
                f"[LLM] Done — returned {len(schema_result.datasets)} ranked results.",
                err=True,
            )
    else:
        schema_result = records_to_search_result(records, query=keyword, top_n=top)

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

    # ── 主要输出 ─────────────────────────────────────────────────────────
    if fmt == "json":
        # JSON 结构化输出（标准 Schema）
        output = schema_result.to_dict()
        click.echo(json.dumps(output, ensure_ascii=False, indent=2))
    elif fmt == "id-list":
        # 仅 ID 列表
        for ds in schema_result.results:
            click.echo(ds.gse_id)
    else:
        # 表格形式（默认）
        click.echo(f"\nFound {len(schema_result.results)} datasets for '{keyword}' (sorted by score):\n")
        click.echo(f"{'Accession':<12} {'Type':<12} {'SC':<4} {'Pert':<5} {'Samples':<8} {'Title':<40}")
        click.echo("-" * 87)
        for ds in schema_result.results:
            title = ds.title[:37] + "..." if len(ds.title) > 40 else ds.title
            sc = "Y" if ds.single_cell else "N"
            pert = "Y" if ds.has_perturbation else "N"
            # 规范化 ID 展示：移除 SRP: 前缀，用 Accession 列显示类型
            acc = ds.gse_id
            if acc.startswith("SRP:") or acc.startswith("ERP:") or acc.startswith("DRP:"):
                acc = acc.split(":")[1] if ":" in acc else acc
            # Samples 为 0 显示为 —
            samples = str(ds.sample_count) if ds.sample_count > 0 else "—"
            click.echo(f"{acc:<12} {ds.data_type:<12} {sc:<4} {pert:<5} {samples:<8} {title:<40}")

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
        except Exception as e:
            logger.error(f"Database save failed: {e}")
            click.echo(f"\n[ERROR] Failed to save to database: {e}")


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

    支持 JSON 输出（标准 Schema 格式），可与 gse-downloader 集成。
    """
    import json as json_mod

    from sra_search.converter import record_to_schema
    from sra_search.data_store.database import get_database

    db = get_database()
    ds = db.get_dataset(gse_id)
    if ds is None:
        click.echo(f"Dataset '{gse_id}' not found")
        return

    if fmt == "json":
        # JSON Schema 输出
        schema = record_to_schema(ds, query="")
        click.echo(json_mod.dumps(schema.to_dict(), ensure_ascii=False, indent=2))
    else:
        # 表格形式
        click.echo(f"\n{'='*60}")
        click.echo(f"  {ds.gse_id}")
        click.echo(f"{'='*60}")
        click.echo(f"  Title:           {ds.title}")
        click.echo(f"  Organism:        {ds.organism}")
        click.echo(f"  Disease:         {ds.disease or '-'}")
        click.echo(f"  Organ:           {ds.organ or '-'}")
        click.echo(f"  Omics Type:      {ds.omics_type or '-'}")
        click.echo(f"  Granularity:     {ds.omics_granularity}")
        click.echo(f"  Sample Count:    {ds.sample_count}")
        click.echo(f"  Platform:        {ds.platform or '-'}")
        click.echo(f"  Journal:         {ds.journal or '-'}")
        click.echo(f"  Publication:     {ds.publication_date or '-'}")
        click.echo(f"  PubMed IDs:      {', '.join(ds.pubmed_ids) or '-'}")
        click.echo(f"  SRA IDs:         {', '.join(ds.sra_ids) or '-'}")
        click.echo(f"  BioProject IDs:  {', '.join(ds.bioproject_ids) or '-'}")
        click.echo(f"  Availability:    {ds.availability_status}")
        if ds.availability_note:
            click.echo(f"  Avail Note:      {ds.availability_note}")
        click.echo(f"  Access Type:     {ds.access_type}")
        click.echo(f"  Has GSE:         {'Yes' if ds.has_gse else 'No'}")
        click.echo(f"  Version:         {ds.version}")
        click.echo(f"  First Seen:      {ds.first_seen_at}")
        click.echo(f"  Last Updated:    {ds.last_updated}")
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


@main.command()
@click.argument("name")
@click.option("--description", "-d", default="", help="主题描述")
@click.option("--keywords", "-k", default="", help="额外关键词（逗号分隔）")
def topic(name: str, description: str, keywords: str):
    """主题式搜索（开发中）"""
    click.echo(f"Topic search '{name}' is under development.")
    click.echo("Currently, use 'sra-search search' for keyword-based search.")


@main.command()
@click.argument("accession")
def convert(accession: str):
    """编号转换（开发中）"""
    click.echo(f"ID conversion for '{accession}' is under development.")


@main.command()
@click.argument("gse_id", required=False)
@click.option("--topic", "-t", help="检查某主题下所有数据集")
@click.option("--all", "check_all", is_flag=True, help="检查全部未验证数据集")
@click.option("--recheck", is_flag=True, help="重新检查已有状态的数据集")
def check(gse_id: str | None, topic: str | None, check_all: bool, recheck: bool):
    """数据集可用性检查（开发中）"""
    if gse_id:
        click.echo(f"Availability check for '{gse_id}' is under development.")
    elif topic:
        click.echo(f"Batch check for topic '{topic}' is under development.")
    elif check_all or recheck:
        click.echo("Full availability check is under development.")
    else:
        click.echo("Use: sra-search check <GSE_ID> [--topic TOPIC] [--all] [--recheck]")


@main.command()
@click.option("--topic", "-t", help="更新指定主题")
def update(topic: str | None):
    """手动触发更新（开发中）"""
    if topic:
        click.echo(f"Update for topic '{topic}' is under development.")
    else:
        click.echo("Full update is under development.")


if __name__ == "__main__":
    main()
