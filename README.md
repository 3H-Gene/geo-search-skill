# SRA Search

多组学数据集智能检索与管理工具，支持 GEO/SRA/PubMed/BioProject 联合搜索、疾病知识图谱扩展、元数据提取和数据集审核。

---

## When to Use (触发条件)

**Agent 可通过以下情况触发此 skill：**

- 用户询问 GEO / GSE / 基因表达数据
- 用户需要 RNA-seq / scRNA-seq / ATAC-seq 数据集
- 用户提到疾病 + 表达数据（如"查找乳腺癌 RNA-seq 数据"）
- 用户需要 perturbation datasets（如 CRISPR、knockout、drug treatment）
- 用户需要单细胞数据或空间转录组数据
- 用户需要查询某物种（human / mouse / rat）的组学数据
- 用户需要获取 GSE ID 列表供下游下载

**不应触发此 skill 的情况：**
- 用户需要下载原始数据（调用 gse-downloader）
- 用户需要表达矩阵解析或预处理
- 用户需要执行差异分析等统计分析

---

## 设计定位

**geo-search-skill 应被严格定义为：GEO 数据集发现（Data Discovery）工具**

该工具只负责：
- 数据检索（search）
- 元数据解析（metadata abstraction）
- 结构化结果输出（structured output）

**明确不承担**：
- 数据下载
- 表达矩阵解析
- 数据预处理或分析

### 系统架构定位

```
geo-search-skill   →   gse-downloader   →   downstream analysis
   (发现数据)           (获取数据)             (分析)
```

设计原则：
- 单一职责（Single Responsibility）
- 松耦合（Loose Coupling）
- 可组合（Composable）

---

## 主要功能

- **多源联合检索** — 同时检索 GEO、SRA、PubMed、BioProject，结果自动去重合并
- **知识图谱扩展** — 自动展开疾病同义词（gout → hyperuricemia, uric acid...）、器官、组学类型
- **元数据提取** — 标准化提取疾病、器官、组学类型、物种、样本数等字段
- **数据集审核** — 支持 pending / approved / irrelevant / deleted 审核状态管理
- **可用性检查** — 自动验证数据集在 NCBI 的可用性
- **数据导出** — 支持 JSON / TSV 格式导出
- **速率限制** — 默认 3 次/秒，配置 API Key 后自动提升至 10 次/秒

---

## 安装方式

### 方式一：从 GitHub 直接安装（推荐）

```bash
pip install "git+https://github.com/3H-Gene/geo-search-skill.git"
```

### 方式二：使用 Conda/Mamba（推荐 HPC/生产环境）

```bash
git clone https://github.com/3H-Gene/geo-search-skill.git
cd geo-search-skill
mamba env create -f environment.yml
conda activate sra_search
pip install .
```

### 方式三：开发者模式

```bash
git clone https://github.com/3H-Gene/geo-search-skill.git
cd geo-search-skill
pip install -e ".[dev]"
```

### 验证安装

```bash
sra-search --version
```

---

## 快速开始

### 配置 NCBI 邮箱（必须）

```bash
export SRA_SEARCH_NCBI_EMAIL=your@email.com
# 可选：配置 API Key 以提升速率限制
export SRA_SEARCH_NCBI_API_KEY=your_api_key
```

### 关键词搜索

```bash
# 搜索痛风相关单细胞数据集
sra-search search "gout single cell"

# 指定数据源
sra-search search "diabetes RNA-seq" --sources geo --sources sra

# 限制返回数量
sra-search search "cancer scRNA-seq" --retmax 50

# JSON 结构化输出（与 gse-downloader 解耦）
sra-search search "breast cancer scRNA-seq" --format json --top 20

# 仅输出 GSE ID 列表（适合管道处理）
sra-search search "liver fibrosis" --format id-list
```

### 输出格式说明

| 格式 | 说明 | 适用场景 |
|------|------|----------|
| `table` | 表格形式（默认） | 交互式浏览 |
| `json` | 标准 JSON Schema 输出 | 程序集成、API |
| `id-list` | 仅 GSE ID 列表 | 管道处理、外部工具调用 |

