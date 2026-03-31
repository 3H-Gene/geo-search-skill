"""查询处理模块（Query Pipeline）

职责：
- query parser: 解析用户输入为结构化查询
- query expander: 使用本体知识扩展查询词

设计原则：
- 单一职责
- 可组合
- 失败处理（无结果/结果过多/查询模糊）
"""
from sra_search.query.parser import QueryParser
from sra_search.query.expander import QueryExpander

__all__ = ["QueryParser", "QueryExpander"]
