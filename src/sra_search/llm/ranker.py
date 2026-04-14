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

_RANKER_SYSTEM_PROMPT = """You are a bioinformatics expert who evaluates the relevance of genomics/transcriptomics datasets.
Your task is to score how relevant a dataset is to a user's search query.
Always respond with ONLY a decimal number between 0.000 and 1.000 (e.g., "0.850").
Do not include any explanation or other text."""

_RANKER_USER_TEMPLATE = """Rate the relevance of this dataset to the user's query.

USER QUERY: {query}

DATASET TITLE: {title}

DATASET SUMMARY: {summary}

ADDITIONAL INFO:
- Organism: {organism}
- Data type: {data_type}
- Granularity: {granularity}
- Disease/condition: {disease}
- Tissue/organ: {tissue}
- Has perturbation: {has_perturbation}

Consider:
1. Does the dataset study the disease/condition in the query?
2. Does the sequencing method/platform match? (e.g., if query asks for single-cell, single-cell data scores higher)
3. Does the organism match?
4. Does the tissue/cell type match?

Output ONLY a number between 0.000 and 1.000."""


def _build_rank_prompt(query: str, dataset: DatasetSchema) -> str:
    """构建评分 prompt"""
    summary = dataset.summary or ""
    if len(summary) > 500:
        summary = summary[:500] + "..."

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
        # 仅当分数明显大于 1（>=2）时才做 /100 缩放，避免误伤边界值（如 1.5）
        if score >= 2.0:
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
    ) -> list[tuple[DatasetSchema, float]]:
        """批量 LLM 语义评分。

        仅对 top_k 个数据集做 LLM 评分（节省成本）。
        失败时返回空列表，调用方应回退到 V1 评分。

        Args:
            datasets: 候选数据集列表（已经过 V1 初步排序）
            query: 用户查询词
            top_k: 最多对前 top_k 个数据集做 LLM 评分
            concurrency: 并发请求数

        Returns:
            (dataset, llm_score) 列表，按分数降序排列
        """
        if not self.is_available() or not datasets:
            return []

        # 只评前 top_k 个，减少成本
        candidates = datasets[:top_k]
        remaining = datasets[top_k:]

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
            cache_hits = len(cached_scores)
            logger.info(
                f"[LLM] Ranking {len(prompts)} datasets "
                f"({cache_hits} from cache, {len(remaining)} use V1 score) "
                f"| query={query!r}"
            )
            try:
                llm_responses = await self.client.abatch_chat(
                    prompts=prompts,
                    system=_RANKER_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_tokens=16,  # 分数只需要 "0.850" 这样的字符
                    concurrency=concurrency,
                )
                logger.info(
                    f"[LLM] Ranking complete: {len(prompts)} scores received"
                )
            except Exception as e:
                logger.warning(f"[LLM] Batch scoring failed: {e}. Falling back to V1.")
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
