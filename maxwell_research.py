"""
maxwell_research.py
================
Real arXiv search for Maxwell's-equations / electromagnetism physics
literature. This is the CORRECT source for this — the existing
research sources (ClinicalTrials.gov, PubMed, ClinVar, St. Jude, NIH
grants, Europe PMC) are all medical/biomedical and would return noise
for physics queries. arXiv is where real physics preprints/papers
actually live, free public API, no auth needed.

Usage
-----
    from maxwell_research import search_arxiv

    papers = search_arxiv("Maxwell equations electromagnetic field", max_results=5)
"""

from __future__ import annotations
import ssl
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

import certifi

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# See extended_research_sources.py's comment on this same pattern: an
# explicit certifi CA bundle is more portable than the platform default.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    params = {"search_query": f"all:{query}", "start": 0, "max_results": max_results}
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=15, context=_SSL_CONTEXT) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)

        results = []
        for entry in root.findall("atom:entry", ATOM_NS):
            title = entry.find("atom:title", ATOM_NS)
            id_url = entry.find("atom:id", ATOM_NS)
            updated = entry.find("atom:updated", ATOM_NS)
            results.append({
                "title": title.text.strip().replace("\n", " ") if title is not None else None,
                "url": id_url.text if id_url is not None else None,
                "updated": updated.text if updated is not None else None,
            })
        return results
    except Exception as e:
        return [{"error": f"arXiv request failed: {e}"}]


if __name__ == "__main__":
    print("=== Real arXiv search: Maxwell's equations ===")
    for p in search_arxiv("Maxwell equations electromagnetic field", max_results=5):
        if "error" in p:
            print(f"  ERROR: {p['error']}")
            continue
        print(f"  {p.get('title')}")
        print(f"    {p.get('url')}  ({p.get('updated')})")
