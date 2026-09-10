"""
peer_history_store.py
================
Persists PeerInfo.address_history to disk — the piece
connection_behavior_stats.py depends on, which currently vanishes on
every restart, unlike network_ledger/token_ledger/dna_state which
already got this treatment.

WHAT GETS SAVED, AND WHAT DELIBERATELY DOESN'T
----------------------------------------------------
Saved: node_id, address_history (host/port/timestamp tuples), last_seen.
NOT saved: signing_pub, session_key, writer — these are live crypto
session state that should come from a REAL fresh handshake on
reconnect, not be restored from disk. Restoring a session key from a
file would mean trusting a peer's identity without actually
re-verifying their signature, which defeats the point of the
handshake. So after a restart, you get back the connection HISTORY,
but the node still has to actually re-handshake with a peer before
trusting them again — this is a deliberate security boundary, not an
oversight.

Usage
-----
    from peer_history_store import save_peer_history, load_peer_history

    save_peer_history(node, "peer_history.json")   # call this whenever you want a snapshot
    history = load_peer_history("peer_history.json")   # {node_id: {"address_history": [...], "last_seen": ...}}
"""

from __future__ import annotations
import json
import os


def save_peer_history(node, path: str = "peer_history.json") -> None:
    data = {}
    for node_id, peer in node.peers.items():
        data[node_id] = {
            "address_history": peer.address_history,
            "last_seen": peer.last_seen,
        }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_peer_history(path: str = "peer_history.json") -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def merge_loaded_history_into_node(node, path: str = "peer_history.json") -> int:
    """After a restart, before any new handshakes happen: restores
    address_history for peers you'll likely reconnect to, so
    connection_behavior_stats.py has continuity across the restart
    instead of starting from zero. Does NOT restore signing_pub or
    session_key — those still require a real handshake, per the
    security boundary above."""
    from network_os import PeerInfo

    history = load_peer_history(path)
    restored = 0
    for node_id, saved in history.items():
        if node_id not in node.peers:
            node.peers[node_id] = PeerInfo(
                node_id=node_id, host=saved["address_history"][-1][0] if saved["address_history"] else "",
                port=saved["address_history"][-1][1] if saved["address_history"] else 0,
                last_seen=saved["last_seen"],
                address_history=saved["address_history"],
            )
            restored += 1
    return restored


if __name__ == "__main__":
    import asyncio
    import tempfile
    from digital_dna import DigitalDNA
    from network_os import NetworkNode
    from connection_behavior_stats import connection_behavior_for_peer

    async def _demo():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "peer_history.json")

            dna_a = DigitalDNA(seed_label="peer-history-a", dna_path=os.path.join(tmp, "a.dna.json"))
            dna_b = DigitalDNA(seed_label="peer-history-b", dna_path=os.path.join(tmp, "b.dna.json"))
            node_a = NetworkNode(dna_a, host="127.0.0.1", port=9961)
            await node_a.start()

            for port in [9962, 9963]:
                node_b = NetworkNode(dna_b, host="127.0.0.1", port=port)
                await node_b.start()
                await node_b.connect_peer("127.0.0.1", 9961)
                await asyncio.sleep(0.2)
                await node_b.stop()
                await asyncio.sleep(0.1)

            peer_id = list(node_a.peers.keys())[0]
            stats_before = connection_behavior_for_peer(node_a.peers[peer_id])
            print("=== Before 'restart' ===")
            print(stats_before)

            save_peer_history(node_a, path)
            await node_a.stop()

            print("\n=== Real restart: fresh NetworkNode instance ===")
            dna_a2 = DigitalDNA(seed_label="peer-history-a", dna_path=os.path.join(tmp, "a.dna.json"))   # same seed = same identity
            node_a2 = NetworkNode(dna_a2, host="127.0.0.1", port=9964)
            await node_a2.start()

            restored = merge_loaded_history_into_node(node_a2, path)
            print(f"Peers restored from disk: {restored}")

            stats_after = connection_behavior_for_peer(node_a2.peers[peer_id])
            print(f"Stats after restart (same peer_id): {stats_after}")
            print(f"Reconnect count survived restart: {stats_after['conn_reconnect_count'] == stats_before['conn_reconnect_count']}")

            await node_a2.stop()

    asyncio.run(_demo())
