"""
ptsd_research.py
================
Real PTSD research literature access — trials, papers, and funded
grants — combining sources already built and tested elsewhere in this
project. Same category as crispr_cancer_research.py: real published
facts, surfaced for a human to read, nothing predictive or diagnostic.

WHAT THIS DELIBERATELY DOES NOT DO
--------------------------------------
Does not diagnose, does not claim to detect or measure trauma, does
not process any individual's data. Pure literature search — the
equivalent of a very good PubMed/ClinicalTrials.gov search focused on
PTSD research specifically.

Usage
-----
    from ptsd_research import search_ptsd_research

    results = search_ptsd_research(biomarker="cortisol", max_results=5)
"""

from __future__ import annotations
from research_matcher import find_trials
from multi_source_research import search_pubmed
from extended_research_sources import search_nih_grants, search_europepmc


def search_ptsd_research(biomarker: str | None = None, max_results: int = 5) -> dict:
    trials = find_trials("PTSD", biomarker=biomarker, recruiting_only=True, max_results=max_results)
    papers = search_pubmed("PTSD", biomarker=biomarker, max_results=max_results)
    grants = search_nih_grants(f"PTSD {biomarker or ''}".strip(), max_results=max_results)
    preprints = search_europepmc(f"PTSD {biomarker or ''}".strip(), max_results=max_results)

    return {"trials": trials, "papers": papers, "grants": grants, "preprints_and_papers": preprints}


def format_results(results: dict) -> str:
    lines = [f"=== {len(results['trials'])} recruiting PTSD trials ==="]
    for t in results["trials"]:
        lines.append(f"  {t.get('title')}  [{t.get('nct_id')}]  {t.get('url')}")

    lines.append(f"\n=== {len(results['papers'])} PubMed papers ===")
    for p in results["papers"]:
        lines.append(f"  {p.get('title')}  ({p.get('pub_date')})  {p.get('url')}")

    lines.append(f"\n=== {len(results['grants'])} funded research grants ===")
    for g in results["grants"]:
        lines.append(f"  {g.get('title')}  ({g.get('fiscal_year')}, {g.get('org')})")

    lines.append(f"\n=== {len(results['preprints_and_papers'])} Europe PMC results ===")
    for e in results["preprints_and_papers"]:
        lines.append(f"  {e.get('title')}  ({e.get('pub_year')})  {e.get('url')}")

    return "\n".join(lines)


if __name__ == "__main__":
    print("=== Real PTSD + cortisol research ===\n")
    results = search_ptsd_research(biomarker="cortisol", max_results=3)
    print(format_results(results))
