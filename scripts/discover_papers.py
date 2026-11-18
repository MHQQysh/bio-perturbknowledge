"""Retrieve candidate PubMed records for human-reviewed CRISPRi screening."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


def request(endpoint, params):
    url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/' + endpoint + '?' + urlencode(params)
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'CRISPRi-course-project/1.0'}), timeout=60) as response:
                return response.read(), url
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def discover(query, limit, step=None):
    def lookup(endpoint, params, summarize):
        if step is None:
            return request(endpoint, params)
        with step(endpoint, params) as result:
            payload, url = request(endpoint, params)
            result.update(url=url, **summarize(payload))
            return payload, url

    raw, search_url = lookup(
        'esearch.fcgi', {'db': 'pubmed', 'term': query, 'retmax': limit, 'retmode': 'json'},
        lambda data: {'hits': int(json.loads(data)['esearchresult']['count']),
                      'ids': json.loads(data)['esearchresult']['idlist']},
    )
    search = json.loads(raw)['esearchresult']
    ids = search['idlist']
    papers = []
    fetch_url = None
    if ids:
        time.sleep(0.4)
        raw, fetch_url = lookup(
            'efetch.fcgi', {'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'xml'},
            lambda data: {'records_received': len(ET.fromstring(data).findall('PubmedArticle'))},
        )
        for item in ET.fromstring(raw).findall('PubmedArticle'):
            title = item.find('.//ArticleTitle')
            pmid = item.findtext('.//PMID')
            abstract = [''.join(part.itertext()) for part in item.findall('.//AbstractText')]
            papers.append({'pmid': pmid, 'title': ''.join(title.itertext()) if title is not None else '',
                           'abstract': '\n'.join(abstract), 'url': 'https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/',
                           'screening_status': 'requires_original_experiment_and_data_review'})
    return {'retrieved_at_utc': datetime.now(timezone.utc).isoformat(), 'query': query,
              'total_search_hits': int(search['count']), 'returned_papers': len(papers),
              'search_url': search_url, 'fetch_url': fetch_url, 'papers': papers,
              'scope': 'Retrieval candidates only; eligibility requires study and public data verification.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', default='(CRISPRi OR "CRISPR interference") AND ("single cell" OR "single-cell" OR "Perturb-seq" OR "CROP-seq")')
    parser.add_argument('--limit', type=int, default=40)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error('--limit must be between 1 and 200')
    result = discover(args.query, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'returned_papers': result['returned_papers'], 'total_search_hits': result['total_search_hits']}))


if __name__ == '__main__':
    main()
