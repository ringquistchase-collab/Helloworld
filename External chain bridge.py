#!/usr/bin/env python3
"""
external_chain_bridge.py
==========================
Read-only, informational bridge to OTHER real public token/chain networks —
explicitly NOT a way to move, hold, or commit value between systems.

What this deliberately does NOT do (by design, not by omission):
  - no wallets, no private keys, no addresses of yours are ever handled
  - no transactions are ever built, signed, or broadcast
  - no external "token" value is ever added to, subtracted from, or
    converted into your internal TokenLedger balance
  - no balance of any kind is ever fetched for a specific person/address

What it DOES do, for real:
  - reads real, public, anonymous chain-tip metadata from Bitcoin and
    Ethereum's actual networks (current block height / block number) —
    the same kind of fact anyone can see on a public block explorer,
    with no account or identity attached to the read at all
  - stamps that read with a timestamp and folds it into a signed,
    informational "external_info_snapshot" — a NOTE about what the outside
    world's chains reported, never a claim of ownership or a transfer

This is the honest shape of "connect to other tokens but only communicate,
never commit": one-way, anonymous, public reads in; nothing ever goes out,
and nothing ever crosses into your own ledger's arithmetic.
"""

from __future__ import annotations

import time

import requests

# Bug found during a real multi-node run: 3 nodes reading blockstream.info
# within the same second tripped its rate limit (429) on one node. Fixed
# with a real fallback to a second independent public source (mempool.space
# mirrors the same public chain-tip data) plus a short retry, rather than
# hammering one endpoint or silently returning a stale/fabricated value.
_BITCOIN_TIP_SOURCES = [
    ("https://blockstream.info/api/blocks/tip/height", "blockstream.info"),
    ("https://mempool.space/api/blocks/tip/height", "mempool.space"),
]


def fetch_bitcoin_tip_height() -> dict:
    """Real, public, anonymous read: the current tip height of the real
    Bitcoin blockchain. No address, no wallet, no identity involved.
    Tries a second independent public source if the first is rate-limited
    or unreachable, rather than failing on a single provider's hiccup."""
    last_error = None
    for url, source_name in _BITCOIN_TIP_SOURCES:
        for attempt in range(2):
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                height = int(resp.text.strip())
                return {"chain": "bitcoin", "metric": "tip_height", "value": height, "source": source_name}
            except Exception as e:
                last_error = e
                if attempt == 0:
                    time.sleep(0.5 + 0.5 * attempt)  # brief backoff before retrying same source
    raise RuntimeError(f"all Bitcoin tip-height sources failed: {last_error}")


def fetch_ethereum_block_number() -> dict:
    """Real, public, anonymous read: the current block number of the real
    Ethereum network, via a public JSON-RPC endpoint. No address, no
    wallet, no identity involved."""
    resp = requests.post(
        "https://ethereum.publicnode.com",
        json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    if "result" not in body:
        raise RuntimeError(f"RPC endpoint returned no result: {body}")
    hex_block = body["result"]
    return {"chain": "ethereum", "metric": "block_number", "value": int(hex_block, 16), "source": "ethereum.publicnode.com"}


def build_external_info_snapshot() -> dict:
    """Read-only snapshot of other real networks' public chain-tip info.
    This function has NO parameter and NO code path for a wallet address,
    a private key, a token balance, or a transaction — informational only,
    structurally, not just by convention."""
    snapshot = {"timestamp": time.time(), "reads": []}
    for fetch_fn in (fetch_bitcoin_tip_height, fetch_ethereum_block_number):
        try:
            snapshot["reads"].append(fetch_fn())
        except Exception as e:
            snapshot["reads"].append({"error": str(e), "source": fetch_fn.__name__})
    return snapshot


def _run_self_tests() -> None:
    print("=" * 78)
    print("external_chain_bridge.py — real verification")
    print("=" * 78)

    failures = []

    def check(label, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            failures.append(label)

    import inspect

    # Structural guarantee: no function in this module accepts anything
    # resembling an address, key, wallet, or amount parameter.
    forbidden_param_names = {"address", "wallet", "private_key", "amount", "recipient", "to_address"}
    for name, fn in inspect.getmembers(__import__(__name__), inspect.isfunction):
        params = set(inspect.signature(fn).parameters.keys())
        check(
            f"  {name}() has no wallet/key/amount parameter",
            params.isdisjoint(forbidden_param_names),
        )

    print("\n  Fetching real external chain-tip data (live, public, anonymous)...")
    snapshot = build_external_info_snapshot()
    for read in snapshot["reads"]:
        if "error" in read:
            print(f"    [{read['source']}] FAILED (network issue): {read['error']}")
        else:
            print(f"    [{read['source']}] {read['chain']} {read['metric']}: {read['value']}")

    successful_reads = [r for r in snapshot["reads"] if "error" not in r]
    check("at least one real external chain read succeeded", len(successful_reads) > 0)
    for r in successful_reads:
        check(f"  {r['chain']} value is a real positive integer", isinstance(r["value"], int) and r["value"] > 0)

    # The critical separation guarantee: this module never imports the
    # ledger module at all — checked at the actual Python import-machinery
    # level (sys.modules), not by scanning source text, which is what
    # produces real assurance rather than a string-matching illusion.
    import sys
    check(
        "token_ledger was never imported by this module (informational-only by construction)",
        "token_ledger" not in sys.modules,
    )

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("RESULT: all checks PASSED — real external reads, zero wallets/keys/")
    print("transactions, and structurally isolated from your internal ledger.")
    print("=" * 78)


if __name__ == "__main__":
    _run_self_tests()
