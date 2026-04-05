"""MeSH 同义词映射器

将用户输入的器官/疾病名称映射到标准 MeSH 术语和同义词。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from sra_search.knowledge_graph._paths import ONTOLOGY_DIR as _ONTOLOGY_DIR


class MeshMapper:
    """MeSH 同义词映射器"""

    def __init__(self, data_path: Path | None = None):
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._data_path = data_path or _ONTOLOGY_DIR / "mesh_synonyms.json"
        # 构建快速查找索引
        self._term_to_canonical: dict[str, str] = {}
        self._canonical_to_entry: dict[str, Any] = {}

    def _load(self) -> None:
        """懒加载 MeSH 数据并构建索引"""
        if self._loaded:
            return
        try:
            with open(self._data_path, encoding="utf-8") as f:
                raw = json.load(f)
            groups = raw.get("synonym_groups") or raw.get("mesh_synonyms", {})

            for canonical, entry in groups.items():
                self._canonical_to_entry[canonical] = entry
                # 索引规范名
                self._term_to_canonical[canonical.lower()] = canonical
                # 索引所有同义词
                for syn in entry.get("synonyms", []):
                    self._term_to_canonical[syn.lower()] = canonical

            self._loaded = True
            logger.debug(
                f"Loaded {len(self._canonical_to_entry)} MeSH entries, "
                f"{len(self._term_to_canonical)} synonym mappings"
            )
        except FileNotFoundError:
            logger.warning(f"MeSH synonyms not found: {self._data_path}")
            self._loaded = True

    def resolve(self, term: str) -> dict[str, Any] | None:
        """将输入术语映射到 MeSH 条目

        Args:
            term: 输入的器官/疾病名称

        Returns:
            MeSH 条目字典，包含 canonical, mesh_id, synonyms, related_uberon 等
        """
        self._load()
        canonical = self._term_to_canonical.get(term.strip().lower())
        if canonical:
            return self._canonical_to_entry[canonical]  # type: ignore[no-any-return]
        return None

    def get_synonyms(self, term: str) -> list[str]:
        """获取术语的所有同义词

        Args:
            term: 输入术语

        Returns:
            同义词列表（包含输入术语本身）
        """
        self._load()
        entry = self.resolve(term)
        if entry:
            return entry.get("synonyms", [])  # type: ignore[no-any-return]
        return [term]

    def get_canonical(self, term: str) -> str:
        """获取术语的规范名（canonical name）

        如果术语未在映射表中，原样返回。
        """
        self._load()
        canonical = self._term_to_canonical.get(term.strip().lower())
        return canonical if canonical else term.strip()

    def expand_organ_terms(self, term: str) -> list[str]:
        """扩展器官相关搜索词

        将器官名称扩展为所有同义词 + adjective form。
        例如 "lung" -> ["lung", "pulmonary", "pulmo", "lung organ"]
        """
        self._load()
        entry = self.resolve(term)
        if entry:
            return list(entry.get("synonyms", []))
        return [term]

    def get_uberon_id(self, term: str) -> str | None:
        """获取器官对应的 Uberon 本体 ID"""
        self._load()
        entry = self.resolve(term)
        if entry:
            return entry.get("related_uberon")
        return None
