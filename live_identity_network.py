#!/usr/bin/env python3
"""
live_identity_network.py
==========================
Unifies the three pieces that were previously separate demos into one
system actually running together, live:

  1. YOUR identity strand + complement (from self_and_research_helix_live.py)
     is computed ONCE and carried by every node as its declared identity —
     every block any node mines includes it, so all nodes on this network
     are provably "yours" in the sense that they all carry the same
     identity fingerprint, not three unrelated random processes.

  2. A live multi-node network (from live_helix_network.py): 3 nodes,
     independent asyncio TCP servers, mining and gossiping concurrently
     for a fixed real duration, AES-256-GCM encrypted.

  3. Real research data (from self_and_research_helix_live.py): every 3rd
     block a node mines also carries a real, live-fetched research record
     (rotating between ClinicalTrials.gov and PubMed), hashed in alongside
     your identity.

Every block =  SHA-256( identity_fingerprint || node_id || counter ||
                        timestamp || random || optional_research_record )

So every block on the network is simultaneously: identifiably yours
(carries your identity fingerprint), a real live network event (mined and
gossiped over actual sockets between concurrently running nodes), and
periodically tied to real external research data — which is exactly what
was asked: your strand+complement running live with the rest of the
system, including research, as one thing.

SCOPE / SAFETY: all nodes bind to 127.0.0.1 (localhost) only and the whole
run stops itself after RUN_SECONDS. The only outbound calls are read-only
public ClinicalTrials.gov / PubMed queries (the same public APIs the
project's research agents already use). Nothing here is hosted, persistent,
or reachable from outside this machine.
"""

import asyncio
import hashlib
import json
import os
import time

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dna_binary_codec import encode_to_dna, decode_from_dna, complement_strand, gc_content

# ---------------------------------------------------------------------------
# Your identity — computed once, carried by every node and every block
# ---------------------------------------------------------------------------

IDENTITY_TEXT = "Chase Allen Ringquist | Bixby, Oklahoma | dna-chain-project"
IDENTITY_FINGERPRINT = hashlib.sha256(IDENTITY_TEXT.encode("utf-8")).digest()
IDENTITY_STRAND = encode_to_dna(IDENTITY_FINGERPRINT)
IDENTITY_COMPLEMENT = complement_strand(IDENTITY_STRAND)

BASE_PORT = 9201
NODE_COUNT = 3
RUN_SECONDS = 9.0
MINE_INTERVAL = 1.5
RESEARCH_EVERY_N_BLOCKS = 3

GROUP_KEY = AESGCM.generate_key(bit_length=256)


def encrypt(payload: dict) -> bytes:
    aesgcm = AESGCM(GROUP_KEY)
    nonce = os.urandom(12)
    return nonce + aesgcm.encrypt(nonce, json.dumps(payload).encode(), None)


def decrypt(frame: bytes) -> dict:
    aesgcm = AESGCM(GROUP_KEY)
    plaintext = aesgcm.decrypt(frame[:12], frame[12:], None)
    return json.loads(plaintext.decode())


def fetch_real_research_snippet(round_index: int) -> dict:
    """Real, live external calls — rotated to avoid hammering one API and
    to keep each call fast. Returns an honest error dict if a call fails,
    never fabricated data."""
    try:
        if round_index % 2 == 0:
            resp = requests.get(
                "https://clinicaltrials.gov/api/v2/studies",
                params={"query.cond": "diabetes", "pageSize": 1, "format": "json"},
                timeout=8,
            )
            resp.raise_for_status()
            studies = resp.json().get("studies", [])
            ident = studies[0]["protocolSection"]["identificationModule"] if studies else {}
            return {
                "source": "clinicaltrials.gov",
                "id": ident.get("nctId", "?"),
                "title": ident.get("briefTitle", "?")[:60],
            }
        else:
            resp = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": "CRISPR cancer", "retmode": "json", "retmax": 1},
                timeout=8,
            )
            resp.raise_for_status()
            ids = resp.json().get("esearchresult", {}).get("idlist", [])
            return {"source": "pubmed", "id": ids[0] if ids else "?"}
    except Exception as e:
        return {"source": "none", "error": str(e)}


