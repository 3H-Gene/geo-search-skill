# SRA_search V2 开发计划：LLM 辅助检索与总结

> 版本：0.4.0（计划）  
> 日期：2026-04-12  
> 目标：引入 LLM 能力提升语义相关性评分和结果总结，在不提供 API Key 时完全回退到现有关键词模式

---

## 1. 背景与目标

### 现状（V1）
- **相关性评分**：`converter.py` 中 `compute_relevance_score()` 基于硬编码关键词库 + 子串匹配
- **查询扩展**：`SmartQueryBuilder` 基于本体知识图谱的规则扩展
- **结果总结**：仅输出表格/JSON，无 LLM 生成的摘要

### V2 新增能力
1. **LLM 语义相关性评分**：替代/增强关键词匹配，更准确判断数据集是否与用户意图相关
2. **查询意图理解**：LLM 解析用户查询中的生物学意图，自动推断疾病/组织/技术/扰动类型
3. **结果摘要生成**：对 top-N 结果生成自然语言摘要，帮助用户快速理解
4. **渐进式增强**：API Key 可选，不提供时完全回退到 V1 模式

### 设计原则
- **优雅降级**：无 API Key 时行为与 V1 完全一致
- **API 无关**：通过统一抽象层支持 OpenAI / Anthropic / 本地模型
- **最小侵入**：不修改核心检索流程，仅在 `converter.py` 和 `cli.py` 增加 LLM 分支
- **成本控制**：批量评分（batch scoring）、缓存、限流

---

## 2. 系统架构

### 2.1 新增模块结构

```
src/sra_search/
├── llm/                          # [NEW] LLM 模块
│   ├── __init__.py
│   ├── client.py                  # LLM 客户端抽象层
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── openai_provider.py    # OpenAI GPT-4o / GPT-4o-mini
│   │   ├── anthropic_provider.py # Anthropic Claude
│   │   └── local_provider.py     # Ollama / LM Studio 本地模型
│   ├── ranker.py                 # LLM 语义排序/评分
│   ├── summarizer.py             # 结果摘要生成
│   └── query_analyzer.py         # 查询意图分析
├── converter.py                   # [MOD] 增加 LLM 评分路径
├── config.py                     # [MOD] 增加 LLM 配置项
├── schema.py                     # [MOD] SearchResultSchema 增加 summary 字段
└── cli.py                        # [MOD] 增加 --llm-* 参数
```

### 2.2 数据流（V2）

```
CLI search "gout single cell"
    │
    ▼
SearchAggregator.search()         # 现有流程（GEO/SRA/PubMed 并发）
    │                             # 不变
    ▼
records_to_search_result()        # converter.py
    │
    ├──► [LLM 可用?] ── 否 ──► compute_relevance_score() [V1 关键词模式]
    │
    └──► [LLM 可用] ── 是 ──► LLMRanker.score_batch()    # 批量语义评分
                                 └──► LLMSummarizer.summarize()  # 生成摘要

    ▼
SearchResultSchema (含 llm_summary)
    │
    ▼
JSON / Table / ID-List 输出
```

---

## 3. 核心模块详细设计

### 3.1 LLM 客户端抽象（`llm/client.py`）

**目标**：统一接口，屏蔽不同 LLM API 的差异

```python
class LLMClient(ABC):
    """LLM 客户端抽象基类"""

    @abstractmethod
    async def achat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """发送对话请求，返回文本响应"""
        ...

    @abstractmethod
    async def abatch_chat(
        self,
        messages: list[dict],  # list of {"role": ..., "content": ...}
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> list[str]:
        """批量对话请求（同一系统提示，不同 user 消息）"""
        ...
```

**配置注入**（`config.py` 新增）：

```python
# LLM 配置（新增）
llm_provider: str = ""           # "openai" / "anthropic" / "local"，空=禁用
llm_model: str = "gpt-4o-mini" # 默认模型
llm_api_key: str = ""            # API Key（优先读取环境变量 SRA_SEARCH_LLM_API_KEY）
llm_base_url: str = ""           # 代理地址（如 OpenAI-compatible endpoint）
llm_temperature: float = 0.0     # 评分用低温度
llm_max_tokens: int = 2048       # 摘要最大 token 数
llm_timeout: float = 60.0        # 请求超时（秒）
llm_enabled: bool = False        # 全局开关（可被 CLI 参数覆盖）
llm_batch_size: int = 10         # 每批评分数（控制成本）
llm_cache_ttl_hours: int = 168  # 缓存有效期（7天）
```

**Provider 实现**：

