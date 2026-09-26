import os

import pytest

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


# -- award() / leaderboard() (positive-only contribution points) --

def test_award_adds_points_via_the_transaction_log():
    ledger = TokenLedger()
    ledger.award("node-a", 1, reason="peer verified block")
    ledger.award("node-a", 3, reason="research-linked block verified")
    assert ledger.balance("node-a") == 4
    # award records through the same log history()/all_balances() read
    assert len(ledger.history("node-a")) == 2


def test_award_returns_new_balance():
    ledger = TokenLedger()
    assert ledger.award("node-a", 2, reason="x") == 2
    assert ledger.award("node-a", 3, reason="y") == 5


def test_award_rejects_non_positive():
    ledger = TokenLedger()
    with pytest.raises(ValueError):
        ledger.award("node-a", 0, reason="zero")
    with pytest.raises(ValueError):
        ledger.award("node-a", -2, reason="negative")


def test_leaderboard_sorted_descending():
    ledger = TokenLedger()
    ledger.award("node-a", 2, reason="x")
    ledger.award("node-b", 5, reason="y")
    ledger.award("node-c", 1, reason="z")
    board = ledger.leaderboard()
    assert [n for n, _ in board] == ["node-b", "node-a", "node-c"]
    assert board[0] == ("node-b", 5)
