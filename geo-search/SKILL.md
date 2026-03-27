---
name: geo-search
description: 基于知识图谱的 GEO 数据集智能搜索工具。用于在 SRA_search 项目中搜索 GEO 数据库，支持疾病同义词扩展、单细胞组学类型识别、智能查询构建。
---

# GEO Search Skill

## 概述

本 Skill 提供 GEO 数据集的智能搜索功能，基于 SRA_search 项目框架，支持：
- 疾病同义词自动扩展（如 gout → hyperuricemia, uric acid...）
- 单细胞组学类型识别（scRNA-seq, single cell, 10x Genomics...）
- 智能 OR/AND 查询逻辑构建

## 使用场景

当用户需要：
- 搜索 GEO 数据库中的组学数据集
- 根据疾病和组学类型组合搜索
- 获取高相关的 GEO 数据集列表

## 使用方法

### 1. 环境准备

确保在 SRA_search 项目目录下操作，已安装依赖：
```bash
cd d:/Programs/workspace/SRA_search
.\venv\Scripts\python.exe -c "import sra_search"
```

### 2. 运行搜索

执行 `scripts/geo_search.py` 脚本：

```powershell
.\venv\Scripts\python.exe .workbuddy/skills/geo-search/scripts/geo_search.py "痛风 高尿酸 单细胞"
```

参数说明：
- 第1个参数：搜索关键词（支持中英文逗号分隔）
- 可选参数 `--retmax`：返回结果数量（默认50）
- 可选参数 `--output`：输出目录（默认 output/search_results/）

### 3. 输出结果

脚本会自动：
1. 调用 SmartQueryBuilder 构建智能查询
2. 搜索 GEO 数据库
3. 获取数据集详细信息
4. 保存为 JSON 格式到指定目录

## 依赖模块

- `sra_search.knowledge_graph.graph.KnowledgeGraph`
- `sra_search.search_engine.query_builder.SmartQueryBuilder`
- `sra_search.search_engine.base.EntrezClient`

## 扩展同义词示例

### 疾病
- gout → gout, hyperuricemia, uric acid, monosodium urate...
- diabetes → diabetes, diabetes mellitus, DM...

### 组学类型
- single cell → scRNA-Seq, single-cell RNA-Seq, 10x Genomics...
- RNA-Seq → RNA-Seq, transcriptome, mRNA-Seq...

## 输出文件格式

```json
{
  "keyword": "gout hyperuricemia single cell",
  "smart_query": "(gout OR hyperuricemia...) AND (single-cell RNA-Seq...)",
  "count": "23",
  "datasets": [
    {
      "gse": "GSE258959",
      "title": "Spatiotemporal Landscape in Kidney...",
      "summary": "...",
      "organism": ["Mus musculus"],
      "pubmed_id": "39817712",
      "type": ["Expression profiling by high throughput sequencing"]
    }
  ]
}
```