| Provider | 模型推荐 | 特点 |
|----------|---------|------|
| OpenAI | `gpt-4o-mini`（默认）、`gpt-4o` | 通用、成本低 |
| Anthropic | `claude-3.5-haiku`（默认）、`claude-3.5-sonnet` | 长上下文、推理强 |
| Local | Ollama any model | 完全离线、无 API 费用 |

### 3.2 LLM 语义排序（`llm/ranker.py`）

**职责**：将每个 `DatasetSchema` 的标题/摘要与用户查询一起发给 LLM，输出相关性分数（0-1）。

**评分 Prompt 模板**：

```
你是一个生物信息学专家。请判断以下 GEO/NCBI 数据集与用户查询的相关性。

【查询】
{query}

【数据集标题】
{title}

【数据集摘要】（如有）
{summary}

请从以下维度评估：
1. 疾病相关性：该数据集是否研究用户查询中提到的疾病？
2. 技术方法：测序方法/平台是否匹配？（如查询 single-cell 则单细胞数据更相关）
3. 生物体/组织：是否匹配？
4. 干预类型：是否有 perturbation（CRISPR/KO/Drug等）？

请输出一个 0.0 到 1.0 的分数（保留3位小数），只输出数字，不要解释。
分数越高表示越相关。
```

**批量评分策略**：
- 每批最多 `llm_batch_size` 个数据集（避免单次请求过长）
- 异步并发请求（`asyncio.gather`），控制并发数避免 rate limit
- 结果缓存（以 `query + dataset_hash` 为 key，有效期 7 天）
- 超时/失败时回退到 V1 `compute_relevance_score()`

**API**：

```python
class LLMRanker:
    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient.from_config()
        self.cache: dict[str, float] = {}  # 内存缓存

    async def score_batch(
        self,
        datasets: list[DatasetSchema],
        query: str,
        top_k: int = 50,  # 只评 top_k，省成本
    ) -> list[tuple[DatasetSchema, float]]:
        """
        批量评分。返回 (dataset, score) 列表。
        失败时返回空列表，调用方回退到 V1 评分。
        """
        ...

    def is_available(self) -> bool:
        """检查 LLM 是否可用（已配置 API Key 且 Provider 有效）"""
        ...
```

### 3.3 结果摘要生成（`llm/summarizer.py`）

**职责**：对 top-N 结果生成自然语言摘要，便于用户快速了解结果概况。

**Prompt 模板**：

```
你是一个生物信息学专家。以下是针对 "{query}" 的搜索结果摘要。

【共找到 {total} 个数据集，以下是 Top {n} 的关键发现】

{for i, ds in enumerate(top_datasets)}:
{i+1}. {ds.gse_id} - {ds.title}
   - 技术: {ds.data_type} / {ds.granularity}
   - 样本数: {ds.sample_count}
   - 生物体: {ds.organism}
   - 疾病: {ds.disease or '未标注'}
   - 组织: {ds.tissue or ds.organ or '未标注'}
   - 干预类型: {', '.join(ds.perturbation_types) or '无'}
   - 发表: {ds.publication_date or '未知'}

请生成一个 3-5 段的自然语言总结，包含：
1. 整体结果概况（多少相关、哪些技术类型为主）
2. 最相关的 2-3 个数据集及其亮点
3. 研究趋势观察（如有）
4. 建议的后续步骤

输出格式：纯文本，不要 Markdown 格式。
```

**API**：

```python
class LLMSummarizer:
    def __init__(self, client: LLMClient | None = None):
        self.client = client

    async def summarize(
        self,
        query: str,
        datasets: list[DatasetSchema],
        total_found: int,
    ) -> str:
        """
        生成搜索结果摘要。
        返回自然语言文本，失败时返回空字符串。
        """
        ...
```

### 3.4 查询意图分析（`llm/query_analyzer.py`）

**职责**：LLM 解析用户查询，自动推断结构化的生物学意图。

**Prompt 模板**：

```
你是一个生物信息学专家。请解析以下用户查询，提取结构化的生物学意图。

【用户查询】
{query}

请输出 JSON 格式：
{
  "disease": ["痛风", "gout"],           // 疾病名称（英文+中文）
  "technology": ["scRNA-seq"],            // 测序技术
  "organism": ["Homo sapiens"],           // 生物体
  "tissue": ["外周血单核细胞"],           // 组织/细胞类型
  "perturbation": ["CRISPR"],              // 干预类型（可选）
  "keywords": ["monosodium urate"],        // 额外关键词
  "intent_summary": "用户想找..."         // 一句话意图总结
}

只输出 JSON，不要其他文字。如果某字段为空则输出空列表。
```

**用途**：
- 作为 `compute_relevance_score()` 的辅助输入（增强疾病关键词库）
- 扩展查询词（补充 LLM 推断的同义词）
- 改进 SRA 过滤条件

