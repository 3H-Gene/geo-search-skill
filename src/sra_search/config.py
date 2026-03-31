"""配置管理模块

SRA_search 全局配置管理。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """全局设置"""

    # NCBI 配置
    ncbi_email: str = ""
    ncbi_api_key: str = ""

    # 速率限制
    rate_limit_requests_per_second: float = 3.0
    effective_rate_limit: float = 3.0

    # 检索配置
    search_retmax: int = 100
    search_epost_threshold: int = 100
    efetch_batch_size: int = 100

    # 重试配置
    retry_max_attempts: int = 5
    retry_base_delay: float = 2.0
    retry_max_delay: float = 60.0
    retry_jitter: float = 0.3

    # 数据库配置
    db_path: str = "data/sra_search.db"
    db_wal_enabled: bool = True
    db_path_resolved: str = ""

    # 导出配置
    export_default_format: str = "tsv"

    # 可用性检查
    availability_min_samples: int = 10

    # 日志
    log_level: str = "INFO"

    class Config:
        env_prefix = "SRA_SEARCH_"
        extra = "ignore"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 解析数据库路径
        if self.db_path:
            db_abs = Path(self.db_path)
            if not db_abs.is_absolute():
                base = Path(__file__).resolve().parent.parent.parent
                self.db_path_resolved = str(base / db_abs)
            else:
                self.db_path_resolved = str(db_abs)


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取全局设置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """重置设置（测试用）"""
    global _settings
    _settings = None


def load_settings_from_file(config_path: str) -> None:
    """从配置文件加载设置"""
    global _settings
    # 简单实现：读取环境变量
    # TODO: 支持 YAML/JSON 配置文件
    _settings = Settings()
