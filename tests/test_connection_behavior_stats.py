import time

from network_os import PeerInfo
from connection_behavior_stats import connection_behavior_for_peer


def _peer_with_history(history):
    return PeerInfo(node_id="test-peer", host="127.0.0.1", port=9999, address_history=history)


def test_no_history_returns_zeroed_stats():
    peer = _peer_with_history([])
    stats = connection_behavior_for_peer(peer)
    assert stats == {"conn_reconnect_count": 0, "conn_unique_addresses": 0, "conn_uptime_seconds": 0}


def test_single_connection_has_zero_reconnects():
    now = time.time()
    peer = _peer_with_history([("1.2.3.4", 9001, now)])
    stats = connection_behavior_for_peer(peer)
    assert stats["conn_reconnect_count"] == 0
    assert stats["conn_unique_addresses"] == 1


def test_three_connections_from_different_addresses():
    now = time.time()
    history = [
        ("1.2.3.4", 9001, now),
        ("1.2.3.4", 9002, now + 1),
        ("1.2.3.4", 9003, now + 2),
    ]
    peer = _peer_with_history(history)
    stats = connection_behavior_for_peer(peer)
    assert stats["conn_reconnect_count"] == 2
    assert stats["conn_unique_addresses"] == 3
    assert stats["conn_avg_seconds_between_reconnects"] == 1.0


def test_repeated_connections_from_same_address_still_count_as_reconnects():
    """address_history entries are only appended when the address
    actually changes (see network_os.py's _dispatch), so every entry
    here is by definition a distinct address -- but conn_unique_addresses
    should still correctly dedupe if the same address appears twice
    non-consecutively."""
    now = time.time()
    history = [
        ("1.2.3.4", 9001, now),
        ("1.2.3.4", 9002, now + 1),
        ("1.2.3.4", 9001, now + 2),  # back to the first address
    ]
    peer = _peer_with_history(history)
    stats = connection_behavior_for_peer(peer)
    assert stats["conn_reconnect_count"] == 2
    assert stats["conn_unique_addresses"] == 2  # only 2 distinct (host, port) pairs
