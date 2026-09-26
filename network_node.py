#!/usr/bin/env python3
"""
network_node.py — consolidated NetworkNode.

Replaces the four overlapping live_*.py demo scripts with one class:
  - real per-peer X25519 handshake (crypto_layer.py) — NOT one shared key
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

Honest scope note: consensus/fork-resolution across nodes is NOT
implemented here (see chain_store.py's docstring) — each node keeps its
own verified local chain of what it mined and what it received.

INTEGRATION NOTE (why this differs slightly from the standalone draft):
this uses the project's real modules, so it imports DigitalDNA from
digital_dna.py, gets a raw-bytes X25519 public key via
crypto_layer.generate_exchange_keypair_raw(), and records mining through
the real consent gate under the "network_mining" source (added to
digital_dna.ALLOWED_SOURCES). All localhost-only, all self-stopping.
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
from crypto_layer import generate_exchange_keypair_raw, derive_shared_key, aead_encrypt, aead_decrypt
from chain_store import ChainStore
from token_ledger import TokenLedger
from digital_dna import DigitalDNA

HANDSHAKE_CONTEXT = b"dna-chain-project/node-session/v1"


@dataclass
class PeerSession:
    port: int
    session_key: Optional[bytes] = None
    handshake_done: bool = False


class NetworkNode:
    def __init__(
        self,
        node_id: int,
        port: int,
        peer_ports: list[int],
        dna: DigitalDNA,
        identity_strand: str,
        ledger: TokenLedger,
        chain_dir: str,
        mine_interval: float = 1.3,
        enrichers: Optional[list[Callable[[int], Optional[dict]]]] = None,
    ):
        self.node_id = node_id
        self.node_key = f"node-{node_id}"
        self.port = port
        self.peer_ports = peer_ports
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
        self.peer_pub_by_port: dict[int, bytes] = {}
        self.sessions: dict[int, PeerSession] = {p: PeerSession(port=p) for p in peer_ports}

        self.block_counter = 0
        self.blocks_received = 0
        self.server = None

    def log(self, msg: str):
        print(f"[{time.strftime('%H:%M:%S')}] {self.node_key}:{self.port}  {msg}")

    # -- server side --

    async def handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            msg_type = (await reader.readexactly(1)).decode()

            if msg_type == "H":  # handshake: sender's raw X25519 public key + origin port
                body = await reader.readexactly(4 + 32)
                origin_port = int.from_bytes(body[:4], "big")
                their_pub = body[4:]
                self.peer_pub_by_port[origin_port] = their_pub
                session_key = derive_shared_key(self.my_priv, their_pub, HANDSHAKE_CONTEXT)
                if origin_port not in self.sessions:
                    self.sessions[origin_port] = PeerSession(port=origin_port)
                self.sessions[origin_port].session_key = session_key
                self.sessions[origin_port].handshake_done = True
                # reply with our own public key so the initiator can derive the same key
                writer.write(b"h" + self.port.to_bytes(4, "big") + self.my_pub)
                await writer.drain()

            elif msg_type == "B":  # encrypted block
                header = await reader.readexactly(4)
                length = int.from_bytes(header, "big")
                frame_with_port = await reader.readexactly(length)
                origin_port = int.from_bytes(frame_with_port[:4], "big")
                frame = frame_with_port[4:]

                session = self.sessions.get(origin_port)
                if not session or not session.handshake_done:
                    self.log(f"<- block from unknown/unshaken peer :{origin_port}, dropping")
                    return

                plaintext = aead_decrypt(session.session_key, frame, aad=f"{origin_port}->{self.port}".encode())
                block = json.loads(plaintext.decode())
                self.blocks_received += 1
                self._verify_and_process(block)
        except (asyncio.IncompleteReadError, ConnectionResetError):
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
        fully_verified = strand_ok and complement_ok and identity_ok

        origin_key = f"node-{block['origin']}"
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
                f"(strand={strand_ok} complement={complement_ok} identity={identity_ok})"
            )

    async def start_server(self):
        self.server = await asyncio.start_server(self.handle_conn, "127.0.0.1", self.port)

    # -- client side: handshake + gossip --

    async def _ensure_handshake(self, peer_port: int) -> bool:
        session = self.sessions.setdefault(peer_port, PeerSession(port=peer_port))
        if session.handshake_done:
            return True
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", peer_port)
            writer.write(b"H" + self.port.to_bytes(4, "big") + self.my_pub)
            await writer.drain()
            reply = await reader.readexactly(1 + 4 + 32)
            their_pub = reply[5:]
            session.session_key = derive_shared_key(self.my_priv, their_pub, HANDSHAKE_CONTEXT)
            session.handshake_done = True
            writer.close()
            await writer.wait_closed()
            self.log(f"real X25519 handshake complete with peer :{peer_port}")
            return True
        except (ConnectionRefusedError, OSError):
            return False

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
            "index": self.block_counter,
            "hash_hex": digest.hex(),
            "strand": strand,
            "complement": complement,
            "identity_strand": self.identity_strand,
            **enrichment,
        }

        # Store in OUR OWN real local chain (previous_hash linked).
        self.chain.append(block)

        tag = " ".join(f"[{k}]" for k in enrichment)
        self.log(f"-> mined block #{self.block_counter} hash={digest.hex()[:12]}... {tag}")

        for peer_port in self.peer_ports:
            if not await self._ensure_handshake(peer_port):
                continue
            session = self.sessions[peer_port]
            frame = aead_encrypt(session.session_key, json.dumps(block).encode(), aad=f"{self.port}->{peer_port}".encode())
            payload = self.port.to_bytes(4, "big") + frame
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", peer_port)
                writer.write(b"B" + len(payload).to_bytes(4, "big") + payload)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except (ConnectionRefusedError, OSError):
                pass

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
