"""检索模块（Retriever Pipeline）

职责：
- geo_api: GEO API 封装（E-utilities）
- 与 search_engine 解耦

设计原则：
- 单一职责
- API 抽象层
- 失败处理
"""
from sra_search.retriever.geo_api import GeoRetriever

__all__ = ["GeoRetriever"]