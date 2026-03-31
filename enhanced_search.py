# -*- coding: utf-8 -*-
"""
增强型搜索脚本
1. 直接搜索 GEO 数据库
2. 获取 PubMed 引用的 GSE 数据集
3. 增强单细胞同义词
"""
import asyncio
import json
import sys
from datetime import datetime
from typing import List, Dict, Set
sys.stdout.reconfigure(encoding='utf-8')

from sra_search.search_engine.query_builder import SmartQueryBuilder
from sra_search.search_engine.pubmed_searcher import PubMedSearcher
from sra_search.search_engine.base import EntrezClient


class EnhancedSearcher:
    """增强型搜索器"""
    
    def __init__(self):
        self.client = EntrezClient(email='test@example.com')
        self.pubmed = PubMedSearcher(self.client)
        self.builder = SmartQueryBuilder()
    
    async def search_geo_directly(self, smart_query: str, retmax: int = 50) -> Dict:
        """直接从 GEO 搜索"""
        # GEO 搜索
        geo_result = await self.client.esearch(db='gds', term=smart_query, retmax=retmax)
        geo_count = geo_result.get('esearchresult', {}).get('count', '0')
        geo_ids = geo_result.get('esearchresult', {}).get('idlist', [])
        
        # 获取 GEO 详情
        geo_datasets = []
        if geo_ids:
            summary_result = await self.client.esummary(db='gds', ids=geo_ids[:20])
            result_data = summary_result.get('result', {})
            uids = result_data.get('uids', [])
            for uid in uids:
                data = result_data.get(uid, {})
                geo_datasets.append({
                    'gse': data.get('gse', ''),
                    'title': data.get('title', ''),
                    'summary': data.get('summary', ''),
                    'organism': data.get('organism', []),
                    'pubmed_id': data.get('pubmed_id', ''),
                    'type': data.get('type', [])
                })
        
        return {
            'count': geo_count,
            'datasets': geo_datasets
        }
    
    async def get_referenced_gse(self, pmids: List[str]) -> Dict[str, Set[str]]:
        """从 PubMed 文章获取引用的 GSE 数据集"""
        import re
        
        pmid_to_gse: Dict[str, Set[str]] = {}
        all_gse: Set[str] = set()
        
        # 获取每篇文章的详细信息（包含参考文献）
        for pmid in pmids[:50]:  # 限制数量
            try:
                # ELink 获取链接到 GEO (ids 是列表)
                link_result = await self.client.elink(
                    dbfrom='pubmed',
                    db='gds',
                    ids=[pmid]
                )
                
                if isinstance(link_result, list):
                    for link in link_result:
                        linksetdbs = link.get('linksetdbs', [])
                        for db_link in linksetdbs:
                            if db_link.get('linkname') == 'pubmed_gds':
                                links = db_link.get('links', [])
                                for item in links:
                                    all_gse.add(str(item.get('id', '')))
                                if all_gse:
                                    pmid_to_gse[pmid] = all_gse.copy()
                
                # 也尝试从文章摘要中提取 GSE 编号
                summary = await self.client.esummary(db='pubmed', ids=[pmid])
                result = summary.get('result', {})
                data = result.get(pmid, {})
                text = data.get('title', '') + ' ' + data.get('source', '')
                
                # 提取 GSE 编号
                gse_matches = re.findall(r'\bGSE\d{4,}\b', text)
                if gse_matches:
                    all_gse.update(gse_matches)
                    pmid_to_gse[pmid] = set(gse_matches)
                    
            except Exception as e:
                print('Error getting GSE for PMID {}: {}'.format(pmid, e))
                continue
        
        return {
            'pmid_to_gse': pmid_to_gse,
            'all_gse': list(all_gse)
        }
    
    async def close(self):
        await self.client.close()


async def main():
    searcher = EnhancedSearcher()
    
    keyword = 'gout hyperuricemia single cell'
    smart_query, info = searcher.builder.build_query(keyword)
    
    print('='*70)
    print('Enhanced Search')
    print('='*70)
    print(f'Keywords: {keyword}')
    print(f'Smart Query: {smart_query}')
    print(f'Expanded omics: {info["omics"][:10]}')
    print()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 1. 直接搜索 GEO
    print('[1] Searching GEO directly...')
    geo_result = await searcher.search_geo_directly(smart_query, retmax=100)
    print(f'    Found {geo_result["count"]} GEO datasets')
    
    # 2. 获取 PubMed 并查找引用的 GSE
    print('[2] Searching PubMed for referenced GSE...')
    pmids = await searcher.pubmed.search(term=smart_query, retmax=100)
    print(f'    Found {len(pmids)} PubMed articles')
    
    ref_gse = await searcher.get_referenced_gse(pmids)
    print(f'    Found {len(ref_gse["all_gse"])} referenced GSE datasets')
    
    # 3. 保存结果
    output = {
        'keyword': keyword,
        'smart_query': smart_query,
        'geo_direct_count': geo_result['count'],
        'pubmed_count': len(pmids),
        'referenced_gse': ref_gse['all_gse'],
        'geo_datasets': geo_result['datasets']
    }
    
    json_path = 'output/search_results/{}_enhanced_gout.json'.format(timestamp)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print()
    print('Saved: ' + json_path)
    
    # 打印前几个 GSE
    if ref_gse['all_gse']:
        print()
        print('Referenced GSE datasets:')
        for gse in ref_gse['all_gse'][:10]:
            print(f'  - {gse}')
    
    if geo_result['datasets']:
        print()
        print('GEO datasets:')
        for ds in geo_result['datasets'][:5]:
            print(f'  - {ds.get("gse", "N/A")}: {ds.get("title", "")[:60]}...')
    
    await searcher.close()
    print()
    print('Done!')


if __name__ == '__main__':
    asyncio.run(main())
