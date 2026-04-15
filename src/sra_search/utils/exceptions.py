"""SRA_search 统一异常类定义

设计原则：
- 所有业务异常继承自 `SRAError` 基类
- 按错误类型分层：网络错误、API 错误、配置错误、数据错误
- 每个异常类包含错误码，便于程序化处理
- 异常消息应提供清晰的解决方案指引

错误码规范：
- 1xxx: 配置相关
- 2xxx: 网络/连接相关
- 3xxx: API 响应相关
- 4xxx: 数据处理相关
- 5xxx: LLM 相关
"""


class SRAError(Exception):
    """SRA_search 异常基类

    所有业务异常应继承此类。
    """
    error_code: int = 0
    error_type: str = "SRAError"

    def __init__(self, message: str, details: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        if self.details:
            return f"[{self.error_code}] {self.message}\n详情: {self.details}"
        return f"[{self.error_code}] {self.message}"

    def to_dict(self) -> dict:
        """转换为可序列化的字典（用于 JSON 输出）"""
        return {
            "error_code": self.error_code,
            "error_type": self.error_type,
            "message": self.message,
            "details": self.details,
        }


# ── 配置相关异常 (1xxx) ────────────────────────────────────────────────────────


class ConfigurationError(SRAError):
    """配置错误基类"""
    error_code = 1000
    error_type = "ConfigurationError"


class NCBIConfigError(ConfigurationError):
    """NCBI 配置错误"""
    error_code = 1001
    error_type = "NCBIConfigError"


class LLMConfigError(ConfigurationError):
    """LLM 配置错误"""
    error_code = 1002
    error_type = "LLMConfigError"


class DatabaseConfigError(ConfigurationError):
    """数据库配置错误"""
    error_code = 1003
    error_type = "DatabaseConfigError"


# ── 网络/连接相关异常 (2xxx) ──────────────────────────────────────────────────


class NetworkError(SRAError):
    """网络错误基类"""
    error_code = 2000
    error_type = "NetworkError"


class ConnectionTimeoutError(NetworkError):
    """连接超时"""
    error_code = 2001
    error_type = "ConnectionTimeoutError"


class ConnectionRefusedError(NetworkError):
    """连接被拒绝"""
    error_code = 2002
    error_type = "ConnectionRefusedError"


class RateLimitError(NetworkError):
    """速率限制（429 错误）"""
    error_code = 2003
    error_type = "RateLimitError"

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class IPBannedError(NetworkError):
    """IP 被封禁"""
    error_code = 2004
    error_type = "IPBannedError"


# ── API 响应相关异常 (3xxx) ──────────────────────────────────────────────────


class APIError(SRAError):
    """API 错误基类"""
    error_code = 3000
    error_type = "APIError"


class NCBIPythonError(APIError):
    """NCBI API 错误（如 400 Bad Request）"""
    error_code = 3001
    error_type = "NCBIAPIError"


class APITimeoutError(APIError):
    """API 超时"""
    error_code = 3002
    error_type = "APITimeoutError"


class APIRateLimitError(APIError):
    """API 速率限制"""
    error_code = 3003
    error_type = "APIRateLimitError"


class APIResponseParseError(APIError):
    """API 响应解析错误"""
    error_code = 3004
    error_type = "APIResponseParseError"


# ── 数据处理相关异常 (4xxx) ──────────────────────────────────────────────────


class DataError(SRAError):
    """数据错误基类"""
    error_code = 4000
    error_type = "DataError"


class ValidationError(DataError):
    """数据校验错误"""
    error_code = 4001
    error_type = "ValidationError"


class ParseError(DataError):
    """数据解析错误"""
    error_code = 4002
    error_type = "ParseError"


class NotFoundError(DataError):
    """资源未找到"""
    error_code = 4003
    error_type = "NotFoundError"


class DuplicateError(DataError):
    """数据重复"""
    error_code = 4004
    error_type = "DuplicateError"


# ── LLM 相关异常 (5xxx) ──────────────────────────────────────────────────────


class LLMError(SRAError):
    """LLM 错误基类"""
    error_code = 5000
    error_type = "LLMError"


class LLMConnectionError(LLMError):
    """LLM 连接错误"""
    error_code = 5001
    error_type = "LLMConnectionError"


class LLMTimeoutError(LLMError):
    """LLM 请求超时"""
    error_code = 5002
    error_type = "LLMTimeoutError"


class LLMResponseParseError(LLMError):
    """LLM 响应解析错误"""
    error_code = 5003
    error_type = "LLMResponseParseError"


class LLMModelNotFoundError(LLMError):
    """LLM 模型不存在"""
    error_code = 5004
    error_type = "LLMModelNotFoundError"


class LLMRateLimitError(LLMError):
    """LLM 速率限制"""
    error_code = 5005
    error_type = "LLMRateLimitError"


# ── 工具函数 ─────────────────────────────────────────────────────────────────


def is_retryable(error: Exception) -> bool:
    """判断异常是否可重试

    Args:
        error: 任意异常对象

    Returns:
        True if the error is retryable
    """
    if isinstance(error, SRAError):
        retryable_codes = {
            2001,  # ConnectionTimeoutError
            2003,  # RateLimitError
            3001,  # NCBIAPIError (部分情况)
            3002,  # APITimeoutError
            3003,  # APIRateLimitError
            5001,  # LLMConnectionError
            5002,  # LLMTimeoutError
            5005,  # LLMRateLimitError
        }
        return error.error_code in retryable_codes
    return False


def format_error_for_user(error: Exception) -> str:
    """将异常格式化为用户友好的错误消息

    Args:
        error: 任意异常对象

    Returns:
        用户友好的错误消息
    """
    if isinstance(error, SRAError):
        return f"{error.message}\n\n解决方案: {_get_solution_hint(error)}"
    return str(error)


def _get_solution_hint(error: SRAError) -> str:
    """根据错误类型返回解决方案提示"""
    hints = {
        1001: "请设置 SRA_SEARCH_NCBI_EMAIL 环境变量: export SRA_SEARCH_NCBI_EMAIL='your@email.com'",
        1002: "请设置 LLM API Key 或选择其他 Provider。运行 'sra-search config' 查看配置选项。",
        2001: "网络连接超时，请检查网络后重试。如持续出现，请设置代理: export HTTPS_PROXY='http://proxy:port'",
        2003: "请求过于频繁，请等待后重试。",
        2004: "您的 IP 可能被 NCBI 临时封禁，请等待 24 小时后重试，或使用代理。",
        3001: "查询语法可能有问题，请检查搜索词是否包含非法字符。",
        3002: "NCBI API 响应超时，请稍后重试。",
        5001: "无法连接到 LLM 服务，请检查网络和 API 地址配置。",
        5004: "指定的模型不存在，请检查模型名称是否正确。",
    }
    hint = hints.get(error.error_code)
    return hint or "请查阅文档或联系开发者。"
