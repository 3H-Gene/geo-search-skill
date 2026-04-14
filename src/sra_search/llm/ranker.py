"""LLM 语义评分器

使用 LLM 对数据集与用户查询进行语义相关性评分，
替代/增强 converter.py 中的关键词匹配评分。

设计原则：
- LLM 不可用时自动回退到 V1 关键词评分
- 仅对 top_k 个候选做 LLM 评分（节省 token 成本）
- 内存缓存（query+dataset_hash → score），TTL = llm_cache_ttl_hours
- 超时/格式错误时回退，不中断主流程
"""
from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from sra_search.llm.client import LLMClient
    from sra_search.schema import DatasetSchema


# ── 评分 Prompt ──────────────────────────────────────────────────────────────

_RANKER_SYSTEM_PROMPT = """You are a biomedical research data curator specializing in genomics and transcriptomics datasets.
Your job is to score how relevant a dataset is to a research query. Output ONLY a decimal between 0.000 and 1.000 (e.g., "0.850"). No explanation, no markdown, no extra text."""

_RANKER_USER_TEMPLATE = """Rate the relevance of this dataset to the research query.

RESEARCH QUERY: {query}

DATASET METADATA:
- Title: {title}
- Summary: {summary}
- Organism: {organism}
- Data type: {data_type}   (scRNA-seq > RNA-seq > microarray for single-cell queries)
- Granularity: {granularity}
- Disease/Condition: {disease}
- Tissue/Organ: {tissue}
- Perturbation: {has_perturbation}{perturbation_types}
- Sample count: {sample_count} samples
- Year: {year}
- Keywords: {keywords}

SCORING GUIDELINES:
1. Disease match (most important): Does the dataset study the same disease/condition?
2. Technology match: scRNA-seq queries should heavily favor single-cell datasets
3. Biological specificity: tissue/organ/cell-type alignment with query
4. Perturbation context: datasets with clear experimental perturbation > observational
5. Sample size: larger cohorts (>100 samples) more valuable for discovery research
6. Recency: prefer newer datasets (>2018) for current methods relevance
7. Keywords: topically relevant keywords in title/summary boost score

Output ONLY a number between 0.000 and 1.000."""


def _build_rank_prompt(query: str, dataset: DatasetSchema) -> str:
    """构建评分 prompt"""
    summary = dataset.summary or ""
    if len(summary) > 500:
        summary = summary[:500] + "..."

    perturbation_types_str = ""
    if dataset.perturbation_types:
        perturbation_types_str = " (" + ", ".join(dataset.perturbation_types) + ")"

    # 从 publication_date 提年份
    year = "unknown"
    if dataset.publication_date:
        year = dataset.publication_date[:4]

    keywords_str = ", ".join(dataset.keywords[:10]) if dataset.keywords else "none"

    return _RANKER_USER_TEMPLATE.format(
        query=query,
        title=dataset.title or "(no title)",
        summary=summary or "(no summary)",
        organism=dataset.organism or "unknown",
        data_type=dataset.data_type or "unknown",
        granularity=dataset.granularity or "unknown",
        disease=dataset.disease or "unknown",
        tissue=dataset.tissue or dataset.organ or "unknown",
        has_perturbation="Yes" if dataset.has_perturbation else "No",
        perturbation_types=perturbation_types_str,
        sample_count=dataset.sample_count or 0,
        year=year,
        keywords=keywords_str,
    )


def _parse_score(text: str | None) -> float | None:
    """从 LLM 响应中解析 0-1 分数"""
    if not text:
        return None
    text = text.strip()
    # 只取第一个数字（防止 LLM 多输出内容）
    import re
    matches = re.findall(r"\d+\.?\d*", text)
    if not matches:
        return None
    try:
        score = float(matches[0])
        # 如果 LLM 给出了 0-100 的分数（偶尔会误输出）：
        # 任何 > 1.0 的数字都是 0-100 范围的输出，需要 /100 缩放到 0-1 范围
        # 边界值如 1.0 保持不变（视为 1.0）
        if score > 1.0:
            score = score / 100.0
        return max(0.0, min(1.0, score))
    except ValueError:
        return None


