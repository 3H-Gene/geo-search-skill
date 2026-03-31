# SRA Search

多组学数据集智能检索与管理工具，支持 GEO/SRA/PubMed/BioProject 联合搜索、疾病知识图谱扩展、元数据提取和数据集审核。

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
```

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
│       ├── knowledge_graph/     # 知识图谱（疾病/器官/组学扩展）
│       ├── search_engine/       # 多源检索引擎
│       ├── metadata_extractor/  # 元数据提取与标准化
│       ├── topic_manager/       # 主题管理
│       ├── review_manager/      # 审核管理
│       ├── data_store/          # SQLite 数据存储（WAL 模式）
│       ├── availability_checker/ # 可用性验证
│       ├── cli.py               # 命令行入口
│       └── config.py            # 配置管理
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
