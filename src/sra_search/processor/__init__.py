"""处理模块（Processor Pipeline）

职责：
- filter: 结果过滤
- ranking: 结果排序（Bio-aware Retrieval）

设计原则：
- 单一职责
- 可组合
- 评分函数可配置
"""
from sra_search.processor.filter import ResultFilter
from sra_search.processor.ranking import BioAwareRanker

__all__ = ["ResultFilter", "BioAwareRanker"]
