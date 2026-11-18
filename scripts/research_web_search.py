"""Search OpenAlex and resolve publication locations, saving provenance locally."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import xml.etree.ElementTree as ET


def fetch(url):
    request = Request(url, headers={"User-Agent": "CRISPRi-course-project/1.0"})
    with urlopen(request, timeout=40) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    search = parser.add_mutually_exclusive_group(required=True)
    search.add_argument("--query", help="Search paper title, topic, authors, or DOI")
    search.add_argument("--doi", help="Resolve one work by DOI")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path, help="JSON file; default is a timestamped local search")
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be 1–50")
    provider = "OpenAlex public API"
    if args.doi:
        doi = args.doi.removeprefix("https://doi.org/")
        if not doi.startswith("10.") or any(c.isspace() for c in doi):
            parser.error("Expected a DOI such as 10.1101/gr.262295.120")
        url = "https://api.openalex.org/works/https://doi.org/" + doi
        works = [fetch(url)]
    else:
        url = "https://api.openalex.org/works?" + urlencode({"search": args.query, "per-page": args.limit})
        try:
            works = fetch(url).get("results", [])
        except HTTPError as exc:
            if exc.code != 429:
                raise
            provider = "NCBI PubMed E-utilities fallback"
            base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
            url = base + "esearch.fcgi?" + urlencode({"db": "pubmed", "term": args.query, "retmax": args.limit, "retmode": "json"})
            ids = fetch(url)["esearchresult"]["idlist"]
            works = []
            if ids:
                request = Request(base + "efetch.fcgi?" + urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}),
                                  headers={"User-Agent": "CRISPRi-course-project/1.0"})
                with urlopen(request, timeout=40) as response:
                    root = ET.fromstring(response.read())
                for article in root.findall("PubmedArticle"):
                    pmid = article.findtext(".//PMID")
                    title = article.find(".//ArticleTitle")
                    doi_node = article.find(".//ArticleId[@IdType='doi']")
                    works.append({"title": "".join(title.itertext()) if title is not None else None,
                                  "doi": "https://doi.org/" + doi_node.text if doi_node is not None and doi_node.text else None,
                                  "id": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
                                  "publication_year": article.findtext(".//PubDate/Year"),
                                  "locations": [{"source": {"display_name": "PubMed"},
                                                 "landing_page_url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/"}]})
    results = []
    for work in works:
        locations = []
        for location in work.get("locations") or []:
            source = location.get("source") or {}
            locations.append({"source_name": source.get("display_name"),
                              "landing_page_url": location.get("landing_page_url"),
                              "pdf_url": location.get("pdf_url"),
                              "is_oa": location.get("is_oa"),
                              "version": location.get("version")})
        results.append({"title": work.get("title"), "doi": work.get("doi"), "id": work.get("id"),
                        "publication_year": work.get("publication_year"), "locations": locations})
    output = args.output
    if output is None:
        output = Path(__file__).resolve().parents[1] / "downloads/web-search" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        parser.error("Output already exists; specify a new file")
    payload = {"searched_at": datetime.now(timezone.utc).isoformat(), "provider": provider,
               "request_url": url, "results": results,
               "scope": "Bibliographic search and source locations; candidates require scientific review. No supplement or dataset verification is implied."}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "results": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
