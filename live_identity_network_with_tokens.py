#!/usr/bin/env python3
"""
live_identity_network_with_tokens.py
======================================
live_identity_network.py + token_ledger.py wired together for real: every
time a node's block is verified by a peer (identity match + helix check),
that origin node is awarded a real token via TokenLedger.award(), live,
during the run — not computed afterward from logs.

Answers: does the binary/DNA identity system connect to (a) other
programming languages and (b) tokens, on the live network?
  (a) dna_codec.js (separate file, run separately) computes the exact same
      identity fingerprint/strand independently in Node.js — proven in the
      previous step, not repeated here to keep this run fast.
  (b) yes — this run awards real tokens, live, and prints a real
      leaderboard at the end, persisted to disk.

SCOPE / SAFETY: all nodes bind to 127.0.0.1 (localhost) only and the run
stops itself after RUN_SECONDS. "Tokens" here are non-monetary contribution
points (see token_ledger.py) — not a currency, not tradable, not a wallet.
Nothing is hosted, persistent-running, or reachable from outside this
machine (the only persisted artifact is a local ledger JSON you can delete).
"""

import asyncio
import hashlib
import json
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dna_binary_codec import encode_to_dna, decode_from_dna, complement_strand
from token_ledger import TokenLedger

IDENTITY_TEXT = "Chase Allen Ringquist | Bixby, Oklahoma | dna-chain-project"
IDENTITY_FINGERPRINT = hashlib.sha256(IDENTITY_TEXT.encode("utf-8")).digest()
IDENTITY_STRAND = encode_to_dna(IDENTITY_FINGERPRINT)

BASE_PORT = 9301
NODE_COUNT = 3
RUN_SECONDS = 7.0
MINE_INTERVAL = 1.3

GROUP_KEY = AESGCM.generate_key(bit_length=256)
LEDGER_PATH = os.path.join(os.path.dirname(__file__), "network_token_ledger.json")


def encrypt(payload: dict) -> bytes:
    aesgcm = AESGCM(GROUP_KEY)
    nonce = os.urandom(12)
    return nonce + aesgcm.encrypt(nonce, json.dumps(payload).encode(), None)


def decrypt(frame: bytes) -> dict:
    aesgcm = AESGCM(GROUP_KEY)
    plaintext = aesgcm.decrypt(frame[:12], frame[12:], None)
    return json.loads(plaintext.decode())


class TokenEarningNode:
    def __init__(self, node_id: int, port: int, peer_ports: list[int], ledger: TokenLedger):
        self.node_id = node_id
        self.node_key = f"node-{node_id}"
        self.port = port
        self.peer_ports = peer_ports
        self.ledger = ledger
        self.block_counter = 0
        self.blocks_received = 0
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
            if fully_verified:
                new_balance = self.ledger.award(
                    origin_key, 1, f"block #{block['index']} verified by {self.node_key}"
                )
                self.log(
                    f"<- block #{block['index']} from {origin_key} VERIFIED -> "
                    f"awarded 1 token to {origin_key} (balance now {new_balance})"
                )
            else:
                self.log(f"<- block #{block['index']} from {origin_key} FAILED verification, no token")
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    async def start_server(self):
        self.server = await asyncio.start_server(self.handle_conn, "127.0.0.1", self.port)

    async def mine_and_gossip(self):
        self.block_counter += 1
        raw = (
            IDENTITY_FINGERPRINT
            + f"|node={self.node_id}|counter={self.block_counter}|t={time.time()}".encode()
            + os.urandom(8)
        )
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
        }
        self.log(f"-> mined block #{self.block_counter} hash={digest.hex()[:12]}...")

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
    # Fresh ledger each run so token counts reflect only this run's real
    # activity (delete any stale file from a previous run first).
    if os.path.exists(LEDGER_PATH):
        os.remove(LEDGER_PATH)
    ledger = TokenLedger(store_path=LEDGER_PATH)

    print("=" * 78)
    print(f"LIVE NETWORK WITH REAL TOKEN AWARDING (NOT a cryptocurrency)")
    print(f"Identity strand carried by every node: {IDENTITY_STRAND[:40]}...")
    print("=" * 78)

    ports = [BASE_PORT + i for i in range(NODE_COUNT)]
    nodes = [
        TokenEarningNode(node_id=i, port=p, peer_ports=[q for q in ports if q != p], ledger=ledger)
        for i, p in enumerate(ports)
    ]

    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(n.run(stop_event)) for n in nodes]
    await asyncio.sleep(RUN_SECONDS)
    stop_event.set()
    await asyncio.gather(*tasks)

    print("\n" + "=" * 78)
    print("REAL TOKEN LEDGER — persisted to disk, awarded live during the run:")
    for node_key, balance in ledger.leaderboard():
        print(f"  {node_key}: {balance} tokens")
    print(f"\n  Ledger file: {LEDGER_PATH}")
    print(f"  Total award events recorded: {len(ledger.history())}")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
