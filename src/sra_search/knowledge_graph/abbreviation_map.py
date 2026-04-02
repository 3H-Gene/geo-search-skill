"""生物医学缩写映射器

将用户输入的缩写（如 MASH, T2D, HCC）扩展为完整术语和关联搜索词。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from sra_search.knowledge_graph._paths import DATA_DIR as _DATA_DIR


class AbbreviationMapper:
    """生物医学缩写 -> 全称 + 关联词映射器"""

    def __init__(self, data_path: Optional[Path] = None):
        self._data: Dict[str, Any] = {}
        self._loaded = False
        self._data_path = data_path or _DATA_DIR / "abbreviation_map.json"

    def _load(self) -> None:
        """懒加载缩写数据"""
        if self._loaded:
            return
        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._data = raw.get("abbreviations", {})
            self._loaded = True
            logger.debug(f"Loaded {len(self._data)} abbreviation entries")
        except FileNotFoundError:
            logger.warning(f"Abbreviation map not found: {self._data_path}")
            self._data = {}
            self._loaded = True

    def resolve(self, term: str) -> Optional[Dict[str, Any]]:
        """解析一个缩写，返回其完整信息

        Args:
            term: 输入的缩写（不区分大小写）

        Returns:
            缩写信息字典，包含 full_name, category, related_* 等字段
        """
        self._load()
        key = term.strip().upper()
        return self._data.get(key)

    def is_abbreviation(self, term: str) -> bool:
        """检查输入是否是已知缩写"""
        self._load()
        key = term.strip().upper()
        return key in self._data

    def expand_search_terms(self, term: str) -> List[str]:
        """将缩写或关键词扩展为搜索词列表

        如果输入是已知缩写，返回其搜索词 + 全称 + 关联疾病/器官。
        如果不是已知缩写，原样返回。

        Args:
            term: 用户输入的关键词或缩写

        Returns:
            扩展后的搜索词列表
        """
        self._load()
        key = term.strip().upper()

        if key in self._data:
            entry = self._data[key]
            terms = list(entry.get("search_terms", []))
            # 添加关联疾病和器官
            for d in entry.get("related_diseases", []):
                if d.lower() not in [t.lower() for t in terms]:
                    terms.append(d)
            for o in entry.get("related_organs", []):
                if o.lower() not in [t.lower() for t in terms]:
                    terms.append(o)
            return terms

        # 不是缩写，原样返回
        return [term]

    def extract_abbreviations(self, text: str) -> List[str]:
        """从文本中提取已知缩写

        Args:
            text: 输入文本

        Returns:
            文本中出现的已知缩写列表
        """
        self._load()
        found = []
        for abbr in self._data:
            # 使用词边界匹配
            pattern = r"\b" + re.escape(abbr) + r"\b"
            if re.search(pattern, text, re.IGNORECASE):
                found.append(abbr)
        return found

    def get_related_organs(self, term: str) -> List[str]:
        """获取与术语关联的器官列表"""
        self._load()
        entry = self.resolve(term)
        if entry:
            return entry.get("related_organs", [])
        return []

    def get_related_diseases(self, term: str) -> List[str]:
        """获取与术语关联的疾病列表"""
        self._load()
        entry = self.resolve(term)
        if entry:
            return entry.get("related_diseases", [])
        return []

    def get_all_abbreviations(self) -> Dict[str, str]:
        """获取所有缩写及其全称的映射"""
        self._load()
        return {abbr: info.get("full_name", "") for abbr, info in self._data.items()}
