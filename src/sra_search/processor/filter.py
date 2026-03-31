"""Result Filter - 结果过滤器

功能：
- 按物种、组学类型、数据质量过滤
- 按 perturbation、single-cell 筛选

设计原则：
- 单一职责
- 过滤器可组合
- 明确的包含/排除规则
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass
class FilterRule:
    """过滤规则"""
    field: str  # 要过滤的字段
    allowed_values: Optional[Set[str]] = None  # 允许的值（None 表示不限制）
    excluded_values: Optional[Set[str]] = None  # 排除的值
    min_value: Optional[int] = None  # 最小值（用于数值字段）
    max_value: Optional[int] = None  # 最大值

    def matches(self, record: Dict[str, Any]) -> bool:
        """检查记录是否匹配规则"""
        value = record.get(self.field)

        # 检查排除
        if self.excluded_values and value in self.excluded_values:
            return False

        # 检查允许
        if self.allowed_values is not None:
            if value not in self.allowed_values:
                return False

        # 检查数值范围
        if self.min_value is not None and isinstance(value, (int, float)):
            if value < self.min_value:
                return False

        if self.max_value is not None and isinstance(value, (int, float)):
            if value > self.max_value:
                return False

        return True


class ResultFilter:
    """结果过滤器

    支持多维度过滤：物种、组学类型、单细胞、perturbation 等
    """

    def __init__(self):
        self.rules: List[FilterRule] = []
        self.custom_filters: List[Callable[[Dict], bool]] = []

    def add_rule(self, rule: FilterRule) -> "ResultFilter":
        """添加过滤规则（链式调用）"""
        self.rules.append(rule)
        return self

    def add_custom(self, filter_fn: Callable[[Dict], bool]) -> "ResultFilter":
        """添加自定义过滤函数"""
        self.custom_filters.append(filter_fn)
        return self

    def filter_organism(self, organism: str) -> "ResultFilter":
        """按物种过滤"""
        return self.add_rule(FilterRule(
            field="organism",
            allowed_values={organism} if organism else None,
        ))

    def filter_omics_type(self, omics_type: str) -> "ResultFilter":
        """按组学类型过滤"""
        return self.add_rule(FilterRule(
            field="data_type",
            allowed_values={omics_type} if omics_type else None,
        ))

    def filter_single_cell(self, require: bool = True) -> "ResultFilter":
        """过滤单细胞数据"""
        return self.add_rule(FilterRule(
            field="single_cell",
            allowed_values={require} if require else None,
        ))

    def filter_perturbation(self, require: bool = True) -> "ResultFilter":
        """过滤 perturbation 数据"""
        return self.add_rule(FilterRule(
            field="has_perturbation",
            allowed_values={require} if require else None,
        ))

    def filter_min_samples(self, min_samples: int) -> "ResultFilter":
        """过滤最小样本数"""
        return self.add_rule(FilterRule(
            field="sample_count",
            min_value=min_samples,
        ))

    def filter_by_score(self, min_score: float = 0.5) -> "ResultFilter":
        """按质量分数过滤"""
        return self.add_rule(FilterRule(
            field="quality_score",
            min_value=min_score,
        ))

    def apply(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """应用过滤

        Args:
            records: 输入记录列表

        Returns:
            过滤后的记录列表
        """
        filtered = []

        for record in records:
            # 应用规则
            match = True
            for rule in self.rules:
                if not rule.matches(record):
                    match = False
                    break

            # 应用自定义过滤
            if match:
                for custom_fn in self.custom_filters:
                    if not custom_fn(record):
                        match = False
                        break

            if match:
                filtered.append(record)

        return filtered

    def count_after_filter(self, total: int, filtered: int) -> Dict[str, int]:
        """生成过滤统计"""
        return {
            "total": total,
            "after_filter": filtered,
            "filtered_out": total - filtered,
        }

    def clear(self) -> "ResultFilter":
        """清除所有规则"""
        self.rules.clear()
        self.custom_filters.clear()
        return self


# === 预设过滤器 ===

def create_quality_filter(min_quality: float = 0.3) -> ResultFilter:
    """创建质量过滤器"""
    return ResultFilter().filter_by_score(min_quality)


def create_scRNA_filter() -> ResultFilter:
    """创建 scRNA-seq 过滤器"""
    return ResultFilter().filter_single_cell(require=True)


def create_perturbation_filter() -> ResultFilter:
    """创建 perturbation 数据过滤器"""
    return ResultFilter().filter_perturbation(require=True)


def create_human_filter() -> ResultFilter:
    """创建人类数据过滤器"""
    return ResultFilter().filter_organism("Homo sapiens")
