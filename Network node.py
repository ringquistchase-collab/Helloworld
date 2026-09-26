#!/usr/bin/env python3
"""
network_node.py — consolidated NetworkNode.

Replaces the four overlapping live_*.py demo scripts with one class:
  - real per-peer X25519 handshake (crypto_layer.py) — NOT one shared key
  - identity routed through DigitalDNA's real consent gate
    (digital_dna_mirror_addon.py) — every mined block is also recorded as
    a consent-gated "network_mining" signal, so the identity fingerprint
    actually evolves with real network activity instead of being computed
    once and hardcoded
  - real local chain storage with previous_hash linkage (chain_store.py)
  - real, non-monetary tokens awarded for peer-verified blocks (token_ledger.py)
  - pluggable "enrichers": functions that add real external data to a
    block on a schedule (research lookups, external-chain reads) —
    swap/add enrichers without touching the node's core logic

Honest scope note: consensus/fork-resolution across nodes is NOT
implemented here (see chain_store.py's docstring) — each node keeps its
own verified local chain of what it mined and what it received.
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
from crypto_layer import generate_exchange_keypair, derive_shared_key, aead_encrypt, aead_decrypt
from chain_store import ChainStore
from token_ledger import TokenLedger
from digital_dna_mirror_addon import DigitalDNA

HANDSHAKE_CONTEXT = b"dna-chain-project/node-session/v1"


@dataclass
class PeerSession:
    address: tuple  # (host, port) — real network address, not assumed localhost
    session_key: Optional[bytes] = None
    handshake_done: bool = False


class NetworkNode:
    def __init__(
        self,
        node_id: int,
        port: int,
        peers: list[tuple],  # list of (host, port) — can be other machines' real IPs
        dna: DigitalDNA,
        identity_strand: str,
        ledger: TokenLedger,
        chain_dir: str,
        bind_host: str = "127.0.0.1",
        mine_interval: float = 1.3,
        enrichers: Optional[list[Callable[[int], Optional[dict]]]] = None,
    ):
        self.node_id = node_id
        self.node_key = f"node-{node_id}"
        self.bind_host = bind_host
        self.port = port
        self.peers = [tuple(p) for p in peers]
        self.dna = dna
        # Frozen for the duration of this run — see module docstring note
        # below on why the broadcast/verified identity must be stable even
        # though dna.signals keeps growing from real mining activity.
        self.identity_strand = identity_strand
        self.ledger = ledger
        self.chain = ChainStore(store_path=os.path.join(chain_dir, f"chain_{self.node_key}.json"))
        self.mine_interval = mine_interval
        self.enrichers = enrichers or []

        self.my_priv, self.my_pub = generate_exchange_keypair()
        self.sessions: dict[tuple, PeerSession] = {addr: PeerSession(address=addr) for addr in self.peers}

        self.block_counter = 0
        self.blocks_received = 0
        self.server = None

    def log(self, msg: str):
        print(f"[{time.strftime('%H:%M:%S')}] {self.node_key}:{self.port}  {msg}")

    # -- server side --

    async def handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            # The observed IP comes from the actual TCP socket, not a
            # self-reported field, so a peer can't spoof which address it
            # claims to be. Only the listening PORT is self-reported (it
            # has to be — the OS-assigned outbound port isn't the peer's
            # listening port), combined with the real observed IP.
            observed_ip = writer.get_extra_info("peername")[0]
            msg_type = (await reader.readexactly(1)).decode()

            if msg_type == "H":  # handshake: sender's raw X25519 public key + claimed listening port
                body = await reader.readexactly(4 + 32)
                origin_port = int.from_bytes(body[:4], "big")
                their_pub = body[4:]
                origin_addr = (observed_ip, origin_port)
                session_key = derive_shared_key(self.my_priv, their_pub, HANDSHAKE_CONTEXT)
                self.sessions[origin_addr] = PeerSession(address=origin_addr, session_key=session_key, handshake_done=True)
                # reply with our own public key so the initiator can derive the same key
                writer.write(b"h" + self.port.to_bytes(4, "big") + self.my_pub)
                await writer.drain()

            elif msg_type == "B":  # encrypted block
                header = await reader.readexactly(4)
                length = int.from_bytes(header, "big")
                frame_with_port = await reader.readexactly(length)
                origin_port = int.from_bytes(frame_with_port[:4], "big")
                frame = frame_with_port[4:]
                origin_addr = (observed_ip, origin_port)

                session = self.sessions.get(origin_addr)
                if not session or not session.handshake_done:
                    self.log(f"<- block from unknown/unshaken peer {origin_addr}, dropping")
                    return

                # AAD uses PORTS only (not host strings) — a bind_host of
                # "0.0.0.0" isn't the same string as the routable IP a peer
                # observes us from, so host-string AAD would mismatch across
                # real machines even though the handshake/encryption is
                # correct. Ports are exchanged explicitly on both sides and
                # stay consistent regardless of how a node is bound.
                plaintext = aead_decrypt(
                    session.session_key, frame, aad=f"{origin_port}->{self.port}".encode()
                )
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
        self.server = await asyncio.start_server(self.handle_conn, self.bind_host, self.port)
        self.log(f"listening on {self.bind_host}:{self.port}")

    # -- client side: handshake + gossip --

    async def _ensure_handshake(self, peer_addr: tuple) -> bool:
        session = self.sessions.setdefault(peer_addr, PeerSession(address=peer_addr))
        if session.handshake_done:
            return True
        host, port = peer_addr
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.write(b"H" + self.port.to_bytes(4, "big") + self.my_pub)
            await writer.drain()
            reply = await reader.readexactly(1 + 4 + 32)
            their_pub = reply[5:]
            session.session_key = derive_shared_key(self.my_priv, their_pub, HANDSHAKE_CONTEXT)
            session.handshake_done = True
            writer.close()
            await writer.wait_closed()
            self.log(f"real X25519 handshake complete with peer {host}:{port}")
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
            self.log(f"handshake with {host}:{port} failed ({e}) — will retry next cycle")
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
        # therefore fingerprint_report()) genuinely grows with real network
        # activity. This is intentionally NOT what gets broadcast/verified
        # per block above (self.identity_strand, frozen at startup) — a
        # per-block verification target has to be stable, or every peer
        # fails to match it the instant anything mines again. The two are
        # different concerns: "prove this block is from your network" vs
        # "your identity's history keeps accumulating real events."
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

        for peer_addr in self.peers:
            if not await self._ensure_handshake(peer_addr):
                continue
            host, port = peer_addr
            session = self.sessions[peer_addr]
            frame = aead_encrypt(
                session.session_key, json.dumps(block).encode(),
                aad=f"{self.port}->{port}".encode()
            )
            payload = self.port.to_bytes(4, "big") + frame
            try:
                reader, writer = await asyncio.open_connection(host, port)
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
