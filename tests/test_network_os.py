import asyncio
import hashlib
import json
import os

import pytest

import crypto_layer as ck
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


def _build_signed_hello(node_id: str, listen_port: int) -> dict:
    """Crafts a real, validly-signed hello for an arbitrary node_id --
    same technique the interop clients use, useful here for simulating
    the same peer reconnecting with a different self-reported listen_port."""
    signing_priv, signing_pub = ck.generate_signing_keypair()
    _, exchange_pub = ck.generate_exchange_keypair()
    strand_hex = "0" * 64
    exchange_hex = ck.exchange_pub_to_hex(exchange_pub)
    to_sign = f"{node_id}|{strand_hex}|{exchange_hex}".encode()
    return {
        "type": "hello", "node_id": node_id, "listen_port": listen_port,
        "strand_hex": strand_hex, "signing_pub": ck.signing_pub_to_hex(signing_pub),
        "exchange_pub": exchange_hex, "sig": ck.sign(signing_priv, to_sign).hex(),
    }


@pytest.mark.asyncio
async def test_peer_reconnection_from_new_address_is_tracked(tmp_path):
    """A peer reconnecting with the same node_id but a different
    self-reported listen_port should grow address_history and log
    peer_reconnected_new_address, not just silently overwrite the old
    entry."""
    dna_a = DigitalDNA(seed_label="test-reconnect-a", dna_path=os.path.join(str(tmp_path), "a4.dna.json"))
    node_a = NetworkNode(dna_a, host="127.0.0.1", port=18806)

    class FakeAudit:
        def __init__(self):
            self.entries = []

        def log(self, **kwargs):
            self.entries.append(kwargs)

    node_a.audit = FakeAudit()

    try:
        await node_a.start()

        fake_pid = "reconnect-test-node"
        hello_1 = _build_signed_hello(fake_pid, listen_port=9001)
        _, writer_1 = await asyncio.open_connection("127.0.0.1", 18806)
        writer_1.write((json.dumps(hello_1) + "\n").encode())
        await writer_1.drain()
        await asyncio.sleep(0.3)

        assert fake_pid in node_a.peers
        assert len(node_a.peers[fake_pid].address_history) == 1
        assert node_a.peers[fake_pid].address_history[0][1] == 9001
        writer_1.close()

        hello_2 = _build_signed_hello(fake_pid, listen_port=9002)
        _, writer_2 = await asyncio.open_connection("127.0.0.1", 18806)
        writer_2.write((json.dumps(hello_2) + "\n").encode())
        await writer_2.drain()
        await asyncio.sleep(0.3)

        assert len(node_a.peers[fake_pid].address_history) == 2
        assert node_a.peers[fake_pid].address_history[1][1] == 9002
        assert any(e["action"] == "peer_reconnected_new_address" for e in node_a.audit.entries)
        writer_2.close()
    finally:
        await node_a.stop()


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
