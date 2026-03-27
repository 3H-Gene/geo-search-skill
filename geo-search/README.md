# GEO Search Skill

智能 GEO 数据集搜索工具，支持疾病同义词扩展和单细胞组学识别。

## 功能特性

- 🔍 **智能关键词扩展** - 自动识别疾病同义词 (gout → hyperuricemia, uric acid...)
- 🧬 **单细胞组学识别** - 支持多种单细胞技术术语 (scRNA-Seq, 10x Genomics, Drop-seq...)
- 🎯 **精准搜索** - 疾病用 OR，组学用 AND，构建最优查询
- 📊 **结果导出** - 支持 JSON 和 TSV 格式

## 安装

```bash
git clone https://github.com/3H-Gene/geo-search-skill.git
cd geo-search-skill
```

## 使用方法

### 基础搜索

```bash
python scripts/geo_search.py "gout hyperuricemia single cell"
```

### 指定返回数量

```bash
python scripts/geo_search.py "diabetes scRNA-seq" --retmax 50
```

### 指定输出目录

```bash
python scripts/geo_search.py "cancer RNA-seq" --output ./results
```

## 搜索示例

| 输入关键词 | 扩展后的查询 |
|-----------|-------------|
| `gout single cell` | `(gout OR hyperuricemia OR "uric acid") AND (scRNA-Seq OR "single-cell RNA-Seq" OR "10x Genomics")` |
| `diabetes RNA-seq` | `(diabetes OR T2D OR "type 2 diabetes") AND (RNA-seq OR "RNA sequencing")` |

## 目录结构

```
geo-search/
├── README.md           # 本文件
├── SKILL.md            # Skill 配置说明
└── scripts/
    └── geo_search.py   # 主搜索脚本
```

## 依赖

- Python 3.8+
- aiohttp
- biopython

## 许可证

MIT
