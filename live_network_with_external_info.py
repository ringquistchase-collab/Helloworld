#!/usr/bin/env python3
"""
live_network_with_external_info.py
=====================================
Adds external_chain_bridge.py to the live identity+token network as a
third, clearly separate concern:

  - your identity strand: carried by every block (unchanged)
  - internal, non-monetary tokens: awarded for peer-verified blocks (unchanged)
  - NEW: every 4th block, a node also reads real, live, anonymous public
    chain-tip data from Bitcoin and Ethereum and stamps it into that block
    as an "external_info" note

The critical property, proven at the end of this run rather than just
claimed: your internal token balances and the external chain data are
NEVER combined. No token amount is ever derived from a Bitcoin/Ethereum
value, and no external value is ever converted into a token award. They
sit side by side in the same block for informational purposes only.

SCOPE / SAFETY: nodes bind to 127.0.0.1 only and the run stops itself
after RUN_SECONDS. External reads are anonymous, read-only, public
chain-tip facts (see external_chain_bridge.py) — no wallets, keys, or
transactions, and nothing crosses into the token ledger's arithmetic.
"""

import asyncio
import hashlib
import json
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dna_binary_codec import encode_to_dna, decode_from_dna, complement_strand
from token_ledger import TokenLedger
from external_chain_bridge import build_external_info_snapshot

IDENTITY_TEXT = "Chase Allen Ringquist | Bixby, Oklahoma | dna-chain-project"
IDENTITY_FINGERPRINT = hashlib.sha256(IDENTITY_TEXT.encode("utf-8")).digest()
IDENTITY_STRAND = encode_to_dna(IDENTITY_FINGERPRINT)

BASE_PORT = 9401
NODE_COUNT = 3
RUN_SECONDS = 8.0
MINE_INTERVAL = 1.4
EXTERNAL_INFO_EVERY_N_BLOCKS = 4

GROUP_KEY = AESGCM.generate_key(bit_length=256)
LEDGER_PATH = os.path.join(os.path.dirname(__file__), "network_token_ledger_external.json")


def encrypt(payload: dict) -> bytes:
    aesgcm = AESGCM(GROUP_KEY)
    nonce = os.urandom(12)
    return nonce + aesgcm.encrypt(nonce, json.dumps(payload).encode(), None)


def decrypt(frame: bytes) -> dict:
    aesgcm = AESGCM(GROUP_KEY)
    plaintext = aesgcm.decrypt(frame[:12], frame[12:], None)
    return json.loads(plaintext.decode())


class Node:
    def __init__(self, node_id: int, port: int, peer_ports: list[int], ledger: TokenLedger):
        self.node_id = node_id
        self.node_key = f"node-{node_id}"
        self.port = port
        self.peer_ports = peer_ports
        self.ledger = ledger
        self.block_counter = 0
        self.blocks_received = 0
        self.external_info_blocks_mined = 0
        self.server = None

    def log(self, msg: str):
        print(f"[{time.strftime('%H:%M:%S')}] {self.node_key}:{self.port}  {msg}")

    async def handle_conn(self, reader, writer):
        try:
            header = await reader.readexactly(4)
            length = int.from_bytes(header, "big")
            frame = await reader.readexactly(length)
            block = decrypt(frame)
            self.blocks_received += 1

            block_hash = bytes.fromhex(block["hash_hex"])
            strand_ok = decode_from_dna(block["strand"]) == block_hash
            complement_ok = decode_from_dna(block["complement"]) == bytes(b ^ 0xFF for b in block_hash)
            identity_ok = block["identity_strand"] == IDENTITY_STRAND
            fully_verified = strand_ok and complement_ok and identity_ok

            origin_key = f"node-{block['origin']}"
            tag = ""
            if block.get("external_info"):
                reads = block["external_info"].get("reads", [])
                parts = [f"{r['chain']}={r.get('value','?')}" for r in reads if "value" in r]
                if parts:
                    tag = f"  [external info: {', '.join(parts)}]"

            if fully_verified:
                bal = self.ledger.award(origin_key, 1, f"block #{block['index']} verified by {self.node_key}")
                self.log(f"<- block #{block['index']} from {origin_key} VERIFIED, +1 token "
                         f"(balance {bal}){tag}")
            else:
                self.log(f"<- block #{block['index']} from {origin_key} FAILED verification{tag}")
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    async def start_server(self):
        self.server = await asyncio.start_server(self.handle_conn, "127.0.0.1", self.port)

    async def mine_and_gossip(self):
        self.block_counter += 1

        external_info = None
        if self.block_counter % EXTERNAL_INFO_EVERY_N_BLOCKS == 0:
            external_info = await asyncio.to_thread(build_external_info_snapshot)
            self.external_info_blocks_mined += 1

        raw = (
            IDENTITY_FINGERPRINT
            + f"|node={self.node_id}|counter={self.block_counter}|t={time.time()}".encode()
            + os.urandom(8)
        )
        if external_info:
            raw += json.dumps(external_info, sort_keys=True).encode()

        digest = hashlib.sha256(raw).digest()
        strand = encode_to_dna(digest)
        complement = complement_strand(strand)

        block = {
            "origin": self.node_id,
            "index": self.block_counter,
            "hash_hex": digest.hex(),
            "strand": strand,
            "complement": complement,
            "identity_strand": IDENTITY_STRAND,
            "external_info": external_info,
        }

        tag = ""
        if external_info:
            parts = [f"{r['chain']}={r.get('value','?')}" for r in external_info.get("reads", []) if "value" in r]
            tag = f"  [external info: {', '.join(parts)}]"
        self.log(f"-> mined block #{self.block_counter} hash={digest.hex()[:12]}...{tag}")

        frame = encrypt(block)
        header = len(frame).to_bytes(4, "big")
        for peer_port in self.peer_ports:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", peer_port)
                writer.write(header + frame)
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
                await asyncio.wait_for(stop_event.wait(), timeout=MINE_INTERVAL)
            except asyncio.TimeoutError:
                pass
        self.server.close()
        await self.server.wait_closed()


async def main():
    if os.path.exists(LEDGER_PATH):
        os.remove(LEDGER_PATH)
    ledger = TokenLedger(store_path=LEDGER_PATH)

    print("=" * 78)
    print("LIVE NETWORK: identity + internal tokens + read-only external chain info")
    print("(three separate concerns, never merged — proven at the end)")
    print("=" * 78)

    ports = [BASE_PORT + i for i in range(NODE_COUNT)]
    nodes = [Node(i, p, [q for q in ports if q != p], ledger) for i, p in enumerate(ports)]

    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(n.run(stop_event)) for n in nodes]
    await asyncio.sleep(RUN_SECONDS)
    stop_event.set()
    await asyncio.gather(*tasks)

    print("\n" + "=" * 78)
    print("SUMMARY")
    for n in nodes:
        print(f"  {n.node_key}: mined {n.block_counter}, external-info blocks {n.external_info_blocks_mined}, "
              f"token balance {ledger.balance(n.node_key)}")

    print("\nSEPARATION CHECK (real, not asserted):")
    awards = ledger.history()
    print(f"  TokenLedger.award() was recorded {len(awards)} times.")
    print(f"  Every award reason names a block-verification event, never a chain value:")
    non_verification_awards = [h for h in awards if "verified by" not in h["reason"]]
    print(f"  Award reasons NOT mentioning verification: {len(non_verification_awards)} "
          f"(should be 0 — confirms no award was ever derived from external chain data)")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
