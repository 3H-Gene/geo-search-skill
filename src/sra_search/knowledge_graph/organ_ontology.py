"""器官本体映射器

基于 Uberon 的器官同义词映射与层次关系查询。
支持器官名标准化、同义词扩展、层次遍历和搜索词生成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from sra_search.knowledge_graph._paths import ONTOLOGY_DIR as _ONTOLOGY_DIR


class OrganOntology:
    """器官本体映射器"""

    def __init__(self, data_path: Optional[Path] = None):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._data_path = data_path or _ONTOLOGY_DIR / "uberon_organs.json"
        self._name_to_canonical: Dict[str, str] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._data = raw.get("organs", {})
            for canonical, entry in self._data.items():
                self._name_to_canonical[canonical.lower()] = canonical
                for syn in entry.get("synonyms", []):
                    self._name_to_canonical[syn.lower()] = canonical
                # adjective form 也索引
                adj = entry.get("adjective", "")
                if adj:
                    self._name_to_canonical[adj.lower()] = canonical
            self._loaded = True
            logger.debug(f"Loaded {len(self._data)} organ entries")
        except FileNotFoundError:
            logger.warning(f"Organ ontology not found: {self._data_path}")
            self._loaded = True

    def resolve(self, name: str) -> Optional[Dict[str, Any]]:
        """解析器官名到完整条目"""
        self._load()
        canonical = self._name_to_canonical.get(name.strip().lower())
        if canonical:
            return self._data.get(canonical)
        return None

    def get_canonical(self, name: str) -> str:
        """获取规范名"""
        self._load()
        canonical = self._name_to_canonical.get(name.strip().lower())
        return canonical if canonical else name.strip()

    def get_synonyms(self, name: str) -> List[str]:
        """获取同义词列表"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("synonyms", []))
        return [name]

    def get_adjective(self, name: str) -> str:
        """获取形容词形式 (lung -> pulmonary)"""
        entry = self.resolve(name)
        if entry:
            return entry.get("adjective", "")
        return ""

    def get_uberon_id(self, name: str) -> Optional[str]:
        """获取 Uberon ID"""
        entry = self.resolve(name)
        if entry:
            return entry.get("uberon_id")
        return None

    def get_children(self, name: str) -> List[str]:
        """获取子器官"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("children", []))
        return []

    def get_parent(self, name: str) -> Optional[str]:
        """获取父器官"""
        entry = self.resolve(name)
        if entry:
            return entry.get("parent")
        return None

    def get_ancestor_chain(self, name: str) -> List[str]:
        """获取从器官到根的祖先链"""
        self._load()
        chain = []
        current = name.strip()
        visited: Set[str] = set()
        while current and current not in visited:
            visited.add(current)
            canonical = self._name_to_canonical.get(current.lower(), current)
            chain.append(canonical)
            entry = self._data.get(canonical)
            if not entry:
                break
            current = entry.get("parent", "")
        return chain

    def get_descendants(self, name: str) -> List[str]:
        """获取所有后代器官 (递归)"""
        self._load()
        canonical = self._name_to_canonical.get(name.strip().lower(), name.strip())
        descendants: List[str] = []

        def _walk(node: str):
            entry = self._data.get(node)
            if entry:
                for child in entry.get("children", []):
                    child_canonical = self._name_to_canonical.get(child.lower(), child)
                    descendants.append(child_canonical)
                    _walk(child_canonical)

        _walk(canonical)
        return descendants

    def get_search_terms(self, name: str) -> List[str]:
        """获取扩展搜索词"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("search_terms", []))
        return [name]

    def is_known_organ(self, name: str) -> bool:
        """检查是否是已知器官"""
        self._load()
        return name.strip().lower() in self._name_to_canonical

    def get_all_organs(self) -> Dict[str, Dict[str, Any]]:
        """获取所有器官条目"""
        self._load()
        return dict(self._data)
