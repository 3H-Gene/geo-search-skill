"""疾病本体映射器

基于 DOID (Disease Ontology) 的疾病-器官-物种关联推理。
支持疾病名标准化、同义词扩展、关联器官查询和搜索词生成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from sra_search.knowledge_graph._paths import ONTOLOGY_DIR as _ONTOLOGY_DIR


class DiseaseOntology:
    """疾病本体映射器"""

    def __init__(self, data_path: Path | None = None):
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._data_path = data_path or _ONTOLOGY_DIR / "doid_hierarchy.json"
        self._name_to_canonical: dict[str, str] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            with open(self._data_path, encoding="utf-8") as f:
                raw = json.load(f)
            self._data = raw.get("diseases", {})
            for canonical, entry in self._data.items():
                self._name_to_canonical[canonical.lower()] = canonical
                self._name_to_canonical[entry["canonical"].lower()] = canonical
                for syn in entry.get("synonyms", []):
                    self._name_to_canonical[syn.lower()] = canonical
            self._loaded = True
            logger.debug(f"Loaded {len(self._data)} disease entries")
        except FileNotFoundError:
            logger.warning(f"Disease ontology not found: {self._data_path}")
            self._loaded = True

    def resolve(self, name: str) -> dict[str, Any] | None:
        """解析疾病名称到完整条目"""
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

    def get_synonyms(self, name: str) -> list[str]:
        """获取同义词列表"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("synonyms", []))
        return [name]

    def get_related_organs(self, name: str) -> list[str]:
        """获取疾病关联的器官"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("related_organs", []))
        return []

    def get_related_species(self, name: str) -> list[str]:
        """获取疾病常见物种"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("related_species", []))
        return []

    def get_search_terms(self, name: str) -> list[str]:
        """获取扩展搜索词"""
        entry = self.resolve(name)
        if entry:
            return list(entry.get("search_terms", []))
        return [name]

    def get_mesh_id(self, name: str) -> str | None:
        """获取 MeSH ID"""
        entry = self.resolve(name)
        if entry:
            return entry.get("mesh_id")
        return None

    def get_doid_id(self, name: str) -> str | None:
        """获取 DOID"""
        entry = self.resolve(name)
        if entry:
            return entry.get("doid_id")
        return None

    def get_subtypes(self, name: str) -> dict[str, str]:
        """获取疾病亚型映射 (缩写 -> 全称)"""
        entry = self.resolve(name)
        if entry:
            return entry.get("subtypes", {})
        return {}

    def find_diseases_by_organ(self, organ: str) -> list[str]:
        """查找与给定器官相关的所有疾病"""
        self._load()
        organ_lower = organ.strip().lower()
        results = []
        for canonical, entry in self._data.items():
            if organ_lower in [o.lower() for o in entry.get("related_organs", [])]:
                results.append(canonical)
        return results

    def is_known_disease(self, name: str) -> bool:
        """检查是否是已知疾病"""
        self._load()
        return name.strip().lower() in self._name_to_canonical

    def get_all_diseases(self) -> dict[str, dict[str, Any]]:
        """获取所有疾病条目"""
        self._load()
        return dict(self._data)
