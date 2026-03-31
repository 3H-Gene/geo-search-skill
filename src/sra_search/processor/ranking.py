"""Bio-aware Ranker - 生物信息学感知排序器

功能：
- 基于多维度评分（relevance + recency + perturbation_bonus + single_cell_bonus + sample_size）
- 可配置的权重
- perturbation-aware 排序（加分项）

设计原则：
- 单一职责
- 评分函数可配置
- 评分因子明确、可解释
"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# === Perturbation 类型加权 ===
PERTURBATION_WEIGHTS = {
    "CRISPR": 0.15,  # 高价值
    "knockout": 0.12,
    "knockdown": 0.10,
    "drug": 0.08,
    "stimulation": 0.06,
    "overexpression": 0.06,
    "siRNA": 0.05,
    "chemical": 0.04,
    "radiation": 0.04,
}

# === 组学类型加权 ===
OMICS_WEIGHTS = {
    "scRNA-seq": 0.10,  # 单细胞加权
    "snRNA-seq": 0.10,
    "scATAC-seq": 0.08,
    "spatial": 0.08,
    "ATAC-seq": 0.05,
    "ChIP-seq": 0.04,
    "RNA-seq": 0.03,
    "WGS": 0.03,
    "WES": 0.02,
    "proteomics": 0.02,
}

# === 物种加权 ===
ORGANISM_WEIGHTS = {
    "Homo sapiens": 0.05,  # 人类数据加权
    "Mus musculus": 0.03,
    "Rattus norvegicus": 0.02,
}


@dataclass
class RankingWeights:
    """排序权重配置"""
    relevance: float = 0.30  # 相关性
    recency: float = 0.15  # 时效性
    quality: float = 0.15  # 数据质量
    sample_size: float = 0.15  # 样本数
    perturbation: float = 0.15  # perturbation 加分
    single_cell: float = 0.10  # 单细胞加分


@dataclass
class ScoreBreakdown:
    """单个记录的评分明细"""
    gse_id: str
    total_score: float = 0.0

    # 各维度分数
    relevance_score: float = 0.0
    recency_score: float = 0.0
    quality_score: float = 0.0
    sample_size_score: float = 0.0
    perturbation_bonus: float = 0.0
    single_cell_bonus: float = 0.0

    # 加分详情
    omics_bonus: float = 0.0
    organism_bonus: float = 0.0


class BioAwareRanker:
    """生物信息学感知排序器

    评分函数：
    score = (
        relevance * relevance_score +
        recency * recency_score +
        quality * quality_score +
        sample_size * sample_size_score +
        perturbation * perturbation_bonus +
        single_cell * single_cell_bonus
    )
    """

    def __init__(self, weights: Optional[RankingWeights] = None):
        """初始化

        Args:
            weights: 排序权重配置，默认值已优化
        """
        self.weights = weights or RankingWeights()

        # Perturbation 关键词（用于识别）
        self.perturbation_patterns = {
            "CRISPR": re.compile(r"crispr|cas9|cas12", re.I),
            "knockout": re.compile(r"knockout|ko|null|knock-out", re.I),
            "knockdown": re.compile(r"knockdown|kd|sirna|rnai|knock-down", re.I),
            "drug": re.compile(r"drug|compound|inhibitor|therapy|chemotherapy|treatment", re.I),
            "stimulation": re.compile(r"stimulation|stimulate|induced|agonist", re.I),
            "overexpression": re.compile(r"overexpression|over-express|oe|transgenic", re.I),
            "siRNA": re.compile(r"sirna|small interfering", re.I),
            "chemical": re.compile(r"chemical|toxicity|exposure|pollutant", re.I),
            "radiation": re.compile(r"radiation|irradiation|uv|gamma", re.I),
        }

        # 单细胞关键词
        self.sc_patterns = [
            re.compile(r"single.?cell", re.I),
            re.compile(r"scrna", re.I),
            re.compile(r"10x", re.I),
            re.compile(r"drop-seq", re.I),
            re.compile(r"smart-seq", re.I),
            re.compile(r"cel-seq", re.I),
        ]

    def rank(
        self,
        records: List[Dict[str, Any]],
        query: str = "",
        top_n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """排序记录

        Args:
            records: 记录列表（每条记录为字典）
            query: 原始查询（用于相关性计算）
            top_n: 返回前 N 个，None 表示全部

        Returns:
            排序后的记录列表
        """
        if not records:
            return []

        # 计算每条记录的分数
        scored_records = []
        for record in records:
            score_breakdown = self._calculate_score(record, query)
            record["total_score"] = score_breakdown.total_score
            record["_score_breakdown"] = score_breakdown
            scored_records.append(record)

        # 按总分排序
        scored_records.sort(key=lambda x: x.total_score, reverse=True)

        # 截取 top N
        if top_n is not None and top_n > 0:
            scored_records = scored_records[:top_n]

        # 移除内部字段
        return [self._clean_record(r) for r in scored_records]

    def _calculate_score(self, record: Dict[str, Any], query: str) -> ScoreBreakdown:
        """计算单条记录的分数"""
        gse_id = record.get("gse_id", "")
        breakdown = ScoreBreakdown(gse_id=gse_id)

        # 1. Relevance Score（相关性）
        breakdown.relevance_score = self._calc_relevance(record, query)

        # 2. Recency Score（时效性）
        breakdown.recency_score = self._calc_recency(record)

        # 3. Quality Score（数据质量）
        breakdown.quality_score = record.get("quality_score", 0.5)

        # 4. Sample Size Score（样本数）
        breakdown.sample_size_score = self._calc_sample_size(record)

        # 5. Perturbation Bonus（扰动数据加分）
        breakdown.perturbation_bonus = self._calc_perturbation_bonus(record)

        # 6. Single Cell Bonus（单细胞加分）
        breakdown.single_cell_bonus = self._calc_single_cell_bonus(record)

        # 7. Omics Bonus（组学类型加权）
        breakdown.omics_bonus = self._calc_omics_bonus(record)

        # 8. Organism Bonus（物种加权）
        breakdown.organism_bonus = self._calc_organism_bonus(record)

        # 计算总分
        w = self.weights
        breakdown.total_score = (
            w.relevance * breakdown.relevance_score +
            w.recency * breakdown.recency_score +
            w.quality * breakdown.quality_score +
            w.sample_size * breakdown.sample_size_score +
            w.perturbation * breakdown.perturbation_bonus +
            w.single_cell * breakdown.single_cell_bonus +
            breakdown.omics_bonus +
            breakdown.organism_bonus
        )

        # 归一化到 [0, 1]
        breakdown.total_score = min(breakdown.total_score, 1.0)

        return breakdown

    def _calc_relevance(self, record: Dict[str, Any], query: str) -> float:
        """计算相关性分数"""
        if not query:
            return 0.5

        query_lower = query.lower()
        title = record.get("title", "").lower()
        summary = record.get("summary", "").lower()
        keywords = " ".join(record.get("keywords", [])).lower()

        # 计算查询词在字段中的出现次数
        query_terms = query_lower.split()
        matches = 0

        for term in query_terms:
            if term in title:
                matches += 2  # 标题中匹配权重更高
            if term in summary:
                matches += 1
            if term in keywords:
                matches += 1

        # 归一化
        max_matches = len(query_terms) * 4
        return min(matches / max_matches, 1.0) if max_matches > 0 else 0.5

    def _calc_recency(self, record: Dict[str, Any]) -> float:
        """计算时效性分数

        越新的数据分数越高
        """
        pub_date = record.get("publication_date", "")
        if not pub_date:
            return 0.5

        try:
            # 尝试解析日期
            if len(pub_date) >= 4:
                year = int(pub_date[:4])
                current_year = datetime.now().year
                age = current_year - year

                # 0-1 年: 1.0
                # 1-2 年: 0.9
                # 2-3 年: 0.7
                # 3-5 年: 0.5
                # 5-10 年: 0.3
                # >10 年: 0.1

                if age <= 1:
                    return 1.0
                elif age <= 2:
                    return 0.9
                elif age <= 3:
                    return 0.7
                elif age <= 5:
                    return 0.5
                elif age <= 10:
                    return 0.3
                else:
                    return 0.1

        except (ValueError, TypeError):
            pass

        return 0.5

    def _calc_sample_size(self, record: Dict[str, Any]) -> float:
        """计算样本数分数

        使用 log scale 避免大样本主导
        """
        sample_count = record.get("sample_count", 0)
        if sample_count <= 0:
            return 0.0

        # log(样本数 + 1) / log(10000 + 1) 归一化到 [0, 1]
        # 假设 10000 样本为满分
        log_score = math.log(sample_count + 1) / math.log(10001)
        return min(log_score, 1.0)

    def _calc_perturbation_bonus(self, record: Dict[str, Any]) -> float:
        """计算 perturbation 加分"""
        # 检查标记字段
        if record.get("has_perturbation"):
            pert_types = record.get("perturbation_types", [])
            if pert_types:
                # 取最高权重
                max_weight = max(
                    PERTURBATION_WEIGHTS.get(t, 0) for t in pert_types
                )
                return max_weight

        # 从标题/摘要中检测
        title = record.get("title", "")
        summary = record.get("summary", "")

        text = f"{title} {summary}".lower()
        detected_types = []

        for pert_type, pattern in self.perturbation_patterns.items():
            if pattern.search(text):
                detected_types.append(pert_type)

        if detected_types:
            # 返回最高权重
            return max(PERTURBATION_WEIGHTS.get(t, 0) for t in detected_types)

        return 0.0

    def _calc_single_cell_bonus(self, record: Dict[str, Any]) -> float:
        """计算单细胞加分"""
        # 检查标记字段
        if record.get("single_cell"):
            return 0.10

        # 从标题/摘要中检测
        title = record.get("title", "")
        summary = record.get("summary", "")

        text = f"{title} {summary}".lower()

        for pattern in self.sc_patterns:
            if pattern.search(text):
                return 0.10

        return 0.0

    def _calc_omics_bonus(self, record: Dict[str, Any]) -> float:
        """计算组学类型加权"""
        data_type = record.get("data_type", "")
        if not data_type:
            return 0.0

        return OMICS_WEIGHTS.get(data_type, 0.0)

    def _calc_organism_bonus(self, record: Dict[str, Any]) -> float:
        """计算物种加权"""
        organism = record.get("organism", "")
        if not organism:
            return 0.0

        return ORGANISM_WEIGHTS.get(organism, 0.0)

    def _clean_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """清理记录（移除内部字段）"""
        cleaned = {k: v for k, v in record.items() if not k.startswith("_")}
        return cleaned

    def get_score_breakdown(self, record: Dict[str, Any]) -> Optional[ScoreBreakdown]:
        """获取记录的评分明细"""
        return record.get("_score_breakdown")


# === 预设排序器 ===

def create_default_ranker() -> BioAwareRanker:
    """创建默认排序器"""
    return BioAwareRanker()


def create_recent_first_ranker() -> BioAwareRanker:
    """创建按时间排序的排序器"""
    weights = RankingWeights(
        relevance=0.20,
        recency=0.40,  # 更强调时效性
        quality=0.15,
        sample_size=0.10,
        perturbation=0.10,
        single_cell=0.05,
    )
    return BioAwareRanker(weights)


def create_perturbation_centric_ranker() -> BioAwareRanker:
    """创建 perturbation 优先的排序器"""
    weights = RankingWeights(
        relevance=0.20,
        recency=0.10,
        quality=0.15,
        sample_size=0.10,
        perturbation=0.35,  # 更强调 perturbation
        single_cell=0.10,
    )
    return BioAwareRanker(weights)
