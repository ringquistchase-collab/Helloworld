"""
Offline regression tests for GrowingResearchAgent's source fetchers
(ClinicalTrials.gov, PubMed, ClinVar two-step, HGNC, St. Jude no-op).

In production these hit real public APIs (see KNOWN_GAPS.md on why the
live paths aren't in the suite). Here the module's async _http_get_json
is replaced with a router over canned JSON, so the real parsing,
two-step ClinVar sequencing, gene extraction, and failure handling run
deterministically and offline. Each test drives exactly one fetcher, so
routing by URL substring is unambiguous.
"""
import pytest

import growing_research_agent as gra
from growing_research_agent import GrowingResearchAgent


def _install_fake_http(monkeypatch, routes):
    async def fake_get_json(url, params=None, headers=None, timeout=20):
        for needle, payload in routes.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(gra, "_http_get_json", fake_get_json)


def _agent(tmp_path):
    return GrowingResearchAgent(
        node=None, dna=None,
        store_path=str(tmp_path / "store.json"),
        interval_s=999, max_topics=50,
    )


# -- PubMed ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_pubmed_prefixes_pmids(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": ["10", "20"]}},
    })
    ids = await _agent(tmp_path)._fetch_pubmed("breast cancer", "BRCA1")
    assert ids == ["PMID:10", "PMID:20"]


@pytest.mark.asyncio
async def test_fetch_pubmed_failure_returns_empty(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {"esearch.fcgi": RuntimeError("boom")})
    assert await _agent(tmp_path)._fetch_pubmed("x", None) == []


# -- ClinicalTrials.gov ----------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_clinicaltrials_ids_and_related_conditions(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {"clinicaltrials.gov": {"studies": [
        {"protocolSection": {
            "identificationModule": {"nctId": "NCT001"},
            "conditionsModule": {"conditions": ["Breast Cancer", "Lymphoma"]},
        }},
        {"protocolSection": {
            "identificationModule": {"nctId": "NCT002"},
            "conditionsModule": {"conditions": ["Melanoma"]},
        }},
    ]}})
    ids, related = await _agent(tmp_path)._fetch_clinicaltrials("breast cancer", None)
    assert ids == ["NCT001", "NCT002"]
    # the queried condition is excluded (case-insensitively); the rest remain
    assert set(related) == {"Lymphoma", "Melanoma"}


@pytest.mark.asyncio
async def test_fetch_clinicaltrials_failure_returns_empty_pair(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {"clinicaltrials.gov": RuntimeError("boom")})
    assert await _agent(tmp_path)._fetch_clinicaltrials("x", None) == ([], [])


# -- ClinVar (two-step esearch -> esummary) --------------------------------

@pytest.mark.asyncio
async def test_fetch_clinvar_ids_and_candidate_genes(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": ["u1", "u2"]}},
        "esummary.fcgi": {"result": {
            "u1": {"genes": [{"symbol": "BRCA1"}, {"symbol": "TP53"}]},
            "u2": {"genes": [{"symbol": "brca1"}]},
        }},
    })
    ids, genes = await _agent(tmp_path)._fetch_clinvar("breast cancer", "BRCA1")
    assert ids == ["ClinVar:u1", "ClinVar:u2"]
    # the biomarker itself is excluded (case-insensitively); others uppercased
    assert genes == ["TP53"]


@pytest.mark.asyncio
async def test_fetch_clinvar_no_uids_short_circuits(monkeypatch, tmp_path):
    # No esummary route: if the code called it despite an empty idlist, the
    # router would AssertionError. An empty idlist must return before that.
    _install_fake_http(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": []}},
    })
    assert await _agent(tmp_path)._fetch_clinvar("x", "GENE") == ([], [])


@pytest.mark.asyncio
async def test_fetch_clinvar_esummary_failure_keeps_ids(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {
        "esearch.fcgi": {"esearchresult": {"idlist": ["u1", "u2"]}},
        "esummary.fcgi": RuntimeError("summary down"),
    })
    ids, genes = await _agent(tmp_path)._fetch_clinvar("x", "GENE")
    assert ids == ["ClinVar:u1", "ClinVar:u2"]
    assert genes == []


# -- HGNC ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_hgnc_returns_normalized_symbol(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {"rest.genenames.org": {
        "response": {"docs": [{"symbol": "TP53"}]},
    }})
    assert await _agent(tmp_path)._fetch_hgnc("tp53") == "TP53"


@pytest.mark.asyncio
async def test_fetch_hgnc_no_docs_returns_none(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {"rest.genenames.org": {"response": {"docs": []}}})
    assert await _agent(tmp_path)._fetch_hgnc("notagene") is None


@pytest.mark.asyncio
async def test_fetch_hgnc_failure_returns_none(monkeypatch, tmp_path):
    _install_fake_http(monkeypatch, {"rest.genenames.org": RuntimeError("boom")})
    assert await _agent(tmp_path)._fetch_hgnc("BRCA1") is None


# -- St. Jude (deliberate no-op) -------------------------------------------

@pytest.mark.asyncio
async def test_fetch_stjude_is_noop(tmp_path):
    # Documented no-op: no endpoint, returns [] without any HTTP call.
    assert await _agent(tmp_path)._fetch_stjude("x", "GENE") == []
