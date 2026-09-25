"""
Offline regression tests for research_matcher.find_trials
(ClinicalTrials.gov v2, one-shot synchronous search).

Mocks urllib.request.urlopen so the real JSON field-mapping,
no-nctId skipping, max_results slicing, optional filter/biomarker
params, and failure-returns-[] behavior run deterministically offline.
"""
import json
import urllib.parse
from urllib.error import URLError

import pytest

import research_matcher as rm


class _FakeResp:
    def __init__(self, payload):
        self._data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_urlopen(monkeypatch, payload, captured=None):
    def fake(req, *args, **kwargs):
        if captured is not None:
            captured.append(req.full_url)
        if isinstance(payload, Exception):
            raise payload
        return _FakeResp(payload)

    monkeypatch.setattr(rm.urllib.request, "urlopen", fake)


def _study(nct_id, title="A trial", status="RECRUITING"):
    return {"protocolSection": {"identificationModule": {
        "nctId": nct_id, "briefTitle": title, "overallStatus": status,
    }}}


def test_maps_nct_id_title_and_builds_study_url(monkeypatch):
    _install_fake_urlopen(monkeypatch, {"studies": [
        _study("NCT001", "Trial One"),
        _study("NCT002", "Trial Two"),
    ]})
    trials = rm.find_trials("PTSD")
    assert trials == [
        {"nct_id": "NCT001", "title": "Trial One", "url": "https://clinicaltrials.gov/study/NCT001"},
        {"nct_id": "NCT002", "title": "Trial Two", "url": "https://clinicaltrials.gov/study/NCT002"},
    ]


def test_studies_without_nct_id_are_skipped(monkeypatch):
    _install_fake_urlopen(monkeypatch, {"studies": [
        _study("NCT001"),
        {"protocolSection": {"identificationModule": {"briefTitle": "no id"}}},
        _study("NCT003"),
    ]})
    assert [t["nct_id"] for t in rm.find_trials("x")] == ["NCT001", "NCT003"]


def test_respects_max_results(monkeypatch):
    _install_fake_urlopen(monkeypatch, {"studies": [_study(f"NCT{i:03d}") for i in range(5)]})
    assert len(rm.find_trials("x", max_results=2)) == 2


def test_biomarker_and_recruiting_only_add_query_params(monkeypatch):
    captured = []
    _install_fake_urlopen(monkeypatch, {"studies": []}, captured=captured)
    rm.find_trials("PTSD", biomarker="cortisol", recruiting_only=True)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(captured[0]).query)
    assert q["query.cond"] == ["PTSD"]
    assert q["query.term"] == ["cortisol"]
    assert q["filter.overallStatus"] == ["RECRUITING"]


def test_no_optional_params_when_not_requested(monkeypatch):
    captured = []
    _install_fake_urlopen(monkeypatch, {"studies": []}, captured=captured)
    rm.find_trials("PTSD")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(captured[0]).query)
    assert "query.term" not in q
    assert "filter.overallStatus" not in q


def test_request_failure_returns_empty_list(monkeypatch):
    _install_fake_urlopen(monkeypatch, URLError("down"))
    assert rm.find_trials("x") == []
