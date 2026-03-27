# -*- coding: utf-8 -*-
"""
GEO Dataset Smart Search Script

Usage:
    python geo_search.py "gout hyperuricemia single cell" [--retmax 50] [--output output/search_results/]
"""
import asyncio
import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from sra_search.search_engine.query_builder import SmartQueryBuilder
from sra_search.search_engine.base import EntrezClient


class GEOSearcher:
    """GEO 数据集搜索器"""
    
    def __init__(self, email: str = "test@example.com"):
        self.client = EntrezClient(email=email)
        self.builder = SmartQueryBuilder()
    
    async def search(self, keywords: str, retmax: int = 50) -> dict:
        """搜索 GEO 数据集"""
        
        # 构建智能查询
        smart_query, info = self.builder.build_query(keywords)
        
        print(f"Keywords: {keywords}")
        print(f"Smart Query: {smart_query}")
        print(f"Expanded omics: {info['omics'][:5]}")
        print()
        
        # 搜索 GEO
        geo_result = await self.client.esearch(db='gds', term=smart_query, retmax=retmax)
        geo_count = geo_result.get('esearchresult', {}).get('count', '0')
        geo_ids = geo_result.get('esearchresult', {}).get('idlist', [])
        
        print(f"Found {geo_count} GEO datasets")
        
        # 获取详情
        datasets = []
        if geo_ids:
            # 限制详情数量
            detail_ids = geo_ids[:min(20, len(geo_ids))]
            summary_result = await self.client.esummary(db='gds', ids=detail_ids)
            result_data = summary_result.get('result', {})
            uids = result_data.get('uids', [])
            
            for uid in uids:
                data = result_data.get(uid, {})
                datasets.append({
                    'gse': data.get('gse', ''),
                    'gsm': data.get('gsm', ''),
                    'title': data.get('title', ''),
                    'summary': data.get('summary', ''),
                    'organism': data.get('organism', []),
                    'pubmed_id': data.get('pubmed_id', ''),
                    'type': data.get('type', []),
                    'platform': data.get('platform', '')
                })
        
        return {
            'keyword': keywords,
            'smart_query': smart_query,
            'classification': info,
            'count': geo_count,
            'datasets': datasets
        }
    
    async def close(self):
        await self.client.close()


async def main():
    parser = argparse.ArgumentParser(description='GEO Dataset Smart Search')
    parser.add_argument('keywords', help='Search keywords')
    parser.add_argument('--retmax', type=int, default=50, help='Max results')
    parser.add_argument('--output', default='output/search_results/', help='Output directory')
    parser.add_argument('--email', default='test@example.com', help='NCBI email')
    
    args = parser.parse_args()
    
    # 确保输出目录存在
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 搜索
    searcher = GEOSearcher(email=args.email)
    result = await searcher.search(args.keywords, retmax=args.retmax)
    await searcher.close()
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    # 清理关键词作为文件名
    safe_name = args.keywords.replace(' ', '_').replace(',', '_')[:30]
    filename = f'{timestamp}_geo_{safe_name}.json'
    output_path = output_dir / filename
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print()
    print(f"Saved: {output_path}")
    print(f"Total: {result['count']} datasets")
    
    # 打印前5个
    if result['datasets']:
        print()
        print("Top datasets:")
        for ds in result['datasets'][:5]:
            print(f"  - {ds.get('gse', 'N/A')}: {ds.get('title', '')[:50]}...")


if __name__ == '__main__':
    asyncio.run(main())
