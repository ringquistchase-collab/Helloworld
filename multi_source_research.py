"""
multi_source_research.py
================
Real PubMed (NCBI E-utilities) search returning normalized paper dicts
(title, pub_date, url). growing_research_agent.py's own PubMed fetcher
only returns bare PMIDs (fine for its change-detection use case); this
adds the esummary.fcgi round trip to get real title/date metadata,
for scripts that want to show a human what was actually found, not
just track new IDs.

Usage
-----
    from multi_source_research import search_pubmed

    papers = search_pubmed("PTSD", biomarker="cortisol", max_results=5)
"""

from __future__ import annotations
import json
import ssl
import time
import urllib.parse
import urllib.request

import certifi

NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_USER_AGENT = "MultiSourceResearch/1.0 (research script; contact: local user)"
_NCBI_MIN_INTERVAL_S = 0.35  # NCBI's <=3 req/s guideline without an API key

# See extended_research_sources.py's comment on this same pattern: an
# explicit certifi CA bundle is more portable than the platform default.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _http_get_json(url: str, params: dict) -> dict:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CONTEXT) as resp:
        return json.loads(resp.read())


def search_pubmed(condition: str, biomarker: str | None = None, max_results: int = 5) -> list[dict]:
    """Real NCBI esearch + esummary round trip. Returns [] on any request
    failure rather than raising."""
    term = f"{condition} AND {biomarker}" if biomarker else condition
    try:
        search_result = _http_get_json(NCBI_ESEARCH, {
            "db": "pubmed", "term": term, "retmode": "json", "retmax": max_results,
        })
        pmids = search_result.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        time.sleep(_NCBI_MIN_INTERVAL_S)
        summary = _http_get_json(NCBI_ESUMMARY, {
            "db": "pubmed", "id": ",".join(pmids), "retmode": "json",
        })
    except Exception:
        return []

    papers = []
    result = summary.get("result", {})
    for pmid in pmids:
        rec = result.get(pmid, {})
        if not rec:
            continue
        papers.append({
            "pmid": pmid,
            "title": rec.get("title"),
            "pub_date": rec.get("pubdate"),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return papers


if __name__ == "__main__":
    print("=== Real PubMed papers: PTSD + cortisol ===")
    for p in search_pubmed("PTSD", biomarker="cortisol", max_results=5):
        print(f"  {p['title']}  ({p['pub_date']})  {p['url']}")
