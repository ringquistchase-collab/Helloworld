import os

from maxwell_chain_agent import MaxwellChainAgent


def test_mine_block_meets_difficulty(tmp_path):
    agent = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=2)
    block = agent.mine_block("research_event", {"topic": "test"})
    assert block["hash"].startswith("00")


def test_chain_linkage(tmp_path):
    agent = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=1)
    b0 = agent.mine_block("research_event", {"n": 0})
    b1 = agent.mine_block("research_event", {"n": 1})
    assert b1["previous_hash"] == b0["hash"]


def test_validate_chain_true_when_untouched():
    agent = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=1)
    agent.mine_block("research_event", {"n": 0})
    agent.mine_block("research_event", {"n": 1})
    assert agent.validate_chain() is True


def test_validate_chain_catches_tampered_genesis_block():
    """Regression test: an earlier draft of validate_chain() looped from
    index 1, so block 0's own hash was never recomputed and checked --
    tampering the genesis block's data went undetected. This must catch it."""
    agent = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=1)
    agent.mine_block("research_event", {"n": 0})
    agent.mine_block("research_event", {"n": 1})

    agent.chain[0]["data"] = {"tampered": True}
    assert agent.validate_chain() is False


def test_validate_chain_catches_tampered_middle_block():
    agent = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=1)
    agent.mine_block("research_event", {"n": 0})
    agent.mine_block("research_event", {"n": 1})
    agent.mine_block("research_event", {"n": 2})

    agent.chain[1]["data"] = {"tampered": True}
    assert agent.validate_chain() is False


def test_maxwell_signature_optional():
    agent = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=1)
    plain = agent.mine_block("research_event", {"n": 0})
    signed = agent.mine_block("field_sample", {"n": 1}, with_maxwell_signature=True)
    assert "maxwell_signature" not in plain
    assert "maxwell_signature" in signed


def test_persistence_across_reload(tmp_path):
    store_path = os.path.join(str(tmp_path), "chain.json")
    agent = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=1, store_path=store_path)
    agent.mine_block("research_event", {"n": 0})

    agent2 = MaxwellChainAgent(canonical_chain=[], miner_id="test", difficulty=1, store_path=store_path)
    assert len(agent2.chain) == 1
    assert agent2.validate_chain() is True
