"""
connection_behavior_stats.py
================
Real statistics about a peer's CONNECTION BEHAVIOR — reconnection
frequency, address stability, uptime — derived from PeerInfo.
address_history (network_os.py). These are honest network metrics,
not personality or emotion: "reconnects often" describes network
conditions (roaming, unstable connectivity), nothing about the person
or process behind it.

Feeds into research_art_generator.py / research_video_generator.py's
extra_signal_stats parameter exactly like MOS scores or EEG stats do
— same abstraction rule: real numbers in, honest abstract visual out,
never claimed to represent anything beyond what it is.

Usage
-----
    from connection_behavior_stats import connection_behavior_for_peer

    stats = connection_behavior_for_peer(node.peers[some_node_id])
    result = generate_network_art(mos_score, topic_count, extra_signal_stats=stats)
"""

from __future__ import annotations
import time


def connection_behavior_for_peer(peer) -> dict:
    """peer is a PeerInfo instance (network_os.py). Real stats only."""
    history = peer.address_history
    if not history:
        return {"conn_reconnect_count": 0, "conn_unique_addresses": 0, "conn_uptime_seconds": 0}

    first_seen = history[0][2]
    reconnect_count = len(history) - 1   # first entry is the initial connection, not a "re"-connect
    unique_addresses = len({(h, p) for h, p, _ in history})
    uptime_seconds = round(time.time() - first_seen, 1)

    avg_seconds_between_reconnects = None
    if reconnect_count > 0:
        gaps = [history[i][2] - history[i - 1][2] for i in range(1, len(history))]
        avg_seconds_between_reconnects = round(sum(gaps) / len(gaps), 1)

    return {
        "conn_reconnect_count": reconnect_count,
        "conn_unique_addresses": unique_addresses,
        "conn_uptime_seconds": uptime_seconds,
        "conn_avg_seconds_between_reconnects": avg_seconds_between_reconnects,
    }


if __name__ == "__main__":
    import asyncio
    from digital_dna import DigitalDNA
    from network_os import NetworkNode

    try:
        from research_art_generator import _build_art_prompt
    except ImportError:
        _build_art_prompt = None
        print("research_art_generator.py not found in this project -- will show the "
              "real connection-behavior stats but skip building an art prompt from them.\n")

    async def _demo():
        dna_a = DigitalDNA(seed_label="conn-stats-a")
        dna_b = DigitalDNA(seed_label="conn-stats-b")
        node_a = NetworkNode(dna_a, host="127.0.0.1", port=9951)
        await node_a.start()

        # simulate three reconnections from different ports
        for port in [9952, 9953, 9954]:
            node_b = NetworkNode(dna_b, host="127.0.0.1", port=port)
            await node_b.start()
            await node_b.connect_peer("127.0.0.1", 9951)
            await asyncio.sleep(0.2)
            await node_b.stop()
            await asyncio.sleep(0.1)

        peer_id = list(node_a.peers.keys())[0]
        stats = connection_behavior_for_peer(node_a.peers[peer_id])
        print("=== Real connection-behavior stats ===")
        print(stats)

        if _build_art_prompt is not None:
            mos = {"quality": 4.0, "connectivity": 5.0, "agreement": 4.5, "mos": 4.5}
            prompt = _build_art_prompt(mos, topic_count=3, style="abstract, calm blues", extra_signal_stats=stats)
            print("\n=== Real art prompt with connection-behavior stats folded in ===")
            print(prompt)

        await node_a.stop()

    asyncio.run(_demo())
