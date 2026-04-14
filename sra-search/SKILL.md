---
name: sra-search
description: 多组学数据集智能检索工具。用于 GEO/SRA/PubMed/BioProject 联合搜索，支持疾病知识图谱扩展、单细胞识别、LLM 语义排序与摘要生成。与 gse-downloader 配合完成数据发现到下载的完整流程。
---

# SRA Search Skill

## 概述

SRA Search 是多组学数据集智能检索与管理工具，基于 NCBI E-utilities 实现 GEO/SRA/PubMed/BioProject 多源联合搜索。

### 核心能力

- **多源联合检索** — 同时检索 GEO、SRA、PubMed、BioProject，结果自动去重合并
- **知识图谱扩展** — 自动展开疾病同义词（gout → hyperuricemia, uric acid...）、器官、组学类型
- **单细胞识别** — 自动识别 scRNA-seq/snRNA-seq 数据集
- **LLM 语义排序**（可选）— 调用 OpenAI / Anthropic / Google Gemini / Ollama 对结果做二次排序
- **LLM 摘要生成**（可选）— 为数据集生成自然语言摘要
- **数据集审核** — 支持 pending/approved/irrelevant/deleted 状态管理

### 系统定位

```
sra-search（发现数据） → gse-downloader（下载数据） → downstream analysis（分析数据）
```

## 触发条件

**应触发此 skill 的情况：**

- 用户询问 GEO / GSE / SRA 数据集
- 用户需要 RNA-seq / scRNA-seq / ATAC-seq 数据集
- 用户提到疾病 + 表达数据（如"查找乳腺癌 RNA-seq 数据"）
- 用户需要 perturbation datasets（CRISPR、knockout、drug treatment）
- 用户需要单细胞数据或空间转录组数据
- 用户需要查询某物种（human / mouse / rat）的组学数据
- 用户需要获取 GSE ID 列表供下游下载

**不应触发此 skill 的情况：**

- 用户需要下载原始数据（调用 gse-downloader）
- 用户需要表达矩阵解析或预处理
- 用户需要执行差异分析等统计分析

## 使用方法

### 1. 环境准备

确保已安装 sra-search：

```bash
pip install "sra-search @ git+https://github.com/3H-Gene/geo-search-skill.git"
```

配置 NCBI 邮箱（必须）：

```bash
export SRA_SEARCH_NCBI_EMAIL=your@email.com
# 可选：配置 API Key 以提升速率限制（3次/秒 → 10次/秒）
export SRA_SEARCH_NCBI_API_KEY=your_api_key
```

### 2. 基本搜索

```bash
# 搜索痛风相关单细胞数据集
sra-search search "gout single cell"

# 指定数据源
sra-search search "diabetes RNA-seq" --sources geo --sources sra

# 限制返回数量
sra-search search "cancer scRNA-seq" --retmax 50

# 限制物种
sra-search search "gout" --organism human

# JSON 结构化输出
sra-search search "breast cancer scRNA-seq" --format json --top 20

# 仅输出 GSE ID 列表（适合管道处理）
sra-search search "liver fibrosis" --format id-list
```

### 3. LLM 语义排序（可选，V2 功能）

LLM 功能完全可选，不配置时自动回退到关键词模式。

#### 配置 LLM Provider

**方式 A：环境变量（推荐）**

```bash
# OpenAI
export SRA_SEARCH_LLM_PROVIDER=openai
export SRA_SEARCH_LLM_API_KEY=sk-...
export SRA_SEARCH_LLM_MODEL=gpt-4o-mini

# Anthropic
export SRA_SEARCH_LLM_PROVIDER=anthropic
export SRA_SEARCH_LLM_API_KEY=sk-ant-...
export SRA_SEARCH_LLM_MODEL=claude-3-5-haiku-20241022

# Google Gemini
export SRA_SEARCH_LLM_PROVIDER=google
export SRA_SEARCH_LLM_API_KEY=AIza...
export SRA_SEARCH_LLM_MODEL=gemini-2.0-flash

# 本地 Ollama（无需 API Key）
export SRA_SEARCH_LLM_PROVIDER=local
export SRA_SEARCH_LLM_MODEL=llama3.2
```

**方式 B：CLI 参数**

```bash
sra-search search "gout single cell" \
  --llm \
  --llm-provider google \
  --llm-api-key AIza... \
  --llm-model gemini-2.5-flash
```

#### 使用 LLM 功能

```bash
# 启用 LLM 语义排序
sra-search search "gout single cell" --llm

# 启用 LLM 排序 + 生成摘要
sra-search search "gout single cell" --llm --summarize

# 显示 LLM 解析的查询意图（调试用）
sra-search search "gout single cell" --llm --analyze-query

# 只对前 10 个结果做 LLM 评分（节省 Token）
sra-search search "gout single cell" --llm --llm-top-k 10

# 纯 LLM 模式（跳过 V1 关键词排序）
sra-search search "gout single cell" --llm-only
```

### 4. 查看和管理结果

```bash
# 列出所有已存储的数据集
sra-search list

# 宽格式显示（含组学类型、物种、样本数）
sra-search list --format wide

# 按审核状态筛选
sra-search list --status approved

# 查看数据集详情
sra-search show GSE123456

# 审核操作
sra-search review GSE123456 --status approved
sra-search review --batch --status approved  # 批量审核

# 查看配置
sra-search config
```

### 5. Agent 调用示例

通过 `scripts/agent_search.py` 进行 Agent 调用：

```bash
python scripts/agent_search.py "gout scRNA-seq" --llm --summarize --format json
```

## 支持的数据源

| 数据源 | 说明 |
|--------|------|
| GEO | Gene Expression Omnibus，基因表达数据集 |
| SRA | Sequence Read Archive，原始测序数据 |
| PubMed | 文献数据库，关联发表论文 |
| BioProject | NCBI BioProject，项目级别聚合 |

## 支持的组学类型

scRNA-Seq、snRNA-Seq、RNA-Seq、ATAC-Seq、ChIP-Seq、Hi-C、WGS、WES、Methylation、Microarray、Proteomics、Metabolomics 等 50+ 种组学类型。

## 依赖环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SRA_SEARCH_NCBI_EMAIL` | - | **必须**，NCBI 邮箱 |
| `SRA_SEARCH_NCBI_API_KEY` | - | 可选，提升速率限制 |
| `SRA_SEARCH_LLM_PROVIDER` | "" | `openai`/`anthropic`/`google`/`local` |
| `SRA_SEARCH_LLM_API_KEY` | "" | LLM API Key（空时禁用 LLM） |
| `SRA_SEARCH_LLM_MODEL` | "" | 模型名 |

## 注意事项

- 必须配置 NCBI 邮箱（`SRA_SEARCH_NCBI_EMAIL`）
- 推荐申请 [NCBI API Key](https://www.ncbi.nlm.nih.gov/account/)，可将速率限制从 3 次/秒提升至 10 次/秒
- LLM 功能为纯可选项，未配置 API Key 时自动回退到关键词排序模式
- 使用 LLM 功能需安装 `[llm]` 可选依赖：`pip install "sra-search[llm]"`
