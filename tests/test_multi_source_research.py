"""
Offline regression tests for multi_source_research.search_pubmed.

The real function makes an NCBI esearch + esummary round trip over the
network (see KNOWN_GAPS.md on why the live path isn't in the suite).
These tests mock urllib.request.urlopen so the real term-building,
two-step sequencing, and JSON parsing are exercised deterministically
without touching the network.
"""
import json
import urllib.parse
import urllib.request
from urllib.error import URLError

import pytest

import multi_source_research as msr


class _FakeResp:
    def __init__(self, payload):
        self._data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_urlopen(monkeypatch, routes, captured=None):
    """routes: {url_substring: payload_dict | Exception}. Raises AssertionError
    if the code requests a URL no route matches (so an unexpected call fails
    loudly instead of silently)."""
    def fake(req, *args, **kwargs):
        url = req.full_url
        if captured is not None:
            captured.append(url)
        for needle, payload in routes.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return _FakeResp(payload)
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(msr.urllib.request, "urlopen", fake)
    # keep the mandated NCBI spacing from actually sleeping in tests
    monkeypatch.setattr(msr.time, "sleep", lambda *a, **k: None)


def test_happy_path_parses_title_date_and_url(monkeypatch):
    _install_fake_urlopen(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": ["111", "222"]}},
        "esummary.fcgi": {"result": {
            "111": {"title": "First paper", "pubdate": "2024 Jan"},
            "222": {"title": "Second paper", "pubdate": "2023 Dec"},
        }},
    })
    papers = msr.search_pubmed("breast cancer", biomarker="BRCA1", max_results=5)
    assert papers == [
        {"pmid": "111", "title": "First paper", "pub_date": "2024 Jan",
         "url": "https://pubmed.ncbi.nlm.nih.gov/111/"},
        {"pmid": "222", "title": "Second paper", "pub_date": "2023 Dec",
         "url": "https://pubmed.ncbi.nlm.nih.gov/222/"},
    ]


def test_biomarker_is_anded_into_the_search_term(monkeypatch):
    captured = []
    _install_fake_urlopen(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": ["1"]}},
        "esummary.fcgi": {"result": {"1": {"title": "t", "pubdate": "2024"}}},
    }, captured=captured)
    msr.search_pubmed("PTSD", biomarker="cortisol")
    esearch_url = next(u for u in captured if "esearch.fcgi" in u)
    term = urllib.parse.parse_qs(urllib.parse.urlparse(esearch_url).query)["term"][0]
    assert term == "PTSD AND cortisol"


def test_no_biomarker_uses_bare_condition_as_term(monkeypatch):
    captured = []
    _install_fake_urlopen(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": ["1"]}},
        "esummary.fcgi": {"result": {"1": {"title": "t", "pubdate": "2024"}}},
    }, captured=captured)
    msr.search_pubmed("glioblastoma")
    esearch_url = next(u for u in captured if "esearch.fcgi" in u)
    term = urllib.parse.parse_qs(urllib.parse.urlparse(esearch_url).query)["term"][0]
    assert term == "glioblastoma"


def test_empty_idlist_returns_empty_and_skips_esummary(monkeypatch):
    captured = []
    # No esummary route on purpose: if the code tried to call it, the fake
    # would AssertionError -- but an empty idlist must short-circuit first.
    _install_fake_urlopen(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": []}},
    }, captured=captured)
    assert msr.search_pubmed("nonexistent condition xyz") == []
    assert all("esummary.fcgi" not in u for u in captured)


def test_request_failure_returns_empty_list(monkeypatch):
    _install_fake_urlopen(monkeypatch, {
        "esearch.fcgi": URLError("network down"),
    })
    assert msr.search_pubmed("breast cancer", biomarker="BRCA1") == []


def test_summary_records_missing_for_some_pmids_are_skipped(monkeypatch):
    _install_fake_urlopen(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": ["1", "2"]}},
        # esummary only has a record for "1" -- "2" must be dropped, not crash
        "esummary.fcgi": {"result": {"1": {"title": "only one", "pubdate": "2024"}}},
    })
    papers = msr.search_pubmed("x")
    assert [p["pmid"] for p in papers] == ["1"]
