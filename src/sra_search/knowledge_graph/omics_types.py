"""组学类型标准词表映射器

基于组学类型标准词表的映射与搜索词生成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from sra_search.knowledge_graph._paths import ONTOLOGY_DIR as _ONTOLOGY_DIR


class OmicsTypeMapper:
    """组学类型标准词表映射器"""

    def __init__(self, data_path: Path | None = None):
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._data_path = data_path or _ONTOLOGY_DIR / "omics_types.json"
        self._name_to_canonical: dict[str, str] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            with open(self._data_path, encoding="utf-8") as f:
                raw = json.load(f)
            self._data = raw.get("omics_types", {})
            for canonical, entry in self._data.items():
                self._name_to_canonical[canonical.lower()] = canonical
                # 索引别名
                for alias in entry.get("aliases", []):
                    self._name_to_canonical[alias.lower()] = canonical
            self._loaded = True
            logger.debug(f"Loaded {len(self._data)} omics type entries")
        except FileNotFoundError:
            logger.warning(f"Omics types not found: {self._data_path}")
            self._loaded = True

    def resolve(self, name: str) -> dict[str, Any] | None:
        """解析组学类型名称到完整条目"""
        self._load()
        canonical = self._name_to_canonical.get(name.strip().lower())
        if canonical:
            return self._data.get(canonical)
        return None

    def standardize(self, name: str) -> str:
        """标准化组学类型名称"""
        self._load()
        canonical = self._name_to_canonical.get(name.strip().lower())
        return canonical if canonical else name.strip()

    def get_search_terms(self, name: str) -> list[str]:
        """获取扩展搜索词"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("search_terms", []))
        return [name]

    def get_aliases(self, name: str) -> list[str]:
        """获取别名列表"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("aliases", []))
        return []

    def detect_from_text(self, text: str) -> list[tuple[str, float]]:
        """从文本中检测组学类型

        Returns:
            [(组学类型, 置信度)] 列表
        """
        self._load()
        text_lower = text.lower()
        matches = []
        for canonical, entry in self._data.items():
            # 检查关键词
            keywords = entry.get("keywords", [])
            for kw in keywords:
                if kw.lower() in text_lower:
                    confidence = entry.get("confidence", 0.5)
                    matches.append((canonical, confidence))
                    break
        return matches
