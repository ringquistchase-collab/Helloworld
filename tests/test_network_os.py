import asyncio
import hashlib
import os

import pytest

from digital_dna import DigitalDNA
from network_os import NetworkNode


@pytest.mark.asyncio
async def test_two_nodes_handshake_and_gossip(tmp_path):
    dna_a = DigitalDNA(seed_label="test-node-a", dna_path=os.path.join(str(tmp_path), "a.dna.json"))
    dna_b = DigitalDNA(seed_label="test-node-b", dna_path=os.path.join(str(tmp_path), "b.dna.json"))

    node_a = NetworkNode(dna_a, host="127.0.0.1", port=18801)
    node_b = NetworkNode(dna_b, host="127.0.0.1", port=18802)

    try:
        await node_a.start()
        await node_b.start()
        await node_b.connect_peer("127.0.0.1", 18801)
        await asyncio.sleep(0.5)

        assert node_a.node_id in node_b.peers
        assert node_b.node_id in node_a.peers

        feature_hash = hashlib.sha256(b"test-event").hexdigest()
        await node_a.mine_and_broadcast("manual_entry", feature_hash, confidence=0.9, consent_verified=True)
        await asyncio.sleep(0.5)

        ledger_entries = node_b.network_ledger.get(node_a.node_id, [])
        assert len(ledger_entries) == 1
        assert ledger_entries[0]["source"] == "manual_entry"
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_topic_gossip(tmp_path):
    dna_a = DigitalDNA(seed_label="test-topic-a", dna_path=os.path.join(str(tmp_path), "a2.dna.json"))
    dna_b = DigitalDNA(seed_label="test-topic-b", dna_path=os.path.join(str(tmp_path), "b2.dna.json"))

    node_a = NetworkNode(dna_a, host="127.0.0.1", port=18803)
    node_b = NetworkNode(dna_b, host="127.0.0.1", port=18804)

    received = []
    node_b.on_topic(lambda t: received.append(t))

    try:
        await node_a.start()
        await node_b.start()
        await node_b.connect_peer("127.0.0.1", 18803)
        await asyncio.sleep(0.5)

        await node_a.broadcast_topic({"condition": "test", "biomarker": "X"})
        await asyncio.sleep(0.5)

        assert len(received) == 1
        assert received[0]["condition"] == "test"
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_forged_signature_is_rejected(tmp_path):
    """A hello with a node_id that doesn't match the signature must never
    become a trusted peer."""
    dna_a = DigitalDNA(seed_label="test-forge-a", dna_path=os.path.join(str(tmp_path), "a3.dna.json"))
    node_a = NetworkNode(dna_a, host="127.0.0.1", port=18805)

    try:
        await node_a.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", 18805)

        import json
        forged_hello = {
            "type": "hello", "node_id": "not-really-my-id", "listen_port": 0,
            "strand_hex": "0" * 64, "signing_pub": "0" * 64, "exchange_pub": "0" * 64,
            "sig": "0" * 128,
        }
        writer.write((json.dumps(forged_hello) + "\n").encode())
        await writer.drain()
        await asyncio.sleep(0.3)

        assert "not-really-my-id" not in node_a.peers
        writer.close()
    finally:
        await node_a.stop()
