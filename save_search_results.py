# -*- coding: utf-8 -*-
import asyncio
import json
import sys
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

from sra_search.search_engine.query_builder import SmartQueryBuilder
from sra_search.search_engine.pubmed_searcher import PubMedSearcher
from sra_search.search_engine.base import EntrezClient


async def search_and_save():
    client = EntrezClient(email='test@example.com')
    pubmed = PubMedSearcher(client)
    builder = SmartQueryBuilder()
    
    keyword = 'gout hyperuricemia single cell'
    smart_query, info = builder.build_query(keyword)
    
    # 获取 PubMed 结果
    results, geo_mapping = await pubmed.search_and_fetch(term=smart_query, retmax=20, link_to_geo=True)
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # JSON 格式
    output = {
        'keyword': keyword,
        'smart_query': smart_query,
        'classification': info,
        'pubmed_count': len(results),
        'geo_mapping': geo_mapping,
        'results': [
            {
                'pmid': r.pmid,
                'title': r.title,
                'journal': r.journal,
                'publication_date': r.publication_date,
                'authors': r.authors,
                'gse_ids': r.gse_ids
            }
            for r in results
        ]
    }
    
    json_path = 'output/search_results/{}_pubmed_gout.json'.format(timestamp)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('Saved: ' + json_path)
    
    # TSV 格式
    tsv_path = 'output/search_results/{}_pubmed_gout.tsv'.format(timestamp)
    with open(tsv_path, 'w', encoding='utf-8') as f:
        f.write('PMID\tTitle\tJournal\tDate\tGSE\n')
        for r in results:
            title = r.title.replace('\t', ' ').replace('\n', ' ')
            gse = ','.join(r.gse_ids)
            f.write('{}\t{}\t{}\t{}\t{}\n'.format(r.pmid, title, r.journal, r.publication_date, gse))
    print('Saved: ' + tsv_path)
    
    # GEO 结果
    geo_result = await client.esearch(db='gds', term=smart_query, retmax=50)
    geo_count = geo_result.get('esearchresult', {}).get('count', '0')
    geo_ids = geo_result.get('esearchresult', {}).get('idlist', [])
    
    geo_output = {
        'keyword': keyword,
        'smart_query': smart_query,
        'count': geo_count,
        'ids': geo_ids[:50]
    }
    
    geo_json_path = 'output/search_results/{}_geo_gout.json'.format(timestamp)
    with open(geo_json_path, 'w', encoding='utf-8') as f:
        json.dump(geo_output, f, ensure_ascii=False, indent=2)
    print('Saved: ' + geo_json_path)
    
    await client.close()
    print('Done!')


if __name__ == '__main__':
    asyncio.run(search_and_save())
