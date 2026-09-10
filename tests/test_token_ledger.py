import os

from token_ledger import TokenLedger


def test_credit_and_balance():
    ledger = TokenLedger()
    ledger.credit("node-a", 1.0, reason="mine_block")
    ledger.credit("node-a", 0.5, reason="analysis")
    assert ledger.balance("node-a") == 1.5


def test_balances_are_per_node():
    ledger = TokenLedger()
    ledger.credit("node-a", 1.0, reason="mine_block")
    ledger.credit("node-b", 2.0, reason="mine_block")
    assert ledger.balance("node-a") == 1.0
    assert ledger.balance("node-b") == 2.0


def test_negative_credit_is_a_debit():
    ledger = TokenLedger()
    ledger.credit("node-a", 1.0, reason="mine_block")
    ledger.credit("node-a", -0.25, reason="correction")
    assert ledger.balance("node-a") == 0.75


def test_history_returns_only_that_node():
    ledger = TokenLedger()
    ledger.credit("node-a", 1.0, reason="mine_block")
    ledger.credit("node-b", 2.0, reason="mine_block")
    history = ledger.history("node-a")
    assert len(history) == 1
    assert history[0]["node_id"] == "node-a"


def test_persistence_across_reload(tmp_path):
    path = os.path.join(str(tmp_path), "ledger.json")
    TokenLedger(store_path=path).credit("node-a", 1.5, reason="mine_block")
    reloaded = TokenLedger(store_path=path)
    assert reloaded.balance("node-a") == 1.5