### 查看搜索结果

```bash
# 列出所有已存储的数据集（简洁模式）
sra-search list

# 宽格式显示（含组学类型、物种、样本数）
sra-search list --format wide

# 按审核状态筛选
sra-search list --status approved

# 按可用性筛选
sra-search list --availability available

# 分页浏览
sra-search list --limit 50 --offset 100
```

### 查看数据集详情

```bash
sra-search show GSE123456
```

### 查看当前配置

```bash
sra-search config
```

---

## 使用示例

| 输入关键词 | 扩展后的查询 |
|-----------|-------------|
| `gout single cell` | `(gout OR hyperuricemia OR "uric acid") AND (scRNA-Seq OR "10x Genomics" OR Drop-seq)` |
| `diabetes RNA-seq` | `(diabetes OR T2D OR "type 2 diabetes") AND (RNA-seq OR "RNA sequencing")` |
| `liver fibrosis ATAC` | `(liver fibrosis OR hepatic fibrosis) AND (ATAC-Seq OR "chromatin accessibility")` |

---

## 目录结构

```
geo-search-skill/
├── data/
│   └── ontologies/              # 本体知识库（v1.1）
│       ├── doid_hierarchy.json  # 疾病本体（DOID）
│       ├── mesh_synonyms.json   # MeSH 同义词
│       ├── omics_types.json     # 组学类型标准词汇
│       └── uberon_organs.json   # 器官本体（Uberon）
├── docs/
│   └── ontology_audit_report.md # 本体审核报告
├── src/
│   └── sra_search/              # 核心包
│       ├── query/               # 查询处理 pipeline
│       │   ├── parser.py        # 结构化查询解析器
│       │   └── expander.py     # 本体知识扩展器
│       ├── retriever/           # 检索层
│       │   └── geo_api.py      # GEO API 封装 + 失败处理
│       ├── processor/           # 结果处理 pipeline
│       │   ├── filter.py       # 多维度结果过滤器
│       │   └── ranking.py      # Bio-aware 排序器
│       ├── schema.py           # 标准输出 Schema（强约束协议）
│       ├── converter.py        # 数据转换器
│       ├── cache.py            # 查询缓存
│       ├── knowledge_graph/    # 知识图谱（疾病/器官/组学扩展）
│       ├── search_engine/      # 多源检索引擎
│       ├── metadata_extractor/ # 元数据提取与标准化
│       ├── topic_manager/      # 主题管理
│       ├── review_manager/     # 审核管理
│       ├── data_store/         # SQLite 数据存储（WAL 模式）
│       ├── availability_checker/ # 可用性验证
│       ├── cli.py              # 命令行入口
│       └── config.py           # 配置管理
├── tests/                       # 测试套件
├── geo-search/                  # AI Skill（WorkBuddy/OpenClaw）
│   ├── SKILL.md
│   └── scripts/geo_search.py
├── environment.yml              # Conda 环境配置
├── pyproject.toml               # 项目构建配置
└── README.md
```

---

## 支持的数据源

| 数据源 | 说明 |
|--------|------|
| GEO | Gene Expression Omnibus，基因表达数据集 |
| SRA | Sequence Read Archive，原始测序数据 |
| PubMed | 文献数据库，关联发表论文 |
| BioProject | NCBI BioProject，项目级别聚合 |

## 支持的组学类型

RNA-Seq、scRNA-Seq、snRNA-Seq、ATAC-Seq、ChIP-Seq、Hi-C、WGS、WES、Methylation、Microarray、Proteomics、Metabolomics 等 50+ 种组学类型。

---

## 注意事项

- 必须配置 NCBI 邮箱（`SRA_SEARCH_NCBI_EMAIL`），否则 NCBI E-utilities 请求可能被拒绝
- 推荐申请 [NCBI API Key](https://www.ncbi.nlm.nih.gov/account/)，可将速率限制从 3 次/秒提升至 10 次/秒
- 默认请求频率遵守 NCBI 使用规范，请勿手动提高速率

---

## License

MIT
