"""
Offline tests for external_chain_bridge (requests mocked, no network):
  - Bitcoin tip-height and Ethereum block-number parsing
  - snapshot shape + graceful per-read error capture
  - structural guarantees: no wallet/key/amount parameters anywhere, and
    the module never pulls in the internal token_ledger (informational-only
    by construction).
"""
import inspect

import pytest

import external_chain_bridge as mod


class _Resp:
    def __init__(self, text=None, json_data=None):
        self._text = text
        self._json = json_data

    @property
    def text(self):
        return self._text

    def json(self):
        return self._json

    def raise_for_status(self):
        return None


def test_fetch_bitcoin_parses_height(monkeypatch):
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp(text="  840123\n"))
    assert mod.fetch_bitcoin_tip_height() == {
        "chain": "bitcoin", "metric": "tip_height", "value": 840123, "source": "blockstream.info",
    }


def test_fetch_ethereum_parses_hex_block(monkeypatch):
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: _Resp(json_data={"jsonrpc": "2.0", "id": 1, "result": "0x10"}))
    r = mod.fetch_ethereum_block_number()
    assert r["chain"] == "ethereum" and r["metric"] == "block_number" and r["value"] == 16


def test_ethereum_missing_result_raises(monkeypatch):
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(json_data={"error": "nope"}))
    with pytest.raises(RuntimeError):
        mod.fetch_ethereum_block_number()


def test_snapshot_shape_with_both_reads(monkeypatch):
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp(text="900000"))
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(json_data={"result": "0x1"}))
    snap = mod.build_external_info_snapshot()
    assert "timestamp" in snap and len(snap["reads"]) == 2
    assert all("error" not in r for r in snap["reads"])
    assert {r["chain"] for r in snap["reads"]} == {"bitcoin", "ethereum"}


def test_snapshot_records_errors_without_crashing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(mod.requests, "get", boom)
    monkeypatch.setattr(mod.requests, "post", boom)
    snap = mod.build_external_info_snapshot()
    assert len(snap["reads"]) == 2
    assert all("error" in r for r in snap["reads"])


def test_no_function_exposes_wallet_key_or_amount_params():
    forbidden = {"address", "wallet", "private_key", "amount", "recipient", "to_address"}
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        params = set(inspect.signature(fn).parameters)
        assert params.isdisjoint(forbidden), f"{name} exposes a forbidden parameter"


def test_module_is_isolated_from_the_internal_ledger():
    # informational-only by construction: it never imports or references the ledger
    assert not hasattr(mod, "TokenLedger")
    assert not hasattr(mod, "token_ledger")


class _HTTPErrorResp(_Resp):
    def __init__(self, status):
        super().__init__(text="")
        self.status_code = status

    def raise_for_status(self):
        raise mod.requests.HTTPError(f"{self.status_code}", response=self)


def _scripted_get(script, calls):
    """requests.get stand-in: script maps source host -> list of responses
    or exceptions, consumed in order; every call's host is logged."""
    def get(url, **kwargs):
        host = "blockstream.info" if "blockstream" in url else "mempool.space"
        calls.append(host)
        item = script[host].pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return get


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def test_rate_limited_source_is_retried_once_then_falls_back(monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(mod.requests, "get", _scripted_get({
        "blockstream.info": [_HTTPErrorResp(429), _HTTPErrorResp(429)],
        "mempool.space": [_Resp(text="850000")],
    }, calls))
    r = mod.fetch_bitcoin_tip_height()
    assert r["source"] == "mempool.space" and r["value"] == 850000
    assert calls == ["blockstream.info", "blockstream.info", "mempool.space"]


def test_transient_failure_recovers_on_retry(monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(mod.requests, "get", _scripted_get({
        "blockstream.info": [mod.requests.ConnectionError("reset"), _Resp(text="850001")],
        "mempool.space": [],
    }, calls))
    assert mod.fetch_bitcoin_tip_height()["source"] == "blockstream.info"
    assert calls == ["blockstream.info", "blockstream.info"]


@pytest.mark.parametrize("bad", [_HTTPErrorResp(404), _Resp(text="<html>not a number</html>")])
def test_non_retryable_failure_skips_straight_to_next_source(monkeypatch, no_sleep, bad):
    calls = []
    monkeypatch.setattr(mod.requests, "get", _scripted_get({
        "blockstream.info": [bad],
        "mempool.space": [_Resp(text="850002")],
    }, calls))
    assert mod.fetch_bitcoin_tip_height()["source"] == "mempool.space"
    assert calls == ["blockstream.info", "mempool.space"]


def test_all_sources_failing_raises(monkeypatch, no_sleep):
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _HTTPErrorResp(503))
    with pytest.raises(RuntimeError):
        mod.fetch_bitcoin_tip_height()


def test_requests_use_the_short_timeout(monkeypatch):
    seen = []
    monkeypatch.setattr(mod.requests, "get", lambda url, **k: seen.append(k["timeout"]) or _Resp(text="1"))
    mod.fetch_bitcoin_tip_height()
    assert seen == [mod.REQUEST_TIMEOUT_SECONDS]


def test_snapshot_respects_its_time_budget(monkeypatch):
    import time as real_time
    monkeypatch.setattr(mod, "SNAPSHOT_BUDGET_SECONDS", 0.5)

    def hang(*a, **k):
        real_time.sleep(k["timeout"])      # behave like a server that never answers
        raise mod.requests.Timeout("timed out")

    monkeypatch.setattr(mod.requests, "get", hang)
    monkeypatch.setattr(mod.requests, "post", hang)
    start = real_time.monotonic()
    snap = mod.build_external_info_snapshot()
    assert real_time.monotonic() - start < 2.0
    assert len(snap["reads"]) == 2 and all("error" in r for r in snap["reads"])
