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
import sys
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import click
from loguru import logger

from sra_search.config import get_settings, reset_settings
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
def main(verbose: bool = False, config: Optional[str] = None):
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
@click.option("--save/--no-save", default=True, help="是否保存到数据库")
def search(keyword: str, sources: tuple, retmax: Optional[int], fmt: str, top: int, save: bool):
    """关键词搜索数据集

    支持三种输出格式：
    - table: 表格形式（默认）
    - json: 标准 JSON Schema 输出（与 gse-downloader 解耦）
    - id-list: 仅 GSE ID 列表（适合管道处理）

    示例：
      sra-search search "breast cancer scRNA-seq" --format json --top 20
      sra-search search "liver fibrosis" --sources geo --format id-list
    """
    import json
    from sra_search.converter import records_to_search_result
    from sra_search.search_engine.aggregator import SearchAggregator

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
            )
            return results
        finally:
            # 关闭 aiohttp session，避免 "Unclosed client session" 警告
            await client.close()

    search_results = run_async(_do_search())

    if not search_results:
        click.echo(f"No datasets found for '{keyword}'")
        return

    # 提取 DatasetRecord 列表
    records = [r.dataset for r in search_results]

    # 转换为 Schema 并排序
    schema_result = records_to_search_result(records, query=keyword, top_n=top)

    # 输出
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
                acc_type = acc.split(":")[0].lower()
                acc = acc.split(":")[1] if ":" in acc else acc
            else:
                acc_type = "gse"
            # Samples 为 0 显示为 —
            samples = str(ds.sample_count) if ds.sample_count > 0 else "—"
            click.echo(f"{acc:<12} {ds.data_type:<12} {sc:<4} {pert:<5} {samples:<8} {title:<40}")

        # 统计摘要（区分来源）
        stats = schema_result.compute_stats()
        click.echo(f"\n--- Summary ---")
        click.echo(f"Total: {stats['total_found']} | scRNA-seq: {stats['scRNA_seq']} | with perturbation: {stats['with_perturbation']}")
        # 说明各源合并情况（当有多个数据源时）
        click.echo(f"\nNote: Results are deduplicated across GEO, SRA, and PubMed sources.")
        click.echo(f"PubMed branch links publications to GEO; not all papers have linked datasets.")

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
def list_cmd(topic: Optional[str], list_all: bool, status: Optional[str],
             availability: Optional[str], limit: int, offset: int, fmt: str):
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
    click.echo(f"\n{'='*50}")
    click.echo("  SRA_search Configuration")
    click.echo(f"{'='*50}")
    click.echo(f"  NCBI Email:      {'[OK] ' + settings.ncbi_email if settings.ncbi_email else '[NOT SET]'}")
    click.echo(f"  NCBI API Key:    {'[OK] ' + settings.ncbi_api_key[:8] + '...' if settings.ncbi_api_key else '[NOT SET]'}")
    click.echo(f"  Rate Limit:      {settings.effective_rate_limit} req/s")
    click.echo(f"  Database:        {settings.db_path_resolved}")
    click.echo(f"  WAL Mode:        {'Enabled' if settings.db_wal_enabled else 'Disabled'}")
    click.echo(f"  Min Samples:     {settings.availability_min_samples}")
    click.echo(f"  Log Level:       {settings.log_level}")
    click.echo(f"{'='*50}\n")

    if not settings.ncbi_email:
        click.echo("To configure, set environment variables:")
        click.echo("   export SRA_SEARCH_NCBI_EMAIL=your@email.com")
        click.echo("   export SRA_SEARCH_NCBI_API_KEY=your_api_key")


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
def check(gse_id: Optional[str], topic: Optional[str], check_all: bool, recheck: bool):
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
def update(topic: Optional[str]):
    """手动触发更新（开发中）"""
    if topic:
        click.echo(f"Update for topic '{topic}' is under development.")
    else:
        click.echo("Full update is under development.")


if __name__ == "__main__":
    main()
