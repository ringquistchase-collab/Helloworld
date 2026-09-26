"""
Tests for chain_store — the single-node append-only, tamper-evident chain.
Pins genesis linkage, previous_hash chaining, whole-chain verification,
persistence across reload, and detection of both payload tampering and a
broken link.
"""
import os

from chain_store import ChainStore, ChainBlock, GENESIS_PREV_HASH


def test_genesis_tip_is_sentinel(tmp_path):
    chain = ChainStore(store_path=os.path.join(str(tmp_path), "c.json"))
    assert chain.tip_hash() == GENESIS_PREV_HASH


def test_blocks_link_to_previous_hash(tmp_path):
    chain = ChainStore(store_path=os.path.join(str(tmp_path), "c.json"))
    b1 = chain.append({"note": "one"})
    b2 = chain.append({"note": "two"})
    assert b1.previous_hash == GENESIS_PREV_HASH
    assert b2.previous_hash == b1.block_hash
    assert chain.tip_hash() == b2.block_hash
    assert b1.index == 0 and b2.index == 1


def test_verify_chain_ok_when_intact(tmp_path):
    chain = ChainStore(store_path=os.path.join(str(tmp_path), "c.json"))
    for i in range(4):
        chain.append({"n": i})
    ok, msg = chain.verify_chain()
    assert ok is True
    assert "4 blocks verified" in msg


def test_persistence_across_reload(tmp_path):
    path = os.path.join(str(tmp_path), "c.json")
    chain = ChainStore(store_path=path)
    chain.append({"a": 1})
    chain.append({"b": 2})

    reloaded = ChainStore(store_path=path)
    assert len(reloaded.blocks) == 2
    ok, _ = reloaded.verify_chain()
    assert ok is True
    # reloaded blocks preserve their real hashes/links
    assert reloaded.blocks[1].previous_hash == reloaded.blocks[0].block_hash


def test_payload_tampering_is_detected(tmp_path):
    chain = ChainStore(store_path=os.path.join(str(tmp_path), "c.json"))
    chain.append({"note": "real"})
    chain.append({"note": "also real"})
    chain.blocks[0].payload["note"] = "TAMPERED"
    ok, msg = chain.verify_chain()
    assert ok is False
    assert "tampered" in msg.lower()


def test_broken_link_is_detected(tmp_path):
    chain = ChainStore(store_path=os.path.join(str(tmp_path), "c.json"))
    chain.append({"n": 0})
    chain.append({"n": 1})
    # break the linkage without touching payload/hash
    chain.blocks[1].previous_hash = "f" * 64
    ok, msg = chain.verify_chain()
    assert ok is False
    assert "previous_hash mismatch" in msg


def test_empty_chain_verifies_trivially(tmp_path):
    chain = ChainStore(store_path=os.path.join(str(tmp_path), "c.json"))
    ok, msg = chain.verify_chain()
    assert ok is True
    assert "0 blocks verified" in msg
