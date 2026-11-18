"""Retrieve PMC full text and supplementary material, retaining source and limitations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile
from io import BytesIO
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from discover_papers import discover

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MAX_BYTES = 25 * 1024 * 1024


def get(url):
    request = Request(url, headers={"User-Agent": "CRISPRi-course-project/1.0"})
    with urlopen(request, timeout=40) as response:
        if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
            raise ValueError("File exceeds 25 MiB intake limit")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("File exceeds 25 MiB intake limit")
        return data, response.geturl()


def validate_supplement(content, filename):
    """Reject challenge pages and truncated or mislabeled common file formats."""
    head = content.lstrip()[:512].lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"recaptcha" in head:
        raise ValueError("Website returned an HTML access or verification page")
    suffix = Path(filename).suffix.lower()
    if suffix in {".xlsx", ".zip"}:
        if not zipfile.is_zipfile(BytesIO(content)):
            raise ValueError(f"Invalid {suffix} archive")
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if archive.testzip() is not None:
                raise ValueError("Archive CRC check failed")
            if suffix == ".xlsx" and "[Content_Types].xml" not in archive.namelist():
                raise ValueError("File is not an XLSX workbook")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("File is not a PDF")


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pmc_ids(pmid):
    query = urlencode({"dbfrom": "pubmed", "db": "pmc", "id": pmid, "retmode": "json"})
    raw, _ = get(BASE + "elink.fcgi?" + query)
    result = json.loads(raw)
    ids = []
    for linkset in result.get("linksets", []):
        for linkdb in linkset.get("linksetdbs", []):
            ids.extend(linkdb.get("links", []))
    return list(dict.fromkeys(str(i) for i in ids))


def intake(paper, directory, supplement_limit):
    item = {"pmid": paper["pmid"], "title": paper["title"], "pubmed_url": paper["url"],
            "screening_status": "pending_evidence_review", "article": None, "supplements": [],
            "geo_accessions": [], "limitations": []}
    try:
        ids = pmc_ids(paper["pmid"])
        if not ids:
            item["limitations"].append("PMC full text unavailable through PubMed link")
            return item
        pmcid = ids[0] if ids[0].upper().startswith("PMC") else "PMC" + ids[0]
        data, resolved = get(BASE + "efetch.fcgi?" + urlencode({"db": "pmc", "id": pmcid}))
        root = ET.fromstring(data)
        article = root.find(".//article")
        if article is None:
            raise ValueError("PMC response did not contain an article")
        paper_dir = directory / paper["pmid"]
        paper_dir.mkdir(parents=True, exist_ok=True)
        xml_file = paper_dir / "article.xml"
        xml_file.write_bytes(data)
        sections = []
        for sec in article.findall(".//sec"):
            title = sec.find("title")
            paragraphs = [" ".join("".join(p.itertext()).split()) for p in sec.findall("./p")]
            if paragraphs:
                sections.append({"heading": " ".join("".join(title.itertext()).split()) if title is not None else "",
                                 "paragraphs": paragraphs})
        save_json(paper_dir / "sections.json", sections)
        evidence = []
        terms = re.compile(r"CRISPRi|CRISPR interference|single.cell|sgRNA|non.target|GSE\d+|GSM\d+", re.I)
        for section in sections:
            for index, paragraph in enumerate(section["paragraphs"], 1):
                if terms.search(paragraph):
                    evidence.append({"heading": section["heading"], "paragraph_in_section": index,
                                     "text": paragraph, "source": pmcid,
                                     "review_status": "candidate_evidence_requires_review"})
        save_json(paper_dir / "evidence-candidates.json", evidence)
        text = " ".join(article.itertext())
        item["geo_accessions"] = sorted(set(re.findall(r"\b(?:GSE|GSM)\d+\b", text, re.I)))
        doi_node = article.find(".//article-id[@pub-id-type='doi']")
        doi = doi_node.text.strip() if doi_node is not None and doi_node.text else None
        if doi:
            item["doi"] = doi
            try:
                openalex_url = "https://api.openalex.org/works/https://doi.org/" + doi
                metadata, _ = get(openalex_url)
                work = json.loads(metadata)
                item["alternative_sources"] = [
                    {"repository": (location.get("source") or {}).get("display_name"),
                     "landing_page_url": location.get("landing_page_url"),
                     "pdf_url": location.get("pdf_url")}
                    for location in work.get("locations", [])
                    if location.get("landing_page_url") or location.get("pdf_url")
                ]
            except Exception as exc:
                item["limitations"].append(f"Alternative-source lookup failed: {type(exc).__name__}: {exc}")
        item["article"] = {"pmcid": pmcid, "source_url": resolved,
                           "file": str(xml_file), "sections": str(paper_dir / "sections.json"),
                           "sha256": hashlib.sha256(data).hexdigest(), "section_count": len(sections),
                           "evidence_candidates": str(paper_dir / "evidence-candidates.json"),
                           "evidence_candidate_count": len(evidence)}
        supplementary_links = []
        for node in article.findall(".//supplementary-material"):
            for e in node.iter():
                for k, v in e.attrib.items():
                    if k.endswith("}href") and v and not v.lower().endswith((".html", ".htm")):
                        supplementary_links.append(v)
        for href in dict.fromkeys(supplementary_links):
            url = urljoin("https://pmc.ncbi.nlm.nih.gov/articles/instance/" + pmcid[3:] + "/bin/", href)
            entry = {"url": url, "status": "discovered"}
            if len(item["supplements"]) < supplement_limit and urlparse(url).hostname == "pmc.ncbi.nlm.nih.gov":
                try:
                    content, final_url = get(url)
                    filename = re.sub(r"[^A-Za-z0-9._-]", "_", Path(urlparse(final_url).path).name)
                    if not filename:
                        raise ValueError("Supplement lacks a filename")
                    validate_supplement(content, filename)
                    target = paper_dir / filename
                    target.write_bytes(content)
                    entry.update(status="downloaded", file=str(target), source_url=final_url,
                                 sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
                except Exception as exc:
                    entry.update(status="unavailable", reason=str(exc))
            item["supplements"].append(entry)
        if not item["supplements"]:
            item["limitations"].append("No supplementary file links found in PMC XML")
        elif any(s["status"] != "downloaded" for s in item["supplements"]):
            item["limitations"].append("Some supplementary files were not downloaded; inspect their individual status")
    except Exception as exc:
        item["limitations"].append(f"Intake failed: {type(exc).__name__}: {exc}")
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="PubMed search query expressing task scope")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--max-fulltext", type=int, default=3)
    parser.add_argument("--max-supplements", type=int, default=3)
    parser.add_argument("--output", type=Path, help="Local run directory; default: project downloads/paper-intake/<timestamp>")
    args = parser.parse_args()
    if not 1 <= args.limit <= 200 or not 0 <= args.max_fulltext <= args.limit or not 0 <= args.max_supplements <= 10:
        parser.error("limit 1–200; fulltext 0–limit; supplements 0–10")
    if args.output is None:
        root = Path(__file__).resolve().parents[1]
        args.output = root / "downloads" / "paper-intake" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "query": args.query,
              "pipeline": "discovery_and_paper_intake", "statuses": {}}
    try:
        candidates = discover(args.query, args.limit)
        save_json(args.output / "candidates.json", candidates)
        result["statuses"]["search"] = "completed"
        items = [intake(paper, args.output, args.max_supplements)
                 for paper in candidates["papers"][:args.max_fulltext]]
        save_json(args.output / "intake.json", items)
        result["statuses"]["paper_and_supplement_intake"] = "completed_with_limitations" if any(i["limitations"] for i in items) else "completed"
        result["candidate_count"] = len(candidates["papers"])
        result["fulltext_downloaded"] = sum(bool(i["article"]) for i in items)
        result["statuses"]["eligibility_and_extraction"] = "pending_evidence_review"
        result["statuses"]["data_matching_and_response"] = "not_run"
        result["limitations"] = ["PMC intake is not scientific extraction or eligibility validation.",
                                 "Generic studies require an explicit dataset adapter and verified groups before analysis."]
    except Exception as exc:
        result["statuses"]["search"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_json(args.output / "run.json", result)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1 if result["statuses"]["search"] == "failed" else 0)


if __name__ == "__main__":
    main()
