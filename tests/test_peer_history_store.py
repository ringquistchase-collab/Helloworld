import os

from network_os import PeerInfo
from peer_history_store import save_peer_history, load_peer_history, merge_loaded_history_into_node


class FakeNode:
    def __init__(self, peers=None):
        self.peers = peers or {}


def test_save_and_load_roundtrip(tmp_path):
    path = os.path.join(str(tmp_path), "history.json")
    history = [("1.2.3.4", 9001, 100.0), ("1.2.3.4", 9002, 200.0)]
    peer = PeerInfo(node_id="peer-1", host="1.2.3.4", port=9002, last_seen=200.0, address_history=history)
    node = FakeNode(peers={"peer-1": peer})

    save_peer_history(node, path)
    loaded = load_peer_history(path)

    assert "peer-1" in loaded
    assert loaded["peer-1"]["last_seen"] == 200.0
    assert loaded["peer-1"]["address_history"] == [["1.2.3.4", 9001, 100.0], ["1.2.3.4", 9002, 200.0]]


def test_load_missing_file_returns_empty_dict(tmp_path):
    path = os.path.join(str(tmp_path), "does_not_exist.json")
    assert load_peer_history(path) == {}


def test_merge_restores_peer_not_currently_connected(tmp_path):
    path = os.path.join(str(tmp_path), "history.json")
    history = [("1.2.3.4", 9001, 100.0)]
    peer = PeerInfo(node_id="peer-1", host="1.2.3.4", port=9001, last_seen=100.0, address_history=history)
    save_peer_history(FakeNode(peers={"peer-1": peer}), path)

    fresh_node = FakeNode(peers={})
    restored = merge_loaded_history_into_node(fresh_node, path)

    assert restored == 1
    assert "peer-1" in fresh_node.peers
    assert fresh_node.peers["peer-1"].address_history == [["1.2.3.4", 9001, 100.0]]


def test_merge_does_not_restore_session_key_or_signing_pub(tmp_path):
    """Security boundary: restoring crypto session state from disk would
    mean trusting a peer without re-verifying their signature."""
    path = os.path.join(str(tmp_path), "history.json")
    history = [("1.2.3.4", 9001, 100.0)]
    peer = PeerInfo(node_id="peer-1", host="1.2.3.4", port=9001, last_seen=100.0, address_history=history)
    save_peer_history(FakeNode(peers={"peer-1": peer}), path)

    fresh_node = FakeNode(peers={})
    merge_loaded_history_into_node(fresh_node, path)

    assert fresh_node.peers["peer-1"].signing_pub is None
    assert fresh_node.peers["peer-1"].session_key is None
    assert fresh_node.peers["peer-1"].writer is None


def test_merge_does_not_overwrite_an_already_connected_peer(tmp_path):
    path = os.path.join(str(tmp_path), "history.json")
    old_history = [("1.2.3.4", 9001, 100.0)]
    old_peer = PeerInfo(node_id="peer-1", host="1.2.3.4", port=9001, last_seen=100.0, address_history=old_history)
    save_peer_history(FakeNode(peers={"peer-1": old_peer}), path)

    live_peer = PeerInfo(node_id="peer-1", host="5.6.7.8", port=9999, last_seen=999.0,
                          address_history=[("5.6.7.8", 9999, 999.0)], signing_pub="real-key")
    node_with_live_peer = FakeNode(peers={"peer-1": live_peer})

    restored = merge_loaded_history_into_node(node_with_live_peer, path)

    assert restored == 0
    assert node_with_live_peer.peers["peer-1"].signing_pub == "real-key"