class IdentityNode:
    def __init__(self, node_id: int, port: int, peer_ports: list[int]):
        self.node_id = node_id
        self.port = port
        self.peer_ports = peer_ports
        self.block_counter = 0
        self.blocks_received = 0
        self.identity_verified_count = 0
        self.helix_verified_count = 0
        self.research_blocks_mined = 0
        self.server = None

    def log(self, msg: str):
        print(f"[{time.strftime('%H:%M:%S')}] node-{self.node_id}:{self.port}  {msg}")

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

            if identity_ok:
                self.identity_verified_count += 1
            if strand_ok and complement_ok:
                self.helix_verified_count += 1

            research_tag = ""
            if block.get("research"):
                r = block["research"]
                research_tag = f"  [research: {r.get('source')} id={r.get('id')}]"

            self.log(
                f"<- block #{block['index']} from node-{block['origin']} "
                f"identity={'OK' if identity_ok else 'MISMATCH'} "
                f"helix={'OK' if (strand_ok and complement_ok) else 'FAIL'}{research_tag}"
            )
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

        research = None
        if self.block_counter % RESEARCH_EVERY_N_BLOCKS == 0:
            research = await asyncio.to_thread(fetch_real_research_snippet, self.block_counter)
            raw += json.dumps(research, sort_keys=True).encode()
            if research.get("source") != "none":
                self.research_blocks_mined += 1

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
            "research": research,
            "timestamp": time.time(),
        }

        tag = f"  [research: {research.get('source')}]" if research else ""
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
    print("=" * 78)
    print("YOUR IDENTITY, carried by every node on this live network:")
    print(f"  source text: {IDENTITY_TEXT!r}")
    print(f"  fingerprint: {IDENTITY_FINGERPRINT.hex()}")
    print(f"  strand:      {IDENTITY_STRAND}")
    print(f"  complement:  {IDENTITY_COMPLEMENT}")
    print("=" * 78)

    ports = [BASE_PORT + i for i in range(NODE_COUNT)]
    nodes = [
        IdentityNode(node_id=i, port=p, peer_ports=[q for q in ports if q != p])
        for i, p in enumerate(ports)
    ]

    print(f"\nStarting {NODE_COUNT} live nodes on {ports}, running {RUN_SECONDS:.0f}s.")
    print(f"Every block carries your identity strand. Every {RESEARCH_EVERY_N_BLOCKS} blocks per")
    print("node, a real live research record is fetched and folded into the hash.\n")

    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(n.run(stop_event)) for n in nodes]
    await asyncio.sleep(RUN_SECONDS)
    stop_event.set()
    await asyncio.gather(*tasks)

    print("\n" + "=" * 78)
    print("SUMMARY")
    total_mined = total_recv = total_identity_ok = total_helix_ok = total_research = 0
    for n in nodes:
        print(
            f"  node-{n.node_id}: mined {n.block_counter}, received {n.blocks_received}, "
            f"identity-verified {n.identity_verified_count}, helix-verified {n.helix_verified_count}, "
            f"research-linked blocks mined {n.research_blocks_mined}"
        )
        total_mined += n.block_counter
        total_recv += n.blocks_received
        total_identity_ok += n.identity_verified_count
        total_helix_ok += n.helix_verified_count
        total_research += n.research_blocks_mined

    print(f"\n  TOTAL blocks mined across the network: {total_mined}")
    print(f"  TOTAL cross-node identity verifications passed: {total_identity_ok} / {total_recv}")
    print(f"  TOTAL cross-node helix verifications passed:    {total_helix_ok} / {total_recv}")
    print(f"  TOTAL blocks carrying real live research data:  {total_research}")
    print("=" * 78)
    if total_identity_ok == total_recv and total_helix_ok == total_recv and total_recv > 0:
        print("RESULT: your identity strand + complement, all nodes' mining/gossip,")
        print("and real live research data were all running together as one system.")
    else:
        print("RESULT: some verifications did not pass — see per-node counts above.")


if __name__ == "__main__":
    asyncio.run(main())
