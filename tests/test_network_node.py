"""
End-to-end tests for the consolidated NetworkNode: two real nodes on
127.0.0.1 do a real X25519 handshake, gossip an encrypted block, and the
receiver verifies it (strand + helix + shared identity) and awards a token.
Also checks the miner's own local chain grows and stays verifiable, and
that mining is recorded through the real consent gate.
"""
import asyncio
import hashlib
import os

import pytest

from digital_dna import DigitalDNA
from dna_binary_codec import encode_to_dna
from token_ledger import TokenLedger
from network_node import NetworkNode

IDENTITY = encode_to_dna(hashlib.sha256(b"test-network-identity").digest())


def _node(node_id, port, peers, tmp_path, ledger):
    dna = DigitalDNA(seed_label=f"nn-{node_id}", dna_path=os.path.join(str(tmp_path), f"n{node_id}.dna.json"))
    return NetworkNode(node_id, port, peers, dna, IDENTITY, ledger, str(tmp_path))


@pytest.mark.asyncio
async def test_handshake_gossip_verify_award_and_chain(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19551, [19552], tmp_path, ledger)
    n1 = _node(1, 19552, [19551], tmp_path, ledger)

    await n0.start_server()
    await n1.start_server()
    try:
        await n0.mine_and_gossip()          # handshakes with n1, sends one encrypted block
        await asyncio.sleep(0.4)            # let n1 receive + process

        # n1 received and fully verified n0's block, awarding node-0 a token
        assert n1.blocks_received >= 1
        assert ledger.balance("node-0") >= 1

        # n0's own local chain has the block and verifies intact
        ok, msg = n0.chain.verify_chain()
        assert ok and len(n0.chain.blocks) == 1

        # a real per-peer session key was established (not a shared constant)
        assert n0.sessions[19552].handshake_done is True
        assert n0.sessions[19552].session_key is not None and len(n0.sessions[19552].session_key) == 32

        # mining was recorded through the real consent gate as network_mining
        assert len(n0.dna.audit_trail("network_mining")) == 1
    finally:
        n0.server.close(); await n0.server.wait_closed()
        n1.server.close(); await n1.server.wait_closed()


@pytest.mark.asyncio
async def test_mismatched_identity_is_not_awarded(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19553, [19554], tmp_path, ledger)
    n1 = _node(1, 19554, [19553], tmp_path, ledger)
    # give n1 a DIFFERENT frozen identity so n0's blocks fail its identity check
    n1.identity_strand = encode_to_dna(hashlib.sha256(b"a-different-identity").digest())

    await n0.start_server()
    await n1.start_server()
    try:
        await n0.mine_and_gossip()
        await asyncio.sleep(0.4)
        assert n1.blocks_received >= 1           # it arrived and decrypted
        assert ledger.balance("node-0") == 0     # but identity mismatch -> no award
    finally:
        n0.server.close(); await n0.server.wait_closed()
        n1.server.close(); await n1.server.wait_closed()
