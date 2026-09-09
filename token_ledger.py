"""
token_ledger.py
================
A real, persisted balance ledger tracking per-node credits earned for
real contributions (mining a block, running a network-analysis pass,
etc.) — not a cryptocurrency, not tradable, not backed by anything.
"Token" here means the same thing DAS/MOS scores mean elsewhere in
this project: a locally-computed number reflecting real activity,
useful for ranking/rewarding participation within THIS node's own
view of the network. No consensus, no other node has to agree with
your balances.

Every balance change is itself an append-only, ordered transaction
record (not just a mutated number) — so `history()` can show exactly
why a balance is what it is, and re-deriving the current balance from
the transaction log always matches what's stored.

Usage
-----
    from token_ledger import TokenLedger

    ledger = TokenLedger(store_path="token_ledger.json", audit=audit)
    ledger.credit("node-a", 1.5, reason="mine_block")
    ledger.balance("node-a")      # 1.5
    ledger.history("node-a")      # [{"amount": 1.5, "reason": "mine_block", ...}]
"""

from __future__ import annotations
import json
import os
import time


class TokenLedger:
    def __init__(self, store_path: str | None = None, audit=None):
        self.store_path = store_path
        self.audit = audit
        self.transactions: list[dict] = []

        if store_path and os.path.exists(store_path):
            with open(store_path, "r", encoding="utf-8") as f:
                self.transactions = json.load(f)

    def _save(self):
        if not self.store_path:
            return
        tmp_path = self.store_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.transactions, f, indent=2)
        os.replace(tmp_path, self.store_path)

    def credit(self, node_id: str, amount: float, reason: str) -> dict:
        """amount may be negative (a debit) -- there's no floor at zero
        enforced here, same as a real ledger: a negative balance is a
        fact to look at, not an error to hide."""
        tx = {"timestamp": time.time(), "node_id": node_id, "amount": amount, "reason": reason}
        self.transactions.append(tx)
        self._save()

        if self.audit is not None:
            self.audit.log(
                module="token_ledger", action="credit", node_id=node_id,
                details={"amount": amount, "reason": reason, "new_balance": self.balance(node_id)},
            )
        return tx

    def balance(self, node_id: str) -> float:
        return round(sum(tx["amount"] for tx in self.transactions if tx["node_id"] == node_id), 6)

    def history(self, node_id: str | None = None) -> list[dict]:
        if node_id is None:
            return list(self.transactions)
        return [tx for tx in self.transactions if tx["node_id"] == node_id]

    def all_balances(self) -> dict[str, float]:
        node_ids = {tx["node_id"] for tx in self.transactions}
        return {nid: self.balance(nid) for nid in node_ids}


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.json")
        ledger = TokenLedger(store_path=path)

        ledger.credit("node-a", 1.0, reason="mine_block")
        ledger.credit("node-a", 0.5, reason="network_analysis")
        ledger.credit("node-b", 2.0, reason="mine_block")

        print(f"node-a balance: {ledger.balance('node-a')} (expected 1.5)")
        print(f"node-b balance: {ledger.balance('node-b')} (expected 2.0)")
        print(f"node-a history: {ledger.history('node-a')}")
        print(f"all balances: {ledger.all_balances()}")

        print("\n=== Reload from disk: balances survive a restart ===")
        ledger2 = TokenLedger(store_path=path)
        print(f"node-a balance after reload: {ledger2.balance('node-a')} (expected 1.5)")

        print("\n=== Debit (negative credit) ===")
        ledger2.credit("node-a", -0.25, reason="correction")
        print(f"node-a balance after debit: {ledger2.balance('node-a')} (expected 1.25)")
