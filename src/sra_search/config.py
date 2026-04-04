"""配置管理模块

SRA_search 全局配置管理。
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局设置"""

    model_config = SettingsConfigDict(
        env_prefix="SRA_SEARCH_",
        extra="ignore",
    )

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
    db_write_batch_size: int = 100
    db_write_flush_interval: float = 2.0
    db_busy_timeout: int = 30000

    # 导出配置
    export_default_format: str = "tsv"

    # 可用性检查
    availability_min_samples: int = 10

    # 日志
    log_level: str = "INFO"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 解析数据库路径
        # 优先使用环境变量 SRA_SEARCH_DB_PATH
        # 如果是相对路径，基于当前工作目录（cwd）而非 __file__，确保
        # search / list / show 命令始终使用同一路径，不受包安装位置影响
        env_db = os.environ.get("SRA_SEARCH_DB_PATH", "")
        if env_db:
            db_abs = Path(env_db)
            if not db_abs.is_absolute():
                self.db_path_resolved = str(Path.cwd() / db_abs)
            else:
                self.db_path_resolved = str(db_abs)
        elif self.db_path:
            db_abs = Path(self.db_path)
            if not db_abs.is_absolute():
                self.db_path_resolved = str(Path.cwd() / db_abs)
            else:
                self.db_path_resolved = str(db_abs)


_settings: Settings | None = None


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