**API**：

```python
@dataclass
class QueryIntent:
    disease: list[str] = field(default_factory=list)
    technology: list[str] = field(default_factory=list)
    organism: list[str] = field(default_factory=list)
    tissue: list[str] = field(default_factory=list)
    perturbation: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    intent_summary: str = ""


class LLMQueryAnalyzer:
    async def analyze(self, query: str) -> QueryIntent | None:
        """解析查询意图，失败时返回 None"""
        ...
```

---

## 4. 配置变更（`config.py`）

```python
# === LLM 配置（新增）===
llm_provider: str = ""           # "openai" / "anthropic" / "local"，空=禁用
llm_model: str = "gpt-4o-mini"  # 默认模型
llm_api_key: str = ""           # 优先读取环境变量 SRA_SEARCH_LLM_API_KEY
llm_base_url: str = ""           # OpenAI-compatible 代理地址
llm_temperature: float = 0.0
llm_max_tokens: int = 2048
llm_timeout: float = 60.0
llm_enabled: bool = False        # 全局开关
llm_batch_size: int = 10
llm_cache_ttl_hours: int = 168
```

**环境变量**：
```bash
export SRA_SEARCH_LLM_API_KEY="sk-..."
export SRA_SEARCH_LLM_PROVIDER="openai"     # 可选
export SRA_SEARCH_LLM_MODEL="gpt-4o-mini"   # 可选
export SRA_SEARCH_LLM_BASE_URL=""           # 如需代理
```

---

## 5. CLI 变更（`cli.py`）

**新增参数**（`search` 命令）：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--llm/--no-llm` | flag | 继承 `llm_enabled` | 强制开启/关闭 LLM |
| `--llm-model` | str | `gpt-4o-mini` | 指定模型 |
| `--llm-provider` | str | `openai` | 提供商 |
| `--llm-top-k` | int | 20 | LLM 评分的 top_k |
| `--summarize` | flag | False | 是否生成 LLM 摘要 |

**示例**：

```bash
# 关键词模式（不启用 LLM）
sra-search search "gout single cell"

# 启用 LLM 评分 + 摘要
sra-search search "gout single cell" --llm --summarize

# 指定模型
sra-search search "liver fibrosis scRNA-seq" --llm --llm-model gpt-4o --llm-top-k 30

# 查看 LLM 配置
sra-search config
# LLM Provider: openai
# LLM Model: gpt-4o-mini
# LLM Enabled: False  <- 需要先设置 API Key
```

**`config` 命令输出更新**：

```
  LLM Provider:  [NOT SET]
  LLM Model:     gpt-4o-mini
  LLM Enabled:    False
  LLM API Key:   [NOT SET]  <- 需设置 SRA_SEARCH_LLM_API_KEY
```

---

## 6. Schema 变更（`schema.py`）

### `SearchResultSchema` 新增字段

```python
@dataclass
class SearchResultSchema:
    # ... 现有字段 ...
    llm_summary: str = ""         # [NEW] LLM 生成的摘要文本
    llm_model: str = ""           # [NEW] 使用的模型
    llm_scored_count: int = 0    # [NEW] 经 LLM 评分的数量
