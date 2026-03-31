"""NCBI E-utilities 通用检索器基类

封装 ESearch / EFetch / ESummary / ELink / EPost，
集成全局令牌桶限速和自动重试。
"""
from __future__ import annotations

import asyncio
import ssl
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
from Bio import Entrez
from loguru import logger

from sra_search.config import get_settings
from sra_search.utils.rate_limiter import get_global_limiter


# NCBI E-utilities 基础 URL
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class NCBIError(Exception):
    """NCBI API 错误"""

    def __init__(self, tool: str, message: str, http_code: Optional[int] = None):
        self.tool = tool
        self.http_code = http_code
        super().__init__(f"NCBI {tool} error: {message}")


class EntrezClient:
    """NCBI Entrez 客户端（基于 aiohttp 的异步封装）

    特性：
    - 全局限速：共享 Token Bucket，确保不超频
    - 自动重试：指数退避 + 429 自动处理
    - WebEnv 支持：EPost + EFetch 批量请求
    """

    def __init__(self, email: Optional[str] = None, api_key: Optional[str] = None):
        settings = get_settings()
        self.email = email or settings.ncbi_email
        self.api_key = api_key or settings.ncbi_api_key
        self.limiter = get_global_limiter()
        self._session: Optional[aiohttp.ClientSession] = None

        # 同步 Entrez（Biopython）配置
        if self.email:
            Entrez.email = self.email
        if self.api_key:
            Entrez.api_key = self.api_key

    def _params(self, **extra: Any) -> Dict[str, str]:
        """构建通用请求参数"""
        params: Dict[str, str] = {}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        for k, v in extra.items():
            if v is not None:
                if isinstance(v, bool):
                    params[k] = "y" if v else "n"
                elif isinstance(v, list):
                    params[k] = ",".join(str(x) for x in v)
                else:
                    params[k] = str(v)
        return params

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            # 禁用 SSL 验证（仅用于开发/测试环境）
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def _request(
        self,
        tool: str,
        params: Dict[str, str],
        retmode: str = "json",
    ) -> Dict[str, Any]:
        """发送异步请求到 NCBI E-utilities

        Args:
            tool: 工具名称 (esearch/efetch/esummary/elink/epost)
            params: 请求参数
            retmode: 返回模式 (json/xml)

        Returns:
            解析后的 JSON 字典

        Raises:
            NCBIError: API 返回错误
        """
        await self.limiter.acquire_async()

        url = f"{EUTILS_BASE}/{tool}.fcgi"
        params["retmode"] = retmode

        session = await self._get_session()

        settings = get_settings()
        max_retries = settings.retry_max_attempts
        base_delay = settings.retry_base_delay

        for attempt in range(max_retries):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        if retmode == "json":
                            # NCBI 有时返回的不是纯 JSON，先获取文本再解析
                            text = await resp.text()
                            # 尝试找到 JSON 开始的位置
                            import json
                            try:
                                # 尝试直接解析
                                data = json.loads(text)
                            except json.JSONDecodeError:
                                # 尝试找到 JSON 对象的边界
                                import re
                                # 查找第一个 { 到最后一个 } 的范围
                                match = re.search(r'\{.+\}', text, re.DOTALL)
                                if match:
                                    try:
                                        data = json.loads(match.group())
                                    except json.JSONDecodeError:
                                        # 如果还是失败，返回原始文本
                                        data = text
                                else:
                                    data = text
                        else:
                            data = await resp.text()
                        self.limiter.report_success()
                        return data  # type: ignore

                    elif resp.status == 429:
                        should_pause = self.limiter.report_429()
                        if should_pause:
                            raise NCBIError(
                                tool, "Rate limit paused due to repeated 429 errors", 429
                            )
                        continue  # 重试

                    elif resp.status == 500:
                        logger.warning(
                            f"NCBI server error (500) for {tool}, attempt {attempt + 1}"
                        )
                        if attempt < max_retries - 1:
                            import random
                            delay = base_delay * (2 ** attempt) * (1 + random.uniform(-0.3, 0.3))
                            delay = min(delay, 60)
                            await asyncio.sleep(delay)
                            continue
                        raise NCBIError(tool, "Server error (500)", 500)

                    else:
                        text = await resp.text()
                        raise NCBIError(
                            tool,
                            f"HTTP {resp.status}: {text[:200]}",
                            resp.status,
                        )

            except NCBIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.debug(f"Request failed ({e}), retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                else:
                    raise NCBIError(tool, str(e))

        raise NCBIError(tool, f"Max retries ({max_retries}) exceeded")

    # ---- ESearch ----

    async def esearch(
        self,
        db: str,
        term: str,
        retmax: Optional[int] = None,
        retstart: int = 0,
        sort: Optional[str] = None,
        mindate: Optional[str] = None,
        maxdate: Optional[str] = None,
        datetype: str = "pubmed",
        use_history: bool = False,
    ) -> Dict[str, Any]:
        """ESearch: 文本搜索，返回 UID 列表"""
        settings = get_settings()
        params = self._params(
            db=db,
            term=term,
            retmax=retmax or settings.search_retmax,
            retstart=retstart,
            sort=sort,
            mindate=mindate,
            maxdate=maxdate,
            datetype=datetype,
            usehistory="y" if use_history else "n",
        )
        return await self._request("esearch", params)

    # ---- EPost ----

    async def epost(
        self,
        db: str,
        ids: List[str],
    ) -> Dict[str, Any]:
        """EPost: 将 ID 列表上传到 NCBI 服务器，获取 WebEnv 和 query_key

        用于批量 EFetch，避免对大量 ID 逐个请求。
        """
        params = self._params(db=db, id=",".join(ids))
        return await self._request("epost", params)

    # ---- EFetch ----

    async def efetch(
        self,
        db: str,
        ids: Optional[List[str]] = None,
        webenv: Optional[str] = None,
        query_key: Optional[str] = None,
        retstart: int = 0,
        retmax: Optional[int] = None,
        rettype: Optional[str] = None,
        retmode: str = "json",
    ) -> Any:
        """EFetch: 根据 UID 或 WebEnv 获取完整记录"""
        params = self._params(
            db=db,
            retstart=retstart,
            retmax=retmax or get_settings().efetch_batch_size,
            rettype=rettype,
        )
        if ids:
            params["id"] = ",".join(ids)
        if webenv:
            params["WebEnv"] = webenv
        if query_key:
            params["query_key"] = str(query_key)
        return await self._request("efetch", params, retmode=retmode)

    # ---- ESummary ----

    async def esummary(
        self,
        db: str,
        ids: List[str],
    ) -> Dict[str, Any]:
        """ESummary: 根据 UID 获取文档摘要"""
        params = self._params(db=db, id=",".join(ids))
        return await self._request("esummary", params)

    # ---- ELink ----

    async def elink(
        self,
        dbfrom: str,
        db: str,
        ids: List[str],
        cmd: str = "neighbor",
    ) -> Dict[str, Any]:
        """ELink: 跨数据库关联查询"""
        params = self._params(
            dbfrom=dbfrom,
            db=db,
            id=",".join(ids),
            cmd=cmd,
        )
        return await self._request("elink", params)

    # ---- Batch EFetch (EPost + EFetch) ----

    async def batch_efetch(
        self,
        db: str,
        ids: List[str],
        rettype: Optional[str] = None,
        retmode: str = "json",
    ) -> List[Any]:
        """批量 EFetch: 当 ID 数量超过阈值时自动使用 EPost + WebEnv

        Args:
            db: 数据库名
            ids: UID 列表
            rettype: 返回类型
            retmode: 返回模式

        Returns:
            所有记录的列表
        """
        settings = get_settings()
        threshold = settings.search_epost_threshold
        batch_size = settings.efetch_batch_size

        if len(ids) <= threshold:
            # 少量 ID，直接请求
            result = await self.efetch(db=db, ids=ids, rettype=rettype, retmode=retmode)
            return [result]

        # 大量 ID，使用 EPost + WebEnv
        logger.info(f"Using EPost for {len(ids)} IDs (threshold={threshold})")
        post_result = await self.epost(db, ids)
        webenv = post_result.get("WebEnv", "")
        query_key = post_result.get("QueryKey", "")

        if not webenv or not query_key:
            logger.warning("EPost failed to return WebEnv/query_key, falling back to direct request")
            result = await self.efetch(db=db, ids=ids, rettype=rettype, retmode=retmode)
            return [result]

        # 分批 EFetch
        all_results = []
        retstart = 0
        while True:
            result = await self.efetch(
                db=db,
                webenv=webenv,
                query_key=query_key,
                retstart=retstart,
                retmax=batch_size,
                rettype=rettype,
                retmode=retmode,
            )
            if not result:
                break
            all_results.append(result)
            retstart += batch_size
            # 如果返回结果数少于 batch_size，说明已到底
            if isinstance(result, list):
                if len(result) < batch_size:
                    break
            elif isinstance(result, dict):
                # JSON 模式
                result_list = result.get("result", {})
                uids = [k for k in result_list if k != "uids"]
                if len(uids) < batch_size:
                    break

        return all_results

    # ---- Cleanup ----

    async def close(self) -> None:
        """关闭 aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


# 全局客户端单例
_client: Optional[EntrezClient] = None


def get_entrez_client() -> EntrezClient:
    """获取全局 Entrez 客户端单例"""
    global _client
    if _client is None:
        _client = EntrezClient()
    return _client


def reset_entrez_client() -> None:
    """重置全局客户端（测试用）"""
    global _client
    if _client is not None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_client.close())
            else:
                loop.run_until_complete(_client.close())
        except RuntimeError:
            pass
    _client = None
