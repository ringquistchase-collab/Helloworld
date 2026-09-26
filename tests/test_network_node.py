"""
End-to-end tests for the consolidated NetworkNode: two real nodes on
127.0.0.1 do a real X25519 handshake, gossip an encrypted block, and the
receiver verifies it (strand + helix + shared identity) and awards a token.
Also checks the miner's own local chain grows and stays verifiable, and
that mining is recorded through the real consent gate.
"""
import asyncio
import hashlib
import json
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


def _signed_block(node, index=1):
    from crypto_layer import sign
    from dna_binary_codec import complement_strand
    from network_node import block_signing_bytes
    digest = hashlib.sha256(f"block-{index}".encode()).digest()
    strand = encode_to_dna(digest)
    block = {
        "origin": node.node_id,
        "index": index,
        "hash_hex": digest.hex(),
        "strand": strand,
        "complement": complement_strand(strand),
        "identity_strand": IDENTITY,
    }
    block["sig"] = sign(node.signing_priv, block_signing_bytes(block)).hex()
    return block


def test_signed_block_is_awarded_and_tampered_block_is_not(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19555, [19556], tmp_path, ledger)
    n1 = _node(1, 19556, [19555], tmp_path, ledger)
    n1.trust_peer(0, n0.signing_pub_hex)

    good = _signed_block(n0)
    n1._verify_and_process(good)
    assert ledger.balance("node-0") == 1

    tampered = dict(good, index=99)          # any field change breaks the signature
    n1._verify_and_process(tampered)
    unsigned = {k: v for k, v in good.items() if k != "sig"}
    n1._verify_and_process(unsigned)
    assert ledger.balance("node-0") == 1


def test_block_signed_by_wrong_key_is_not_awarded(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19557, [19558], tmp_path, ledger)
    n1 = _node(1, 19558, [19557], tmp_path, ledger)
    impostor = _node(0, 19559, [19558], tmp_path, ledger)   # claims node_id 0, own key
    n1.trust_peer(0, n0.signing_pub_hex)

    n1._verify_and_process(_signed_block(impostor))
    assert ledger.balance("node-0") == 0


@pytest.mark.asyncio
async def test_impostor_handshake_with_changed_key_is_rejected(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19560, [19561], tmp_path, ledger)
    n1 = _node(1, 19561, [19560], tmp_path, ledger)
    impostor = _node(0, 19562, [19561], tmp_path, ledger)
    n1.trust_peer(0, n0.signing_pub_hex)

    await n1.start_server()
    try:
        await impostor.mine_and_gossip()
        await asyncio.sleep(0.4)
        assert impostor.sessions[19561].handshake_done is False
        assert n1.blocks_received == 0
        assert ledger.balance("node-0") == 0
    finally:
        n1.server.close(); await n1.server.wait_closed()


@pytest.mark.asyncio
async def test_require_known_peers_rejects_unregistered_peer(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19563, [19564], tmp_path, ledger)
    n1 = _node(1, 19564, [19563], tmp_path, ledger)
    n1.require_known_peers = True

    await n1.start_server()
    try:
        await n0.mine_and_gossip()
        await asyncio.sleep(0.4)
        assert n1.blocks_received == 0
        assert ledger.balance("node-0") == 0

        n1.trust_peer(0, n0.signing_pub_hex)   # register it, and it goes through
        await n0.mine_and_gossip()
        await asyncio.sleep(0.4)
        assert ledger.balance("node-0") == 1
    finally:
        n1.server.close(); await n1.server.wait_closed()


def test_replayed_block_is_not_awarded_twice(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19565, [19566], tmp_path, ledger)
    n1 = _node(1, 19566, [19565], tmp_path, ledger)
    n1.trust_peer(0, n0.signing_pub_hex)

    block = _signed_block(n0, index=1)
    n1._verify_and_process(block)
    n1._verify_and_process(dict(block))          # exact replay
    assert ledger.balance("node-0") == 1

    n1._verify_and_process(_signed_block(n0, index=2))   # a new index still counts
    assert ledger.balance("node-0") == 2


def test_forged_copy_does_not_block_the_real_block(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19567, [19568], tmp_path, ledger)
    n1 = _node(1, 19568, [19567], tmp_path, ledger)
    impostor = _node(0, 19569, [19568], tmp_path, ledger)
    n1.trust_peer(0, n0.signing_pub_hex)

    n1._verify_and_process(_signed_block(impostor, index=1))   # fails signature, not recorded
    n1._verify_and_process(_signed_block(n0, index=1))
    assert ledger.balance("node-0") == 1


@pytest.mark.asyncio
async def test_replay_over_the_wire_is_not_awarded(tmp_path):
    from crypto_layer import aead_encrypt
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19570, [19571], tmp_path, ledger)
    n1 = _node(1, 19571, [19570], tmp_path, ledger)

    await n0.start_server()
    await n1.start_server()
    try:
        await n0.mine_and_gossip()
        await asyncio.sleep(0.4)
        assert ledger.balance("node-0") == 1

        # resend the exact block n0 already gossiped, over the real session
        block = n0.chain.blocks[-1].payload
        session = n0.sessions[19571]
        frame = aead_encrypt(session.session_key, json.dumps(block).encode(), aad=b"19570->19571")
        payload = (19570).to_bytes(4, "big") + frame
        reader, writer = await asyncio.open_connection("127.0.0.1", 19571)
        writer.write(b"B" + len(payload).to_bytes(4, "big") + payload)
        await writer.drain()
        writer.close(); await writer.wait_closed()
        await asyncio.sleep(0.4)

        assert n1.blocks_received == 2
        assert ledger.balance("node-0") == 1
    finally:
        n0.server.close(); await n0.server.wait_closed()
        n1.server.close(); await n1.server.wait_closed()
