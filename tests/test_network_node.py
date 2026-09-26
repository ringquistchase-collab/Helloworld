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
        out = n0.outbound[("127.0.0.1", 19552)]
        assert out.handshake_done is True and out.peer_node_id == 1
        assert out.session_key is not None and len(out.session_key) == 32
        assert n1.inbound[0].session_key == out.session_key

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
        assert ("127.0.0.1", 19561) not in impostor.outbound
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
        addr = ("127.0.0.1", 19571)
        assert await n0._send_block(addr, n0.outbound[addr], block) == b"k"
        await asyncio.sleep(0.2)

        assert n1.blocks_received == 2
        assert ledger.balance("node-0") == 1
    finally:
        n0.server.close(); await n0.server.wait_closed()
        n1.server.close(); await n1.server.wait_closed()


def test_signing_key_path_keeps_identity_across_restarts(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    key_path = os.path.join(str(tmp_path), "keys", "node-0.ed25519.pem")
    first = NetworkNode(0, 19572, [], _node(9, 19599, [], tmp_path, ledger).dna, IDENTITY, ledger,
                        str(tmp_path), signing_key_path=key_path)
    second = NetworkNode(0, 19572, [], first.dna, IDENTITY, ledger, str(tmp_path), signing_key_path=key_path)
    assert first.signing_pub_hex == second.signing_pub_hex
    assert first.boot_id != second.boot_id


def test_restarted_node_restarting_its_index_is_not_a_replay(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    key_path = os.path.join(str(tmp_path), "keys", "node-0.ed25519.pem")
    dna = _node(9, 19599, [], tmp_path, ledger).dna
    n1 = _node(1, 19573, [], tmp_path, ledger)

    before = NetworkNode(0, 19574, [], dna, IDENTITY, ledger, str(tmp_path), signing_key_path=key_path)
    n1.trust_peer(0, before.signing_pub_hex)
    old_block = dict(_signed_block(before, index=1), boot_id=before.boot_id)
    from crypto_layer import sign
    from network_node import block_signing_bytes
    old_block["sig"] = sign(before.signing_priv, block_signing_bytes(old_block)).hex()
    n1._verify_and_process(old_block)

    after = NetworkNode(0, 19574, [], dna, IDENTITY, ledger, str(tmp_path), signing_key_path=key_path)
    new_block = dict(_signed_block(after, index=1), boot_id=after.boot_id)
    new_block["sig"] = sign(after.signing_priv, block_signing_bytes(new_block)).hex()
    n1._verify_and_process(new_block)       # same index, new boot -> accepted
    n1._verify_and_process(dict(old_block))  # old boot's block again -> replay
    assert ledger.balance("node-0") == 2


@pytest.mark.asyncio
async def test_block_relayed_by_another_peer_is_dropped(tmp_path):
    from crypto_layer import aead_encrypt
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19575, [19576], tmp_path, ledger)
    n1 = _node(1, 19576, [19575, 19577], tmp_path, ledger)
    n2 = _node(2, 19577, [19576], tmp_path, ledger)

    await n0.start_server(); await n1.start_server(); await n2.start_server()
    try:
        await n0.mine_and_gossip()
        await asyncio.sleep(0.3)
        assert ledger.balance("node-0") == 1

        # n2 (a legitimately handshaken peer) relays a validly signed n0
        # block that n1 has NOT seen yet, so only the origin/session check
        # can stop it (the replay set alone wouldn't)
        from crypto_layer import sign
        from network_node import block_signing_bytes
        addr = ("127.0.0.1", 19576)
        session = await n2._ensure_handshake(addr)
        assert session is not None
        block = dict(_signed_block(n0, index=2), boot_id=n0.boot_id)
        block["sig"] = sign(n0.signing_priv, block_signing_bytes(block)).hex()
        await n2._send_block(addr, session, block)
        await asyncio.sleep(0.2)

        assert n1.blocks_received == 2
        assert ledger.balance("node-0") == 1
    finally:
        for n in (n0, n1, n2):
            n.server.close(); await n.server.wait_closed()


def test_pins_are_saved_and_reloaded(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    peers_path = os.path.join(str(tmp_path), "keys", "node-1.known_peers.json")
    n0 = _node(0, 19578, [], tmp_path, ledger)
    dna = n0.dna

    first = NetworkNode(1, 19579, [], dna, IDENTITY, ledger, str(tmp_path), known_peers_path=peers_path)
    first.trust_peer(0, n0.signing_pub_hex)
    second = NetworkNode(1, 19579, [], dna, IDENTITY, ledger, str(tmp_path), known_peers_path=peers_path)
    assert second.peer_signing_keys == {0: n0.signing_pub_hex}

    # the reloaded pin is enforced: a block signed by a different node-0 key fails
    impostor = _node(0, 19580, [], tmp_path, ledger)
    second._verify_and_process(_signed_block(impostor))
    assert ledger.balance("node-0") == 0
    second._verify_and_process(_signed_block(n0))
    assert ledger.balance("node-0") == 1


def test_trust_peer_refuses_a_changed_key_unless_replace(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    peers_path = os.path.join(str(tmp_path), "node-1.known_peers.json")
    n0 = _node(0, 19581, [], tmp_path, ledger)
    new_n0 = _node(0, 19582, [], tmp_path, ledger)

    n1 = NetworkNode(1, 19583, [], n0.dna, IDENTITY, ledger, str(tmp_path), known_peers_path=peers_path)
    n1.trust_peer(0, n0.signing_pub_hex)
    n1.trust_peer(0, n0.signing_pub_hex)                     # same key again is fine
    with pytest.raises(ValueError):
        n1.trust_peer(0, new_n0.signing_pub_hex)
    assert n1.peer_signing_keys[0] == n0.signing_pub_hex

    n1.trust_peer(0, new_n0.signing_pub_hex, replace=True)
    reloaded = NetworkNode(1, 19583, [], n0.dna, IDENTITY, ledger, str(tmp_path), known_peers_path=peers_path)
    assert reloaded.peer_signing_keys[0] == new_n0.signing_pub_hex


@pytest.mark.asyncio
async def test_trust_on_first_use_pin_is_saved(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    peers_path = os.path.join(str(tmp_path), "node-1.known_peers.json")
    n0 = _node(0, 19584, [19585], tmp_path, ledger)
    n1 = NetworkNode(1, 19585, [19584], n0.dna, IDENTITY, ledger, str(tmp_path), known_peers_path=peers_path)

    await n1.start_server()
    try:
        await n0.mine_and_gossip()
        await asyncio.sleep(0.3)
    finally:
        n1.server.close(); await n1.server.wait_closed()
    with open(peers_path, encoding="utf-8") as f:
        assert json.load(f) == {"0": n0.signing_pub_hex}


def test_corrupt_known_peers_file_raises_and_is_kept(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    peers_path = tmp_path / "node-1.known_peers.json"
    dna = _node(0, 19586, [], tmp_path, ledger).dna
    for bad in ('{"0": "not-hex"}', "[1, 2]", "{not json"):
        peers_path.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            NetworkNode(1, 19587, [], dna, IDENTITY, ledger, str(tmp_path), known_peers_path=str(peers_path))
        assert peers_path.read_text(encoding="utf-8") == bad


@pytest.mark.asyncio
async def test_nodes_on_the_same_port_different_hosts(tmp_path):
    # Two "machines" both listening on the same port: 127.0.0.1 and
    # 127.0.0.2 are distinct loopback addresses on Windows and Linux.
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    dna = _node(9, 19599, [], tmp_path, ledger).dna
    (tmp_path / "b").mkdir()
    a = NetworkNode(0, 19590, None, dna, IDENTITY, ledger, str(tmp_path),
                    peers=[("127.0.0.2", 19590)], bind_host="127.0.0.1")
    b = NetworkNode(1, 19590, None, dna, IDENTITY, ledger, str(tmp_path / "b"),
                    peers=[("127.0.0.1", 19590)], bind_host="127.0.0.2")
    try:
        await b.start_server()
    except OSError:
        pytest.skip("127.0.0.2 loopback not available on this machine")
    await a.start_server()
    try:
        await a.mine_and_gossip()
        await b.mine_and_gossip()
        await asyncio.sleep(0.3)
        assert ledger.balance("node-0") == 1 and ledger.balance("node-1") == 1
    finally:
        a.server.close(); await a.server.wait_closed()
        b.server.close(); await b.server.wait_closed()


@pytest.mark.asyncio
async def test_simultaneous_handshakes_leave_both_directions_working(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n0 = _node(0, 19591, [19592], tmp_path, ledger)
    n1 = _node(1, 19592, [19591], tmp_path, ledger)
    await n0.start_server(); await n1.start_server()
    try:
        await asyncio.gather(n0.mine_and_gossip(), n1.mine_and_gossip())
        await asyncio.sleep(0.3)
        await asyncio.gather(n0.mine_and_gossip(), n1.mine_and_gossip())
        await asyncio.sleep(0.3)
        assert ledger.balance("node-0") == 2 and ledger.balance("node-1") == 2
    finally:
        n0.server.close(); await n0.server.wait_closed()
        n1.server.close(); await n1.server.wait_closed()


@pytest.mark.asyncio
async def test_sender_rehandshakes_after_receiver_restart(tmp_path):
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    key = os.path.join(str(tmp_path), "keys", "node-1.ed25519.pem")
    n0 = _node(0, 19593, [19594], tmp_path, ledger)
    n1 = NetworkNode(1, 19594, [19593], n0.dna, IDENTITY, ledger, str(tmp_path), signing_key_path=key)
    await n1.start_server()
    await n0.mine_and_gossip()
    await asyncio.sleep(0.2)
    assert ledger.balance("node-0") == 1
    n1.server.close(); await n1.server.wait_closed()

    # n1 "restarts": same node_id, new process state, no inbound sessions
    (tmp_path / "restart").mkdir()
    n1b = NetworkNode(1, 19594, [19593], n0.dna, IDENTITY, ledger, str(tmp_path / "restart"),
                      signing_key_path=key)   # same saved key, fresh sessions
    await n1b.start_server()
    try:
        await n0.mine_and_gossip()   # stale session -> "n" -> re-handshake -> resend
        await asyncio.sleep(0.2)
        assert ledger.balance("node-0") == 2
    finally:
        n1b.server.close(); await n1b.server.wait_closed()


@pytest.mark.asyncio
async def test_oversized_frame_and_silent_connection_are_dropped(tmp_path, monkeypatch):
    import network_node
    monkeypatch.setattr(network_node, "READ_TIMEOUT_SECONDS", 0.3)
    ledger = TokenLedger(store_path=os.path.join(str(tmp_path), "ledger.json"))
    n1 = _node(1, 19595, [], tmp_path, ledger)
    await n1.start_server()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 19595)
        writer.write(b"B" + (network_node.MAX_BLOCK_FRAME_BYTES + 1).to_bytes(4, "big"))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=2) == b""   # closed, nothing read
        writer.close()

        reader, writer = await asyncio.open_connection("127.0.0.1", 19595)
        assert await asyncio.wait_for(reader.read(), timeout=2) == b""   # idle -> timed out, closed
        writer.close()
        assert n1.blocks_received == 0
    finally:
        n1.server.close(); await n1.server.wait_closed()