```

---

## 7. V1 回退策略

当 LLM 不可用时，整个系统回退到 V1 行为：

| 场景 | 行为 |
|------|------|
| `llm_api_key` 为空 | 完全禁用 LLM，无任何额外调用 |
| LLM 请求超时（>60s） | 回退到 V1 `compute_relevance_score()`，记录 WARNING |
| LLM 返回格式错误 | 回退到 V1 评分，记录 WARNING |
| API Key 无效（401） | 禁用 LLM，记录 ERROR，提示用户检查 Key |
| Rate limit（429） | 退避重试 1 次，仍失败则回退到 V1 |

---

## 8. 成本控制策略

| 策略 | 说明 |
|------|------|
| **Batch Scoring** | 每次请求最多 10 个数据集（gpt-4o-mini 支持 128k context） |
| **Top-K 优先** | 只对 top 20-50 结果做 LLM 评分，其他用 V1 |
| **结果缓存** | 7 天缓存（query + dataset_hash -> score），避免重复评分 |
| **模型选择** | 默认 `gpt-4o-mini`（便宜 40 倍于 gpt-4o） |
| **摘要仅一次** | 摘要只生成一次，不是每个数据集 |
| **温度=0** | 评分和摘要都用 temperature=0，确保确定性 |

**预估成本**（`gpt-4o-mini`）：
- 评分：20 个数据集 x ~500 tokens = ~10k tokens = **$0.0002/次**
- 摘要：1 个查询 x ~1500 tokens = **$0.00006/次**
- 总计：约 **$0.0003/查询**，几乎可以忽略

---

## 9. 测试策略

### 9.1 单元测试（`tests/test_llm_*.py`）

- `test_client.py`：测试各 Provider 的请求/响应解析
- `test_ranker.py`：测试批量评分逻辑、缓存、回退
- `test_summarizer.py`：测试摘要生成格式
- `test_query_analyzer.py`：测试 JSON 解析和字段提取

### 9.2 集成测试

- Mock LLM API 响应，验证完整流程
- 测试回退逻辑（API Key 为空、请求失败）

### 9.3 对比测试

- 相同查询，对比 V1 和 V2 的评分结果
- 人工评估 top-10 排序质量是否有提升

---

## 10. 实施计划

### Phase 1：基础设施（1 天）
1. 新增 `llm/` 目录结构和 `__init__.py`
2. 实现 `llm/client.py`（LLM 抽象 + OpenAI Provider）
3. 更新 `config.py`（LLM 配置项）
4. 编写基础测试

### Phase 2：语义排序（2 天）
1. 实现 `llm/providers/openai_provider.py`
2. 实现 `llm/ranker.py`（评分 + 缓存）
3. 修改 `converter.py`（集成 LLM 评分路径）
4. 测试批量评分 + V1 回退

### Phase 3：结果摘要（1 天）
1. 实现 `llm/summarizer.py`
2. 更新 `schema.py`（新增字段）
3. 修改 `cli.py`（`--summarize` 参数 + 输出摘要）
4. 测试摘要质量

### Phase 4：多 Provider 支持（1 天）
1. 实现 `llm/providers/anthropic_provider.py`
2. 实现 `llm/providers/local_provider.py`
3. 更新 Provider 选择逻辑
4. 测试各 Provider

### Phase 5：查询意图分析（1 天）
1. 实现 `llm/query_analyzer.py`
2. 集成到 `aggregator.py`（可选，作为辅助输入）
3. 测试意图解析准确性

### Phase 6：文档与发布（0.5 天）
1. 更新 README.md（LLM 使用说明）
2. 更新 pyproject.toml（新增依赖：`openai`、`anthropic`）
3. 生成 CHANGELOG
4. 打 tag v0.4.0，推送 GitHub

---

## 11. 依赖变更

**pyproject.toml 新增依赖**：

```toml
[project.optional-dependencies]
llm = [
    "openai>=1.0",
    "anthropic>=0.20",
]
all = [
    "sra-search[geo,sra,llm,dev]",
]
```

> 注：`openai` SDK 同时支持 OpenAI API 和 OpenAI-compatible API（如 vLLM、Ollama、TGI），
> `anthropic` SDK 用于 Claude。

---

## 12. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM API 不可用/超时 | 中 | 低 | V1 回退，用户无感知 |
| API Key 配置错误 | 高 | 中 | config 命令提示检查 Key |
| 评分质量不如 V1 | 低 | 高 | 提供 `--no-llm` 强制回退；对比测试 |
| Token 成本超预期 | 低 | 中 | top_k 限制；缓存；默认 mini 模型 |
| Provider API 变更 | 低 | 中 | 抽象层隔离 Provider，利于升级 |
| 网络不稳定 | 中 | 低 | 重试机制；超时控制 |

---

## 13. 附录

### A. 参考项目

- [SRAgent](https://github.com/ArcInstitute/SRAgent)：LangGraph + LLM 驱动的 SRA 检索（已参考，本项目不引入 LangGraph）
- [Bio-Search-Evaluation](https://github.com/Y礼仪-Ding-Lab/Bio-Search-Evaluation)：生物医学搜索评测基准
- [LangChain NCBI](https://github.com/langchain-ai/langchain-ncbi)：LangChain NCBI 工具集成

### B. 环境变量速查

```bash
# NCBI（已有）
SRA_SEARCH_NCBI_EMAIL=your@email.com
SRA_SEARCH_NCBI_API_KEY=your_ncbi_key

# LLM（新增）
SRA_SEARCH_LLM_API_KEY=sk-...
SRA_SEARCH_LLM_PROVIDER=openai          # openai / anthropic / local
SRA_SEARCH_LLM_MODEL=gpt-4o-mini
SRA_SEARCH_LLM_BASE_URL=https://api.openai.com/v1  # 如需代理
SRA_SEARCH_LLM_ENABLED=false             # 默认关闭
```

### C. 版本规划

| 版本 | 主要内容 | 目标日期 |
|------|---------|---------|
| v0.3.0 | 当前版本，关键词评分 | 已完成 |
| v0.4.0 | LLM 语义评分 + 摘要（OpenAI） | 待实施 |
| v0.5.0 | 多 Provider + 查询意图分析 | 后续 |
