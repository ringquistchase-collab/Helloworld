#!/usr/bin/env python3
"""
network_node.py — consolidated NetworkNode.

Replaces the four overlapping live_*.py demo scripts with one class:
  - real per-peer X25519 handshake (crypto_layer.py) — NOT one shared key
  - Ed25519-signed handshakes and blocks: each node has its own signing
    key, pinned per origin node_id by its peers (explicitly via
    trust_peer(), or trust-on-first-use), so a receiver can verify WHICH
    node produced a block, not just that it knows the shared identity strand.
    The key is per-process unless signing_key_path is given, in which case
    it's loaded from / saved to that PEM file so the node keeps its identity
    across runs.
  - replay protection: a block must arrive over a session whose signed
    handshake came from its origin, and each (origin, boot_id, index) earns
    a token at most once (boot_id is random per node start, so a restarted
    node's index counter starting over isn't mistaken for a replay)
  - identity routed through DigitalDNA's real consent gate
    (digital_dna.py) — every mined block is also recorded as a
    consent-gated "network_mining" signal, so the identity fingerprint
    actually evolves with real network activity instead of being computed
    once and hardcoded
  - real local chain storage with previous_hash linkage (chain_store.py)
  - real, non-monetary tokens awarded for peer-verified blocks (token_ledger.py)
  - pluggable "enrichers": async functions that add real external data to a
    block on a schedule (research lookups, external-chain reads) —
    swap/add enrichers without touching the node's core logic
  - multi-host: peers are (host, port) pairs and the listener binds to
    bind_host, so nodes on different machines can all use the same port.
    Sessions are one per direction: a node sends over the session IT
    initiated to a peer's address, and receives over the session the peer
    initiated, looked up by the peer's handshake-authenticated node_id. A
    receiver that has no session for a sender (e.g. it restarted) answers
    "n" and the sender re-handshakes. Connect/read timeouts and a frame
    size cap keep one bad connection from stalling or exhausting a node.

Honest scope note: consensus/fork-resolution across nodes is NOT
implemented here (see chain_store.py's docstring) — each node keeps its
own verified local chain of what it mined and what it received.

INTEGRATION NOTE (why this differs slightly from the standalone draft):
this uses the project's real modules, so it imports DigitalDNA from
digital_dna.py, gets a raw-bytes X25519 public key via
crypto_layer.generate_exchange_keypair_raw(), and records mining through
the real consent gate under the "network_mining" source (added to
digital_dna.ALLOWED_SOURCES). Localhost-only by default (bind_host and
peer hosts default to 127.0.0.1); see run_node_cli.py for multi-machine use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from dna_binary_codec import encode_to_dna, decode_from_dna, complement_strand
from cryptography.exceptions import InvalidTag

from crypto_layer import (
    generate_exchange_keypair_raw, derive_shared_key, aead_encrypt, aead_decrypt,
    generate_signing_keypair, load_or_create_signing_keypair,
    signing_pub_to_hex, signing_pub_from_hex, sign, verify,
)
from chain_store import ChainStore
from token_ledger import TokenLedger
from digital_dna import DigitalDNA

HANDSHAKE_CONTEXT = b"dna-chain-project/node-session/v1"
HANDSHAKE_SIG_CONTEXT = b"dna-chain-project/handshake-sig/v1|"
BLOCK_SIG_CONTEXT = b"dna-chain-project/block-sig/v1|"

# Handshake body after the 1-byte type: port(4) + node_id(4) +
# X25519 pub(32) + Ed25519 pub(32) + Ed25519 sig(64)
HANDSHAKE_BODY_LEN = 4 + 4 + 32 + 32 + 64

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 10.0
MAX_BLOCK_FRAME_BYTES = 1 << 20   # 1 MiB; real blocks are a few KB

# 1-byte status a receiver writes back after a block frame
STATUS_OK = b"k"
STATUS_NO_SESSION = b"n"   # unknown sender session or undecryptable: re-handshake


def _handshake_signed_bytes(node_id: int, port: int, exchange_pub: bytes) -> bytes:
    return HANDSHAKE_SIG_CONTEXT + node_id.to_bytes(4, "big") + port.to_bytes(4, "big") + exchange_pub


def block_signing_bytes(block: dict) -> bytes:
    """Canonical bytes a block's signature covers: every field except
    "sig" itself, JSON-encoded with sorted keys and no whitespace."""
    unsigned = {k: v for k, v in block.items() if k != "sig"}
    return BLOCK_SIG_CONTEXT + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()


def _block_aad(sender_id: int, receiver_id: int) -> bytes:
    return f"node-{sender_id}->node-{receiver_id}".encode()


@dataclass
class PeerSession:
    session_key: Optional[bytes] = None
    handshake_done: bool = False
    peer_node_id: Optional[int] = None   # authenticated by the signed handshake


class NetworkNode:
    def __init__(
        self,
        node_id: int,
        port: int,
        peer_ports: Optional[list[int]],
        dna: DigitalDNA,
        identity_strand: str,
        ledger: TokenLedger,
        chain_dir: str,
        mine_interval: float = 1.3,
        enrichers: Optional[list[Callable[[int], Optional[dict]]]] = None,
        require_known_peers: bool = False,
        signing_key_path: Optional[str] = None,
        signing_key_passphrase: Optional[bytes] = None,
        known_peers_path: Optional[str] = None,
        peers: Optional[list[tuple[str, int]]] = None,
        bind_host: str = "127.0.0.1",
    ):
        self.node_id = node_id
        self.node_key = f"node-{node_id}"
        self.port = port
        self.bind_host = bind_host
        # peers (host, port) takes precedence; peer_ports is the
        # localhost shorthand used by run_consolidated_network.py
        self.peers: list[tuple[str, int]] = (
            list(peers) if peers is not None else [("127.0.0.1", p) for p in (peer_ports or [])]
        )
        self.dna = dna
        # Frozen for the duration of this run — see the note in
        # mine_and_gossip() on why the broadcast/verified identity must be
        # stable even though dna.signals keeps growing from real mining.
        self.identity_strand = identity_strand
        self.ledger = ledger
        self.chain = ChainStore(store_path=os.path.join(chain_dir, f"chain_{self.node_key}.json"))
        self.mine_interval = mine_interval
        self.enrichers = enrichers or []

        self.my_priv, self.my_pub = generate_exchange_keypair_raw()
        if signing_key_path:
            self.signing_priv, signing_pub = load_or_create_signing_keypair(
                signing_key_path, signing_key_passphrase
            )
        else:
            self.signing_priv, signing_pub = generate_signing_keypair()
        self.boot_id = os.urandom(8).hex()
        self.signing_pub_hex = signing_pub_to_hex(signing_pub)
        # origin node_id -> pinned Ed25519 public key (hex). With
        # require_known_peers=True only keys added via trust_peer() are
        # accepted; otherwise the first key seen for a node_id is pinned.
        # With known_peers_path, pins are loaded from and saved to that
        # JSON file, so a peer whose key changes between runs is rejected.
        self.known_peers_path = known_peers_path
        self.peer_signing_keys: dict[int, str] = self._load_known_peers()
        self.require_known_peers = require_known_peers
        # (origin node_id, boot_id) -> block indices already accepted
        self.accepted_indices: dict[tuple[int, Optional[str]], set[int]] = {}
        # sessions WE initiated, by peer address (used to send), and
        # sessions peers initiated, by their authenticated node_id (used to
        # receive). One per direction, so simultaneous handshakes between
        # two nodes can't leave them holding different keys.
        self.outbound: dict[tuple[str, int], PeerSession] = {}
        self.inbound: dict[int, PeerSession] = {}

        self.block_counter = 0
        self.blocks_received = 0
        self.server = None

    def log(self, msg: str):
        print(f"[{time.strftime('%H:%M:%S')}] {self.node_key}:{self.port}  {msg}")

    # -- signing keys --

    def _load_known_peers(self) -> dict[int, str]:
        """Read saved pins. A file that exists but can't be parsed raises
        ValueError instead of being treated as empty, since that would
        silently drop every pin and re-trust whatever key shows up next."""
        if not self.known_peers_path or not os.path.exists(self.known_peers_path):
            return {}
        try:
            with open(self.known_peers_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            pins = {int(k): str(v) for k, v in raw.items()}
            for v in pins.values():
                signing_pub_from_hex(v)
        except (OSError, ValueError, AttributeError) as e:
            raise ValueError(f"could not load known peers from {self.known_peers_path}: {e}") from e
        return pins

    def _save_known_peers(self) -> None:
        if not self.known_peers_path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.known_peers_path)), exist_ok=True)
        tmp_path = self.known_peers_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in sorted(self.peer_signing_keys.items())}, f, indent=2)
        os.replace(tmp_path, self.known_peers_path)

    def trust_peer(self, node_id: int, signing_pub_hex: str, replace: bool = False) -> None:
        """Pin a peer's Ed25519 public key ahead of time (out-of-band).
        Raises ValueError if a DIFFERENT key is already pinned for that
        node_id, unless replace=True -- a changed key is exactly what
        pinning exists to catch, so replacing one must be deliberate."""
        pinned = self.peer_signing_keys.get(node_id)
        if pinned == signing_pub_hex:
            return
        if pinned is not None and not replace:
            raise ValueError(
                f"node-{node_id}'s signing key differs from the one pinned for it "
                f"({pinned[:16]}... vs {signing_pub_hex[:16]}...)"
            )
        self.peer_signing_keys[node_id] = signing_pub_hex
        self._save_known_peers()

    def _pin_or_check(self, node_id: int, signing_pub_hex: str) -> bool:
        pinned = self.peer_signing_keys.get(node_id)
        if pinned is not None:
            return pinned == signing_pub_hex
        if self.require_known_peers or node_id == self.node_id:
            return False
        self.peer_signing_keys[node_id] = signing_pub_hex  # trust on first use
        self._save_known_peers()
        return True

    def _handshake_body(self) -> bytes:
        sig = sign(self.signing_priv, _handshake_signed_bytes(self.node_id, self.port, self.my_pub))
        return (
            self.port.to_bytes(4, "big")
            + self.node_id.to_bytes(4, "big")
            + self.my_pub
            + bytes.fromhex(self.signing_pub_hex)
            + sig
        )

    def _check_handshake_body(self, body: bytes) -> Optional[tuple[int, int, bytes]]:
        """Verify a peer's handshake body. Returns (port, node_id, X25519
        pub) if the signature is valid and the signing key matches what is
        pinned for that node_id, else None."""
        port = int.from_bytes(body[0:4], "big")
        node_id = int.from_bytes(body[4:8], "big")
        exchange_pub = body[8:40]
        signing_pub_hex = body[40:72].hex()
        sig = body[72:136]
        try:
            signing_pub = signing_pub_from_hex(signing_pub_hex)
        except ValueError:
            return None
        if not verify(signing_pub, _handshake_signed_bytes(node_id, port, exchange_pub), sig):
            return None
        if not self._pin_or_check(node_id, signing_pub_hex):
            return None
        return port, node_id, exchange_pub

    def _block_signature_ok(self, block: dict) -> bool:
        pinned = self.peer_signing_keys.get(block.get("origin"))
        if pinned is None or not isinstance(block.get("sig"), str):
            return False
        try:
            sig = bytes.fromhex(block["sig"])
        except ValueError:
            return False
        return verify(signing_pub_from_hex(pinned), block_signing_bytes(block), sig)

    # -- server side --

    async def _read(self, reader: asyncio.StreamReader, n: int) -> bytes:
        return await asyncio.wait_for(reader.readexactly(n), timeout=READ_TIMEOUT_SECONDS)

    async def handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            msg_type = await self._read(reader, 1)

            if msg_type == b"H":  # signed handshake: port, node_id, X25519 pub, Ed25519 pub, sig
                body = await self._read(reader, HANDSHAKE_BODY_LEN)
                checked = self._check_handshake_body(body)
                if checked is None:
                    self.log("<- handshake with bad signature or unpinned/changed signing key, dropping")
                    return
                _, their_node_id, their_pub = checked
                self.inbound[their_node_id] = PeerSession(
                    session_key=derive_shared_key(self.my_priv, their_pub, HANDSHAKE_CONTEXT),
                    handshake_done=True,
                    peer_node_id=their_node_id,
                )
                # reply with our own signed handshake so the initiator can
                # verify us and derive the same key
                writer.write(b"h" + self._handshake_body())
                await writer.drain()

            elif msg_type == b"B":  # encrypted block: sender node_id(4) + AEAD frame
                length = int.from_bytes(await self._read(reader, 4), "big")
                if not 4 <= length <= MAX_BLOCK_FRAME_BYTES:
                    self.log(f"<- block frame of {length} bytes out of bounds, dropping")
                    return
                frame_with_sender = await self._read(reader, length)
                sender_id = int.from_bytes(frame_with_sender[:4], "big")
                frame = frame_with_sender[4:]

                session = self.inbound.get(sender_id)
                try:
                    if not session or not session.handshake_done:
                        raise LookupError
                    plaintext = aead_decrypt(session.session_key, frame, aad=_block_aad(sender_id, self.node_id))
                except (LookupError, InvalidTag):
                    self.log(f"<- block from node-{sender_id} with no usable session, asking it to re-handshake")
                    writer.write(STATUS_NO_SESSION)
                    await writer.drain()
                    return
                writer.write(STATUS_OK)
                await writer.drain()

                block = json.loads(plaintext.decode())
                self.blocks_received += 1
                if block.get("origin") != session.peer_node_id:
                    self.log(
                        f"<- block claiming origin node-{block.get('origin')} arrived over "
                        f"node-{session.peer_node_id}'s session, dropping (relay/replay)"
                    )
                    return
                self._verify_and_process(block)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionResetError):
            pass
        except Exception as e:
            self.log(f"handle_conn error: {e}")
        finally:
            writer.close()

    def _verify_and_process(self, block: dict) -> None:
        block_hash = bytes.fromhex(block["hash_hex"])
        strand_ok = decode_from_dna(block["strand"]) == block_hash
        complement_ok = decode_from_dna(block["complement"]) == bytes(b ^ 0xFF for b in block_hash)
        identity_ok = block["identity_strand"] == self.identity_strand
        signature_ok = self._block_signature_ok(block)
        fully_verified = strand_ok and complement_ok and identity_ok and signature_ok

        origin_key = f"node-{block['origin']}"
        # Replay protection: "boot_id" and "index" are covered by the
        # signature, so each (origin, boot_id, index) can earn a token at
        # most once. Only blocks
        # that fully verified are recorded, so a forged copy can't burn an
        # index before the real block arrives.
        if fully_verified:
            seen = self.accepted_indices.setdefault((block["origin"], block.get("boot_id")), set())
            if block["index"] in seen:
                self.log(f"<- block #{block['index']} from {origin_key} REPLAYED, already accepted, no token")
                return
            seen.add(block["index"])

        tag = ""
        for key in ("research", "external_info"):
            if block.get(key):
                tag += f"  [{key}]"

        if fully_verified:
            bal = self.ledger.award(origin_key, 1, f"block #{block['index']} verified by {self.node_key}")
            self.log(f"<- block #{block['index']} from {origin_key} VERIFIED, +1 token (balance {bal}){tag}")
        else:
            self.log(
                f"<- block #{block['index']} from {origin_key} FAILED verification "
                f"(strand={strand_ok} complement={complement_ok} identity={identity_ok} "
                f"signature={signature_ok})"
            )

    async def start_server(self):
        self.server = await asyncio.start_server(self.handle_conn, self.bind_host, self.port)

    # -- client side: handshake + gossip --

    async def _connect(self, addr: tuple[str, int]):
        return await asyncio.wait_for(asyncio.open_connection(*addr), timeout=CONNECT_TIMEOUT_SECONDS)

    async def _ensure_handshake(self, addr: tuple[str, int]) -> Optional[PeerSession]:
        """Outbound session to the peer at addr, handshaking if needed.
        Returns None if the peer is unreachable or fails verification."""
        session = self.outbound.get(addr)
        if session and session.handshake_done:
            return session
        host, port = addr
        writer = None
        try:
            reader, writer = await self._connect(addr)
            writer.write(b"H" + self._handshake_body())
            await writer.drain()
            reply = await self._read(reader, 1 + HANDSHAKE_BODY_LEN)
            checked = self._check_handshake_body(reply[1:]) if reply[:1] == b"h" else None
            if checked is None or checked[0] != port:
                self.log(f"handshake reply from {host}:{port} failed signature/key check, not sending")
                return None
            session = PeerSession(
                session_key=derive_shared_key(self.my_priv, checked[2], HANDSHAKE_CONTEXT),
                handshake_done=True,
                peer_node_id=checked[1],
            )
            self.outbound[addr] = session
            self.log(f"real X25519 handshake complete with node-{checked[1]} at {host}:{port}")
            return session
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            return None
        finally:
            if writer is not None:
                writer.close()

    async def _send_block(self, addr: tuple[str, int], session: PeerSession, block: dict) -> Optional[bytes]:
        """Send one encrypted block frame; returns the receiver's 1-byte
        status, or None if the send failed."""
        frame = aead_encrypt(session.session_key, json.dumps(block).encode(),
                             aad=_block_aad(self.node_id, session.peer_node_id))
        payload = self.node_id.to_bytes(4, "big") + frame
        writer = None
        try:
            reader, writer = await self._connect(addr)
            writer.write(b"B" + len(payload).to_bytes(4, "big") + payload)
            await writer.drain()
            return await asyncio.wait_for(reader.read(1), timeout=READ_TIMEOUT_SECONDS)
        except (OSError, asyncio.TimeoutError):
            return None
        finally:
            if writer is not None:
                writer.close()

    async def mine_and_gossip(self):
        self.block_counter += 1

        enrichment = {}
        for enricher in self.enrichers:
            result = await enricher(self.block_counter)
            if result:
                enrichment.update(result)

        raw = (
            self.identity_strand.encode()
            + f"|node={self.node_id}|counter={self.block_counter}|t={time.time()}".encode()
            + os.urandom(8)
        )
        if enrichment:
            raw += json.dumps(enrichment, sort_keys=True).encode()

        digest = hashlib.sha256(raw).digest()
        strand = encode_to_dna(digest)
        complement = complement_strand(strand)

        # Route through the REAL consent gate: this mining event becomes an
        # actual DigitalDNA signal, so your identity's SIGNAL HISTORY (and
        # therefore its accumulated audit trail) genuinely grows with real
        # network activity. This is intentionally NOT what gets
        # broadcast/verified per block above (self.identity_strand, frozen
        # at startup) — a per-block verification target has to be stable, or
        # every peer fails to match it the instant anything mines again. The
        # two are different concerns: "prove this block is from your network"
        # vs "your identity's history keeps accumulating real events."
        self.dna.add_live_signal(
            source="network_mining",
            feature_hash_hex=digest.hex(),
            confidence=1.0,
            consent_verified=True,
        )

        block = {
            "origin": self.node_id,
            "boot_id": self.boot_id,
            "index": self.block_counter,
            "hash_hex": digest.hex(),
            "strand": strand,
            "complement": complement,
            "identity_strand": self.identity_strand,
            **enrichment,
        }
        block["sig"] = sign(self.signing_priv, block_signing_bytes(block)).hex()

        # Store in OUR OWN real local chain (previous_hash linked).
        self.chain.append(block)

        tag = " ".join(f"[{k}]" for k in enrichment)
        self.log(f"-> mined block #{self.block_counter} hash={digest.hex()[:12]}... {tag}")

        for addr in self.peers:
            session = await self._ensure_handshake(addr)
            if session is None:
                continue
            status = await self._send_block(addr, session, block)
            if status == STATUS_NO_SESSION:
                # the peer lost our session (restart) -- handshake again, retry once
                self.outbound.pop(addr, None)
                session = await self._ensure_handshake(addr)
                if session is not None:
                    await self._send_block(addr, session, block)
            elif status is None:
                # unreachable: handshake afresh next time it's back
                self.outbound.pop(addr, None)

    async def run(self, stop_event: asyncio.Event):
        await self.start_server()
        while not stop_event.is_set():
            await self.mine_and_gossip()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.mine_interval)
            except asyncio.TimeoutError:
                pass
        self.server.close()
        await self.server.wait_closed()
