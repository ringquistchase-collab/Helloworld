#!/usr/bin/env python3
"""
live_helix_network.py
=======================
Runs a real live network of algorithm nodes: 3 independent asyncio TCP
servers on 127.0.0.1, each mining its own blocks on a timer and gossiping
them, AES-256-GCM encrypted, to the other nodes — while it runs, not as a
single request/response.

Every block carries BOTH representations of its hash:
  - binary code:     the raw SHA-256 digest, as hex
  - binary DNA:      the same bytes mirrored to A/C/G/T (dna_binary_codec.py)
  - the complementary (helix) strand of that DNA mirror, using real
    Watson-Crick pairing (A<->T, C<->G)

Each receiving node independently verifies, on every block it gets from
every peer, live:
  1. decode_from_dna(strand) == the hash bytes it was told to expect
  2. the complementary strand really is the bitwise NOT of the same bytes
     (the real double-helix relationship this project has been building
     toward)

This is algorithms running live on the network, not a single-shot demo:
the nodes run concurrently for a fixed real duration, mining and gossiping
on their own schedules, and you can see interleaved output from all three.

SCOPE / SAFETY: everything binds to 127.0.0.1 (localhost) only, so it is
not reachable from outside this machine, and the whole run stops itself
after RUN_SECONDS. It is a bounded local demonstration, not a hosted or
always-on service.
"""

import asyncio
import hashlib
import json
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dna_binary_codec import encode_to_dna, decode_from_dna, complement_strand

BASE_PORT = 9001
NODE_COUNT = 3
RUN_SECONDS = 6.0
MINE_INTERVAL = 1.2

# Shared symmetric key for this demo's gossip encryption. (network_os.py's
# real peers instead derive a unique per-peer key via an X25519 handshake;
# this demo uses one shared AES-256-GCM key so three independent processes
# can decrypt each other's gossip without also re-implementing that
# handshake here — the encryption primitive itself is identical.)
GROUP_KEY = AESGCM.generate_key(bit_length=256)


def encrypt(payload: dict) -> bytes:
    aesgcm = AESGCM(GROUP_KEY)
    nonce = os.urandom(12)
    plaintext = json.dumps(payload).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt(frame: bytes) -> dict:
    aesgcm = AESGCM(GROUP_KEY)
    nonce, ciphertext = frame[:12], frame[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


class HelixNode:
    def __init__(self, node_id: int, port: int, peer_ports: list[int]):
        self.node_id = node_id
        self.port = port
        self.peer_ports = peer_ports
        self.block_counter = 0
        self.blocks_received = 0
        self.verified_ok = 0
        self.verified_failed = 0
        self.server = None

    def log(self, msg: str):
        t = time.strftime("%H:%M:%S")
        print(f"[{t}] node-{self.node_id}:{self.port}  {msg}")

    # ---- server side: accept gossip from peers ----

    async def handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            header = await reader.readexactly(4)
            length = int.from_bytes(header, "big")
            frame = await reader.readexactly(length)
            block = decrypt(frame)
            self.blocks_received += 1

            expected_hash = bytes.fromhex(block["hash_hex"])
            strand = block["dna_strand"]
            complement = block["dna_complement"]

            decoded_from_strand = decode_from_dna(strand)
            decoded_from_complement = decode_from_dna(complement)
            bitwise_not_of_hash = bytes(b ^ 0xFF for b in expected_hash)

            strand_ok = decoded_from_strand == expected_hash
            helix_ok = decoded_from_complement == bitwise_not_of_hash

            if strand_ok and helix_ok:
                self.verified_ok += 1
                self.log(
                    f"<- received block #{block['index']} from node-{block['origin']} "
                    f"(hash {block['hash_hex'][:12]}...)  strand OK  helix-complement OK"
                )
            else:
                self.verified_failed += 1
                self.log(
                    f"<- received block #{block['index']} from node-{block['origin']} "
                    f"VERIFICATION FAILED (strand_ok={strand_ok} helix_ok={helix_ok})"
                )
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    async def start_server(self):
        self.server = await asyncio.start_server(self.handle_conn, "127.0.0.1", self.port)

    # ---- client side: mine and gossip to peers ----

    async def mine_and_gossip(self):
        self.block_counter += 1
        # Real data going into the hash: node identity, counter, wall-clock
        # time, and fresh randomness — not fabricated/templated content.
        raw = f"node={self.node_id}|counter={self.block_counter}|t={time.time()}".encode()
        raw += os.urandom(8)
        digest = hashlib.sha256(raw).digest()

        strand = encode_to_dna(digest)
        complement = complement_strand(strand)

        block = {
            "origin": self.node_id,
            "index": self.block_counter,
            "hash_hex": digest.hex(),
            "dna_strand": strand,
            "dna_complement": complement,
            "timestamp": time.time(),
        }

        self.log(
            f"-> mined block #{self.block_counter} hash={digest.hex()[:12]}... "
            f"strand={strand[:24]}..."
        )

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
                self.log(f"   (peer :{peer_port} not reachable yet)")

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
    ports = [BASE_PORT + i for i in range(NODE_COUNT)]
    nodes = []
    for i, port in enumerate(ports):
        peer_ports = [p for p in ports if p != port]
        nodes.append(HelixNode(node_id=i, port=port, peer_ports=peer_ports))

    print("=" * 78)
    print(f"LIVE NETWORK: {NODE_COUNT} nodes on 127.0.0.1:{ports}, running for "
          f"{RUN_SECONDS:.0f}s in real time")
    print("Each mines its own blocks on a timer and gossips both DNA strands")
    print("(the sequence and its real Watson-Crick complement) to every peer,")
    print("AES-256-GCM encrypted. Every receiving node verifies both strands")
    print("live, independently.")
    print("=" * 78 + "\n")

    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(n.run(stop_event)) for n in nodes]

    await asyncio.sleep(RUN_SECONDS)
    stop_event.set()
    await asyncio.gather(*tasks)

    print("\n" + "=" * 78)
    print("LIVE RUN COMPLETE — summary per node:")
    total_ok, total_failed, total_mined = 0, 0, 0
    for n in nodes:
        print(f"  node-{n.node_id}: mined {n.block_counter} blocks, "
              f"received {n.blocks_received} (verified OK: {n.verified_ok}, "
              f"failed: {n.verified_failed})")
        total_ok += n.verified_ok
        total_failed += n.verified_failed
        total_mined += n.block_counter
    print(f"\n  TOTALS: {total_mined} blocks mined across the network, "
          f"{total_ok} cross-node verifications passed, {total_failed} failed.")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
