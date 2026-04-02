"""知识图谱数据文件路径解析

兼容两种安装模式：
1. pip install（包内 data/）：src/sra_search/data/ontologies/
2. 开发模式（项目根 data/）：  data/ontologies/
"""
from __future__ import annotations

from pathlib import Path


def _find_ontology_dir() -> Path:
    """查找本体数据目录，按优先级依次尝试多个位置"""
    # 候选路径列表（按优先级排序）
    candidates = [
        # 1. pip install 后：包内 data/ontologies/
        Path(__file__).resolve().parent.parent / "data" / "ontologies",
        # 2. 开发模式（editable install / python -m）：项目根 data/ontologies/
        Path(__file__).resolve().parent.parent.parent.parent / "data" / "ontologies",
        # 3. 工作目录 data/ontologies/
        Path.cwd() / "data" / "ontologies",
    ]

    for p in candidates:
        if p.is_dir():
            return p

    # 找不到时返回第一个候选（会在加载时产生 WARNING）
    return candidates[0]


ONTOLOGY_DIR: Path = _find_ontology_dir()
"""本体数据目录路径（自动解析）"""


def _find_data_dir() -> Path:
    """查找 data 根目录（包含 abbreviation_map.json 等）"""
    candidates = [
        Path(__file__).resolve().parent.parent / "data",
        Path(__file__).resolve().parent.parent.parent.parent / "data",
        Path.cwd() / "data",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return candidates[0]


DATA_DIR: Path = _find_data_dir()
"""数据根目录路径（自动解析）"""
