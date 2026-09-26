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
