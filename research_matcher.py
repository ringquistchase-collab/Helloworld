"""
research_matcher.py
================
Real ClinicalTrials.gov API v2 search, returning a normalized list of
trial dicts (title, nct_id, url) rather than growing_research_agent.py's
raw NCT-ID list. This is a synchronous, single-shot search utility for
scripts like ptsd_research.py, not a persistent background agent --
see growing_research_agent.py for that pattern; this module
deliberately doesn't reuse its async machinery since a one-shot
search doesn't need it.

Usage
-----
    from research_matcher import find_trials

    trials = find_trials("PTSD", biomarker="cortisol", recruiting_only=True, max_results=5)
"""

from __future__ import annotations
import json
import ssl
import urllib.parse
import urllib.request

import certifi

CLINICALTRIALS_API = "https://clinicaltrials.gov/api/v2/studies"
_USER_AGENT = "ResearchMatcher/1.0 (research script; contact: local user)"

# See extended_research_sources.py's comment on this same pattern: an
# explicit certifi CA bundle is more portable than the platform default.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def find_trials(
    condition: str, biomarker: str | None = None,
    recruiting_only: bool = False, max_results: int = 5,
) -> list[dict]:
    """Real ClinicalTrials.gov v2 search. Returns [] on any request
    failure rather than raising -- a bad network day shouldn't crash a
    caller that's just trying to show what it could find."""
    params = {"query.cond": condition, "pageSize": max_results, "fields": "NCTId,BriefTitle,OverallStatus"}
    if biomarker:
        params["query.term"] = biomarker
    if recruiting_only:
        params["filter.overallStatus"] = "RECRUITING"

    url = f"{CLINICALTRIALS_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CONTEXT) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []

    trials = []
    for study in data.get("studies", []):
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        nct_id = ident.get("nctId")
        if not nct_id:
            continue
        trials.append({
            "nct_id": nct_id,
            "title": ident.get("briefTitle"),
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
        })
    return trials[:max_results]


if __name__ == "__main__":
    print("=== Real recruiting trials: PTSD + cortisol ===")
    for t in find_trials("PTSD", biomarker="cortisol", recruiting_only=True, max_results=5):
        print(f"  {t['title']}  [{t['nct_id']}]  {t['url']}")
