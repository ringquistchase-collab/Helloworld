"""
Offline regression tests for maxwell_research.search_arxiv.

arXiv returns Atom XML (not JSON), and this fetcher is also the one
that signals failure differently from the others -- it returns
[{"error": ...}] rather than []. Both behaviors are pinned here by
mocking urllib.request.urlopen; the real XML parsing runs offline.
"""
from urllib.error import URLError

import pytest

import maxwell_research as mr


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_urlopen(monkeypatch, payload):
    def fake(url_or_req, *args, **kwargs):
        if isinstance(payload, Exception):
            raise payload
        return _FakeResp(payload)

    monkeypatch.setattr(mr.urllib.request, "urlopen", fake)


_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>  Spanning
two lines  </title>
    <id>http://arxiv.org/abs/1234.5678v1</id>
    <updated>2024-01-02T00:00:00Z</updated>
  </entry>
  <entry>
    <title>Second paper</title>
    <id>http://arxiv.org/abs/2222.0001v2</id>
    <updated>2023-11-15T00:00:00Z</updated>
  </entry>
</feed>"""


def test_parses_entries_and_normalizes_title_whitespace(monkeypatch):
    _install_fake_urlopen(monkeypatch, _FEED)
    papers = mr.search_arxiv("Maxwell equations", max_results=5)
    assert papers == [
        {"title": "Spanning two lines", "url": "http://arxiv.org/abs/1234.5678v1",
         "updated": "2024-01-02T00:00:00Z"},
        {"title": "Second paper", "url": "http://arxiv.org/abs/2222.0001v2",
         "updated": "2023-11-15T00:00:00Z"},
    ]


def test_missing_title_element_becomes_none(monkeypatch):
    feed = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/9999.0000</id>
        <updated>2024-05-05T00:00:00Z</updated>
      </entry>
    </feed>"""
    _install_fake_urlopen(monkeypatch, feed)
    papers = mr.search_arxiv("x")
    assert papers == [
        {"title": None, "url": "http://arxiv.org/abs/9999.0000", "updated": "2024-05-05T00:00:00Z"},
    ]


def test_empty_feed_returns_empty_list(monkeypatch):
    _install_fake_urlopen(monkeypatch, b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    assert mr.search_arxiv("x") == []


def test_request_failure_returns_error_dict(monkeypatch):
    # Unlike the other fetchers (which return []), search_arxiv reports
    # failure as a single-element list carrying an "error" key.
    _install_fake_urlopen(monkeypatch, URLError("network down"))
    result = mr.search_arxiv("x")
    assert len(result) == 1 and "error" in result[0]


def test_malformed_xml_is_caught_as_error_dict(monkeypatch):
    _install_fake_urlopen(monkeypatch, b"not xml at all <<<")
    result = mr.search_arxiv("x")
    assert len(result) == 1 and "error" in result[0]