class LLMRanker:
    """LLM 语义评分器

    Args:
        client: LLM 客户端。若 None，从 settings 自动初始化。
        cache_ttl_hours: 评分缓存有效期（小时），0 表示禁用缓存
    """

    def __init__(
        self,
        client: LLMClient | None = None,
        cache_ttl_hours: int = 168,
    ) -> None:
        if client is None:
            from sra_search.llm.client import LLMClient as _LLMClient
            client = _LLMClient.from_config()
        self.client = client
        self.cache_ttl_hours = cache_ttl_hours
        # cache: {cache_key: (score, expire_ts)}
        self._cache: dict[str, tuple[float, float]] = {}

    def is_available(self) -> bool:
        """检查 LLM 评分是否可用"""
        return self.client.is_available()

    def _cache_key(self, query: str, dataset: DatasetSchema) -> str:
        """构建缓存 key"""
        raw = f"{query}||{dataset.gse_id}||{dataset.title[:50]}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    def _get_cached(self, key: str) -> float | None:
        if key not in self._cache:
            return None
        score, expire_ts = self._cache[key]
        if time.time() > expire_ts:
            del self._cache[key]
            return None
        return score

    def _set_cached(self, key: str, score: float) -> None:
        expire_ts = time.time() + self.cache_ttl_hours * 3600
        self._cache[key] = (score, expire_ts)

    async def score_batch(
        self,
        datasets: list[DatasetSchema],
        query: str,
        top_k: int = 20,
        concurrency: int = 5,
        min_relevance: float = 0.0,
        score_all: bool = False,
    ) -> list[tuple[DatasetSchema, float]]:
        """批量 LLM 语义评分。

        Args:
            datasets: 候选数据集列表（已经过 V1 初步排序）
            query: 用户查询词
            top_k: 最多对前 top_k 个数据集做 LLM 评分
            concurrency: 并发请求数
            min_relevance: 仅对 relevance_score >= 此值的数据集做 LLM 评分（默认 0）
            score_all: 若 True，忽略 top_k 限制，对所有通过 min_relevance 的数据集评分

        Returns:
            (dataset, llm_score) 列表，按分数降序排列。
            未通过 min_relevance 的数据集用 V1 分数兜底。
        """
        if not self.is_available() or not datasets:
            return []

        # ── 预过滤 + 候选集选择 ────────────────────────────────────────────────
        # 1. min_relevance 过滤：relevance_score == 0 的数据集跳过 LLM（节省 token）
        candidates = [ds for ds in datasets if ds.relevance_score >= min_relevance]

        # 2. score_all=False 时：只评前 top_k 个（节省成本）；score_all=True 时评全部
        if not score_all and len(candidates) > top_k:
            candidates = candidates[:top_k]

        # 3. 剩余的（未进入 LLM 评分）用 V1 分数保底
        scored_ids = {ds.gse_id for ds in candidates}
        remaining = [ds for ds in datasets if ds.gse_id not in scored_ids]

        prompts: list[str] = []
        hit_indices: list[int] = []     # 需要调用 LLM 的下标
        cached_scores: dict[int, float] = {}  # 已缓存的 index → score

        for i, ds in enumerate(candidates):
            key = self._cache_key(query, ds)
            cached = self._get_cached(key)
            if cached is not None:
                cached_scores[i] = cached
            else:
                hit_indices.append(i)
                prompts.append(_build_rank_prompt(query, ds))

        # 调用 LLM（只调未缓存的）
        llm_responses: list[str | None] = []
        if prompts:
            import asyncio

            cache_hits = len(cached_scores)
            total = len(prompts)
            skipped = len(datasets) - len(candidates)
            logger.info(
                f"[LLM] Ranking {total} datasets "
                f"({cache_hits} from cache, {skipped} skipped by min_relevance={min_relevance}, "
                f"{len(remaining)} use V1 score) "
                f"| concurrency={concurrency} | query={query!r}"
            )
            try:
                # 分批并发调用，每批后 yield 让事件循环处理进度日志
                batch_size = concurrency
                llm_responses = [None] * total
                for batch_start in range(0, total, batch_size):
                    batch_end = min(batch_start + batch_size, total)
                    batch_prompts = prompts[batch_start:batch_end]
                    batch_tasks = [
                        self.client.achat(
                            prompt=p,
                            system=_RANKER_SYSTEM_PROMPT,
                            temperature=0.2,  # 适度区分度，避免所有相关数据集都输出相似高分
                            max_tokens=64,  # 浮点数 0.850 约需 ~10 tokens，64 留足余量
                        )
                        for p in batch_prompts
                    ]
                    batch_results = await asyncio.gather(*batch_tasks)
                    for j, result in enumerate(batch_results):
                        llm_responses[batch_start + j] = result
                    if batch_end < total:
                        logger.info(
                            f"[LLM] Progress: {batch_end}/{total} scored..."
                        )
                        await asyncio.sleep(0)  # yield 让 loguru 写日志
                logger.info(f"[LLM] Ranking complete: {total} scores received")
            except Exception as e:
                exc_type = type(e).__name__
                if "RateLimitError" in exc_type:
                    logger.warning(f"[LLM] Batch scoring rate limited: {e}. Consider retry later.")
                elif "AuthenticationError" in exc_type or "401" in str(e):
                    logger.error(f"[LLM] Batch scoring auth failed: {e}. Check API key.")
                elif "Timeout" in exc_type:
                    logger.warning(f"[LLM] Batch scoring timeout: {e}")
                elif "APIConnectionError" in exc_type:
                    logger.warning(f"[LLM] Batch scoring connection error: {e}")
                else:
                    logger.warning(f"[LLM] Batch scoring failed: {exc_type}: {e}")
                return []
        else:
            logger.info(
                f"[LLM] All {len(cached_scores)} scores from cache, skipping API call"
            )

        # 聚合结果
        results: list[tuple[DatasetSchema, float]] = []

        for i, ds in enumerate(candidates):
            if i in cached_scores:
                score = cached_scores[i]
            else:
                resp_idx = hit_indices.index(i)
                raw_text = llm_responses[resp_idx] if resp_idx < len(llm_responses) else None
                parsed = _parse_score(raw_text)
                if parsed is None:
                    # 解析失败，用 V1 分数作为保底
                    score = ds.relevance_score
                    logger.debug(
                        f"LLM score parse failed for {ds.gse_id!r} "
                        f"(response: {raw_text!r}). Using V1 score={score:.3f}"
                    )
                else:
                    score = parsed
                    # 写入缓存
                    self._set_cached(self._cache_key(query, ds), score)

            results.append((ds, score))

        # remaining 使用 V1 分数
        for ds in remaining:
            results.append((ds, ds.relevance_score))

        # 按 LLM 分数降序排列
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    async def score_single(self, dataset: DatasetSchema, query: str) -> float | None:
        """对单个数据集评分，失败返回 None。"""
        results = await self.score_batch([dataset], query, top_k=1)
        if not results:
            return None
        return results[0][1]
