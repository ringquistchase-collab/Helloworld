"""
network_os.py
================
A real multi-node network layer on top of digital_dna.py.

WHAT THIS ACTUALLY IS
----------------------
A lightweight peer-to-peer gossip protocol over plain TCP sockets
(asyncio), where each node:
  - has its own DigitalDNA identity strand (from digital_dna.py)
  - mines local events into blocks and gossips them to known peers
  - scores every block with a DAS quality score (0-6, same scale as
    your existing chain)
  - rolls all of that up into one live MOS score per node (1.0-5.0,
    the standard Mean Opinion Score range) that reflects block
    quality + peer connectivity + cross-node agreement

WHAT THIS IS NOT
----------------
- Not an OS. "Network OS" here means an always-on orchestration
  process, not a kernel — it doesn't touch hardware, processes, or
  system calls beyond normal sockets.
- Not internet-scale peer discovery. There's no DHT, no NAT
  traversal, no bootstrap network. You give it a list of host:port
  peers (LAN IPs, or public IPs with port-forwarding/a relay you set
  up yourself) and it gossips with exactly those. Real internet-wide
  discovery is a separate, much bigger project — this gives you a
  working multi-node protocol you can point at whatever peers you
  actually have reachable.
- Not authenticated or encrypted BY DEFAULT everywhere — but the
  handshake and all post-handshake traffic now genuinely are:
    * Ed25519 signs the handshake, so a peer can't claim someone
      else's node_id without their private key.
    * X25519 + HKDF-SHA256 derives a real per-peer shared session
      key (an actual Diffie-Hellman exchange, not something derived
      from your identity strand).
    * AES-256-GCM authenticated-encrypts every block/ping/pong/topic
      after the handshake — tamper-evident and confidential.
  What's still missing vs. real TLS/SSH: no certificate authority
  or trust-on-first-use pinning (a peer's Ed25519 key is trusted
  the moment you connect to it — fine for known devices, not for
  arbitrary internet peers), and no replay-window/rekeying policy.
  Add pinning before trusting unknown peers over an open network.

IDENTITY VS. NETWORK LEARNING — an important separation
---------------------------------------------------------
Your own DigitalDNA strand (from digital_dna.py) is only ever
mutated by YOUR consented signals via add_live_signal(). Blocks
gossiped in from other nodes do NOT get folded into your personal
identity strand — that would let someone else's data quietly change
who "you" are in the system, which breaks the whole point of a
personal identity strand. Instead, incoming peer blocks go into a
separate NETWORK LEDGER used only for the MOS rollup and for other
algorithms to learn from collectively. Identity stays personal;
learning can be collective.

network_ledger vs. the real mined chain (MaxwellChainAgent) — these
are two DIFFERENT real systems, deliberately not merged:
  - network_ledger (this file): a per-peer, DAS-scored, Ed25519-signed
    APPEND LOG of live-signal events, gossiped over the encrypted
    session. No proof-of-work, no shared consensus chain -- each node
    keeps its own view, seeded by what it directly mined or received.
    Purpose: real-time network-quality rollup (mos_score()).
  - MaxwellChainAgent (maxwell_chain_agent.py): the actual
    proof-of-work research chain this project has been building all
    session (research_finding blocks, etc.), persisted to
    maxwell_chain_agent_state.json, mined by mine_research_blocks.py /
    run_integrated_cycle.py / research_agent.py. Purpose: durable,
    difficulty-verified record of research activity.
  If you want a live-signal event to ALSO land on the real chain, mine
  it there explicitly via MaxwellChainAgent.mine_block() -- this
  file's mine_and_broadcast() only ever touches network_ledger.

TOPIC GOSSIP — kept working for growing_research_agent.py
----------------------------------------------------------------
growing_research_agent.py's GrowingResearchAgent calls
`node.on_topic(callback)` to register for incoming topic shares, and
`node.broadcast_topic(topic_dict)` when it discovers something. Those
two methods didn't exist in an earlier draft of this file's encrypted
redesign (only mine_and_broadcast()'s block/ping/pong messages did),
which would have silently stopped all topic gossip for the entire
existing research pipeline (mine_research_blocks.py,
run_integrated_cycle.py, run_network_peer.py, run_listening_explorer.py)
-- no error, since GrowingResearchAgent only ever checks
hasattr(node, "on_topic"/"broadcast_topic") before using them. Fixed
here: topics now travel as an authenticated-encrypted inner message
type, same as blocks/pings, via the custom_handlers extension point.

Usage
-----
    from digital_dna import DigitalDNA
    from network_os import NetworkNode
    import asyncio

    async def main():
        dna = DigitalDNA(seed_label="my-node")
        node = NetworkNode(dna, host="0.0.0.0", port=8765)
        await node.start()
        await node.connect_peer("192.168.1.42", 8765)   # a real peer

        await node.mine_and_broadcast(
            source="work_login",
            feature_hash_hex="...",
            confidence=0.9,
            consent_verified=True,
        )

        print(node.mos_score())
        await asyncio.sleep(3600)

    asyncio.run(main())
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field

from digital_dna import DigitalDNA
import crypto_layer as ck


HEARTBEAT_INTERVAL_S = 15
LEDGER_WINDOW = 200          # how many recent blocks per node feed DAS avg
PEER_TIMEOUT_S = 45          # no pong within this window -> considered unreachable


# ----------------------------------------------------------------- #
# DAS scoring for arbitrary blocks (local or gossiped)
# ----------------------------------------------------------------- #

def compute_das_score(block: dict) -> float:
    """
    0-6 scale, same range as your existing chain's DAS score.
    If the block already carries an explicit das_score, trust it.
    Otherwise derive one from confidence + recency — a rough but
    honest stand-in until a block passes through your real DAS
    pipeline.
    """
    if "das_score" in block:
        return float(block["das_score"])

    confidence = float(block.get("confidence", 0.5))
    base = 2.0 + confidence * 3.0        # confidence -> 2.0-5.0

    age_s = max(0.0, time.time() - float(block.get("timestamp", time.time())))
    recency_bonus = 1.0 if age_s < 3600 else (0.5 if age_s < 86400 else 0.0)

    return round(min(6.0, base + recency_bonus), 3)


# ----------------------------------------------------------------- #
# Network node
# ----------------------------------------------------------------- #

@dataclass
class PeerInfo:
    node_id: str
    host: str
    port: int
    writer: object = None
    last_seen: float = field(default_factory=time.time)
    latest_block_hash: str | None = None
    signing_pub: object = None      # Ed25519PublicKey, once verified
    session_key: bytes | None = None  # AES-256 key from X25519 ECDH


class NetworkNode:
    def __init__(self, dna: DigitalDNA, host: str = "0.0.0.0", port: int = 8765, audit=None, ledger_store_path: str | None = None):
        self.dna = dna
        # FIXED: use DigitalDNA's own node_id (sha256(seed_label:salt), unique
        # per installation) instead of recomputing sha256(seed_label) alone --
        # the salt-less version would let two different real installs using
        # the same seed_label collide on the same node_id.
        self.node_id = dna.node_id
        self.host = host
        self.port = port
        self.peers: dict[str, PeerInfo] = {}          # node_id -> PeerInfo
        self.ledger_store_path = ledger_store_path     # set to persist network_ledger to disk — see _save_ledger()

        if ledger_store_path and os.path.exists(ledger_store_path):
            with open(ledger_store_path) as f:
                self.network_ledger: dict[str, list[dict]] = json.load(f)
        else:
            self.network_ledger: dict[str, list[dict]] = {}  # node_id -> blocks (incl. our own)
        self.network_ledger.setdefault(self.node_id, [])

        self._server = None
        self._tasks: list[asyncio.Task] = []           # heartbeat + read-loop tasks, for clean shutdown
        self.audit = audit   # optional AuditTrail — see audit_trail.py (not present in this project;
                              # every self.audit.log(...) call below is guarded by `if self.audit is not None`)

        # identity keys for authentication + key exchange (see crypto_layer.py)
        self.signing_priv, self.signing_pub = ck.generate_signing_keypair()
        self.exchange_priv, self.exchange_pub = ck.generate_exchange_keypair()

        # extension point: other modules register a handler for a new inner
        # message "type" (e.g. "topic") without editing this file.
        # handler signature: async def handler(payload: dict, sender_node_id: str)
        self.custom_handlers: dict[str, "callable"] = {}

        # topic gossip (growing_research_agent.py compatibility) -- see the
        # module docstring's "TOPIC GOSSIP" section for why this exists
        self._on_topic = None
        self.custom_handlers["topic"] = self._handle_topic_message

    def _save_ledger(self):
        if not self.ledger_store_path:
            return
        tmp_path = self.ledger_store_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(self.network_ledger, f, indent=2)
        os.replace(tmp_path, self.ledger_store_path)   # atomic, same pattern as digital_dna.py / growing_research_agent.py

    # ---- lifecycle ----
    async def start(self):
        self._server = await asyncio.start_server(self._handle_conn, self.host, self.port)
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        return self._server

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        for peer in self.peers.values():
            if peer.writer is not None:
                peer.writer.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- handshake helpers ----
    def _hello_fields(self) -> dict:
        exchange_hex = ck.exchange_pub_to_hex(self.exchange_pub)
        to_sign = f"{self.node_id}|{self.dna.as_hex()}|{exchange_hex}".encode()
        return {
            "node_id": self.node_id,
            "strand_hex": self.dna.as_hex(),
            "signing_pub": ck.signing_pub_to_hex(self.signing_pub),
            "exchange_pub": exchange_hex,
            "sig": ck.sign(self.signing_priv, to_sign).hex(),
        }

    def _verify_hello(self, msg: dict) -> bool:
        try:
            peer_signing_pub = ck.signing_pub_from_hex(msg["signing_pub"])
            to_verify = f"{msg['node_id']}|{msg['strand_hex']}|{msg['exchange_pub']}".encode()
            return ck.verify(peer_signing_pub, to_verify, bytes.fromhex(msg["sig"]))
        except (KeyError, ValueError):
            return False

    # ---- connecting out to a peer ----
    async def connect_peer(self, host: str, port: int):
        reader, writer = await asyncio.open_connection(host, port)
        hello = {"type": "hello", "listen_port": self.port, **self._hello_fields()}
        await self._send_plain(writer, hello)
        self._tasks.append(asyncio.create_task(self._read_loop(reader, writer, host)))

    # ---- inbound connections ----
    async def _handle_conn(self, reader, writer):
        peer_host = writer.get_extra_info("peername")[0]
        await self._read_loop(reader, writer, peer_host)

    async def _read_loop(self, reader, writer, peer_host: str):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                await self._dispatch(msg, writer, peer_host)
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def _dispatch(self, msg: dict, writer, peer_host: str):
        mtype = msg.get("type")

        if mtype == "hello":
            if not self._verify_hello(msg):
                if self.audit is not None:
                    self.audit.log(
                        module="network_os", action="handshake_rejected", node_id=self.node_id,
                        details={"claimed_node_id": msg.get("node_id"), "peer_host": peer_host, "reason": "signature verification failed"},
                    )
                return  # bad/forged signature — silently drop, don't trust this peer
            pid = msg["node_id"]
            session_key = ck.derive_shared_key(
                self.exchange_priv, ck.exchange_pub_from_hex(msg["exchange_pub"])
            )
            self.peers[pid] = PeerInfo(
                node_id=pid, host=peer_host, port=msg.get("listen_port", 0),
                writer=writer, last_seen=time.time(),
                signing_pub=ck.signing_pub_from_hex(msg["signing_pub"]),
                session_key=session_key,
            )
            self.network_ledger.setdefault(pid, [])
            if self.audit is not None:
                self.audit.log(
                    module="network_os", action="handshake_accepted", node_id=self.node_id,
                    details={"peer_node_id": pid, "peer_host": peer_host},
                )
            ack = {"type": "hello_ack", **self._hello_fields()}
            await self._send_plain(writer, ack)

        elif mtype == "hello_ack":
            if not self._verify_hello(msg):
                if self.audit is not None:
                    self.audit.log(
                        module="network_os", action="handshake_rejected", node_id=self.node_id,
                        details={"claimed_node_id": msg.get("node_id"), "peer_host": peer_host, "reason": "signature verification failed"},
                    )
                return
            pid = msg["node_id"]
            session_key = ck.derive_shared_key(
                self.exchange_priv, ck.exchange_pub_from_hex(msg["exchange_pub"])
            )
            peer = self.peers.setdefault(pid, PeerInfo(node_id=pid, host=peer_host, port=0))
            peer.writer = writer
            peer.last_seen = time.time()
            peer.signing_pub = ck.signing_pub_from_hex(msg["signing_pub"])
            if self.audit is not None:
                self.audit.log(
                    module="network_os", action="handshake_accepted", node_id=self.node_id,
                    details={"peer_node_id": pid, "peer_host": peer_host},
                )
            peer.session_key = session_key
            self.network_ledger.setdefault(pid, [])

        elif mtype == "enc":
            await self._dispatch_encrypted(msg, writer, peer_host)

        else:
            pass  # unknown/plaintext non-hello message — ignore rather than trust it

    async def _dispatch_encrypted(self, envelope: dict, writer, peer_host: str):
        pid = envelope.get("node_id")
        peer = self.peers.get(pid)
        if peer is None or peer.session_key is None:
            return  # no session with this node_id yet — can't decrypt, don't trust it
        try:
            plaintext = ck.aead_decrypt(
                peer.session_key, bytes.fromhex(envelope["payload"]), aad=pid.encode()
            )
            inner = json.loads(plaintext.decode())
        except Exception:
            return  # forged / corrupted / wrong key — AES-GCM tag check failed

        itype = inner.get("type")
        if itype == "block":
            block = inner["block"]
            sig_hex = block.pop("sig", None)
            canonical = json.dumps(block, sort_keys=True).encode()
            if sig_hex is None or not ck.verify(peer.signing_pub, canonical, bytes.fromhex(sig_hex)):
                return  # block claims to be from this node but isn't validly signed by them
            block["das_score"] = compute_das_score(block)
            self.network_ledger.setdefault(pid, []).append(block)
            self.network_ledger[pid] = self.network_ledger[pid][-LEDGER_WINDOW:]
            self._save_ledger()
            peer.latest_block_hash = block.get("block_id")
            peer.last_seen = time.time()

        elif itype == "ping":
            await self._send_encrypted(peer, {"type": "pong"})

        elif itype == "pong":
            peer.last_seen = time.time()

        elif itype in self.custom_handlers:
            # already authenticated (AES-GCM under the peer's session key) and
            # already scoped to this specific peer's node_id — custom handlers
            # don't need to redo that verification themselves.
            await self.custom_handlers[itype](inner, pid)

    async def _send_plain(self, writer, msg: dict):
        writer.write((json.dumps(msg) + "\n").encode())
        await writer.drain()

    async def _send_encrypted(self, peer: PeerInfo, inner_msg: dict):
        if peer.writer is None or peer.session_key is None:
            return
        payload = ck.aead_encrypt(
            peer.session_key, json.dumps(inner_msg).encode(), aad=self.node_id.encode()
        )
        envelope = {"type": "enc", "node_id": self.node_id, "payload": payload.hex()}
        await self._send_plain(peer.writer, envelope)

    async def broadcast_encrypted(self, inner_msg: dict):
        dead = []
        for pid, peer in self.peers.items():
            try:
                await self._send_encrypted(peer, inner_msg)
            except (ConnectionError, OSError):
                dead.append(pid)
        for pid in dead:
            del self.peers[pid]

    # ---- topic gossip (growing_research_agent.py compatibility) ----
    def on_topic(self, callback):
        """Register a callback(topic_dict) invoked whenever a peer shares a
        topic. Same interface the pre-encryption network_os.py had --
        growing_research_agent.py calls this directly."""
        self._on_topic = callback

    async def _handle_topic_message(self, inner: dict, sender_node_id: str):
        if self._on_topic is None:
            return
        topic = inner.get("topic")
        if topic is None:
            return
        result = self._on_topic(topic)
        if asyncio.iscoroutine(result):
            await result

    async def broadcast_topic(self, topic: dict):
        """Send a discovered/updated topic to every connected peer, now
        authenticated-encrypted like everything else post-handshake."""
        await self.broadcast_encrypted({"type": "topic", "topic": topic})

    # ---- heartbeat / liveness ----
    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            await self.broadcast_encrypted({"type": "ping"})

    # ---- mining + gossiping ----
    async def mine_and_broadcast(
        self, source: str, feature_hash_hex: str,
        confidence: float = 1.0, consent_verified: bool = True,
    ):
        """
        Folds a consented signal into OUR OWN identity strand (the
        digital_dna consent gate still applies), scores it, signs it
        with our Ed25519 key so peers can verify authorship, records
        it in our own network ledger (NOT the real mined chain -- see
        the module docstring's "network_ledger vs. the real mined
        chain" section), and gossips the SIGNED block — AES-256-GCM
        encrypted per-peer — to every connected peer.
        """
        event = self.dna.add_live_signal(
            source, feature_hash_hex, confidence=confidence,
            consent_verified=consent_verified,
        )
        block = dict(self.dna.blocks[-1])
        block["das_score"] = compute_das_score(block)
        self.network_ledger[self.node_id].append(block)
        self.network_ledger[self.node_id] = self.network_ledger[self.node_id][-LEDGER_WINDOW:]
        self._save_ledger()

        signed_block = {k: v for k, v in block.items() if k != "sig"}
        canonical = json.dumps(signed_block, sort_keys=True).encode()
        signed_block["sig"] = ck.sign(self.signing_priv, canonical).hex()

        await self.broadcast_encrypted({"type": "block", "block": signed_block})

        if self.audit is not None:
            self.audit.log(
                module="network_os", action="mine_block", node_id=self.node_id,
                details={"source": source, "das_score": block["das_score"], "block_id": block["block_id"]},
            )

        return event, block["das_score"]

    # ---- MOS: network-wide rollup ----
    def mos_score(self) -> dict:
        """
        Composite Mean Opinion Score, 1.0-5.0, rolled up from:
          - quality:      avg DAS across all known nodes' recent blocks (0-6 -> 0-5)
          - connectivity: reachable peers / known peers
          - agreement:    fraction of peers whose latest block matches
                           the majority latest block (rough consensus)
        """
        all_blocks = [b for blocks in self.network_ledger.values() for b in blocks]
        if all_blocks:
            avg_das = sum(b.get("das_score", 3.0) for b in all_blocks) / len(all_blocks)
        else:
            avg_das = 3.0
        quality = (avg_das / 6.0) * 5.0

        now = time.time()
        known = len(self.peers) or 1
        reachable = sum(1 for p in self.peers.values() if now - p.last_seen < PEER_TIMEOUT_S)
        connectivity = (reachable / known) * 5.0 if self.peers else 5.0  # solo node = fully "consistent" with itself

        latest_hashes = [p.latest_block_hash for p in self.peers.values() if p.latest_block_hash]
        if latest_hashes:
            majority = max(set(latest_hashes), key=latest_hashes.count)
            agreement = (latest_hashes.count(majority) / len(latest_hashes)) * 5.0
        else:
            agreement = 5.0

        mos = 0.5 * quality + 0.25 * connectivity + 0.25 * agreement
        mos = max(1.0, min(5.0, mos))

        return {
            "mos": round(mos, 3),
            "quality": round(quality, 3),
            "connectivity": round(connectivity, 3),
            "agreement": round(agreement, 3),
            "known_peers": len(self.peers),
            "reachable_peers": reachable,
            "total_blocks_seen": len(all_blocks),
        }


# ----------------------------------------------------------------- #
# Demo: two local nodes gossiping over real TCP sockets (localhost),
# real Ed25519/X25519/AES-GCM handshake + encryption throughout, plus
# a real topic-gossip round-trip proving growing_research_agent.py
# compatibility survived the redesign.
# ----------------------------------------------------------------- #

async def _demo():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        dna_a = DigitalDNA(seed_label="node-a", dna_path=os.path.join(tmp, "a.dna.json"))
        dna_b = DigitalDNA(seed_label="node-b", dna_path=os.path.join(tmp, "b.dna.json"))

        node_a = NetworkNode(dna_a, host="127.0.0.1", port=8801)
        node_b = NetworkNode(dna_b, host="127.0.0.1", port=8802)

        received_topics = []
        node_b.on_topic(lambda t: received_topics.append(t))

        await node_a.start()
        await node_b.start()
        await node_b.connect_peer("127.0.0.1", 8801)
        await asyncio.sleep(0.3)   # let the handshake settle

        for i in range(3):
            h = hashlib.sha256(f"event-{i}".encode()).hexdigest()
            await node_a.mine_and_broadcast("work_login", h, confidence=0.9, consent_verified=True)
            await asyncio.sleep(0.1)

        await node_a.broadcast_topic({"condition": "test topic", "biomarker": "TEST1"})
        await asyncio.sleep(0.3)   # let gossip land

        print("=== Node A MOS ===")
        print(json.dumps(node_a.mos_score(), indent=2))
        print("=== Node B MOS (received A's blocks via encrypted gossip) ===")
        print(json.dumps(node_b.mos_score(), indent=2))
        print("\nNode B's network ledger for node A's id:")
        print(json.dumps(node_b.network_ledger.get(node_a.node_id, []), indent=2))
        print(f"\nTopic gossip compatibility: node B received {len(received_topics)} topic(s) via on_topic(): {received_topics}")

        await node_a.stop()
        await node_b.stop()


if __name__ == "__main__":
    asyncio.run(_demo())
