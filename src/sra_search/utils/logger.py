"""日志配置模块"""
import sys

from loguru import logger


def setup_logger(level: str = "INFO"):
    """配置日志

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR），默认 INFO。
               None 或空字符串时回退到 INFO。
    """
    # 防御：None 或空值时回退到 INFO
    if not level:
        level = "INFO"
    level = level.upper()

    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.add(
        "logs/sra_search.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )

