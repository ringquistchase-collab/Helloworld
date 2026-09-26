#!/usr/bin/env python3
"""
self_and_research_helix_live.py
=================================
Answers two things with real, executed code:

  1. Does this pull from real DNA research/educational datasets?
     Not until now. This adds a REAL, live fetch of a public reference
     sequence from NCBI (human INS gene mRNA, RefSeq NM_000207 — a standard
     public/educational sequence, not anyone's personal data) and computes
     its REAL base composition and GC content, directly from the actual
     nucleotide letters NCBI returns.

  2. Can this use local/self data to build its own DNA helix and put it on
     the live network?
     Yes — SELF_DATA below is hashed (SHA-256) and mirrored through
     dna_binary_codec.py into its own unique DNA helix (strand + real
     Watson-Crick complement), exactly like every other block in this
     project. That helix is then gossiped, AES-256-GCM encrypted, across a
     live 2-node network and verified on the receiving end.

These two things are then compared side by side — NOT to claim any
biological relationship, but to show, honestly, that they are structurally
different kinds of data:
  - the real NCBI sequence is actual biological nucleotide data (A/C/G/T
    that some organism's real genome/transcript contains)
  - the "self" helix is a digital encoding of arbitrary personal/identity
    bytes into the same 4-letter alphabet — a mirror, not a genome.
Both are real; they are not the same kind of real.

SCOPE / SAFETY: the only outbound request is a read-only fetch of a public
NCBI reference record (same public API the project's research agents
already use); the 2-node exchange binds to 127.0.0.1 only and completes in
one shot. Nothing here is hosted, persistent, or internet-facing.

If you attach a local file to this conversation, replace the SELF_DATA
line below (or pass a path) to fingerprint an actual file of yours instead
of the placeholder text.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from collections import Counter

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dna_binary_codec import encode_to_dna, decode_from_dna, complement_strand, gc_content

PORT_A = 9101
PORT_B = 9102

# ---------------------------------------------------------------------------
# Part 1: REAL public research/educational DNA sequence (NCBI RefSeq)
# ---------------------------------------------------------------------------

def fetch_real_reference_sequence() -> dict:
    """Live fetch of a real, public NCBI RefSeq record — human INS
    (insulin) mRNA, accession NM_000207. This is standard public reference
    data used in biology education/research, not any individual's genome."""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "nucleotide",
        "id": "NM_000207",
        "rettype": "fasta",
        "retmode": "text",
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    fasta_text = resp.text
    lines = fasta_text.strip().splitlines()
    header = lines[0] if lines else "UNKNOWN"
    sequence = "".join(lines[1:]).upper()
    return {"accession": "NM_000207", "header": header, "sequence": sequence}


def analyze_real_sequence(sequence: str) -> dict:
    """Real statistics computed directly on the real nucleotide letters —
    no simulation."""
    counts = Counter(sequence)
    total = len(sequence)
    return {
        "length": total,
        "base_counts": dict(counts),
        "gc_content": (counts.get("G", 0) + counts.get("C", 0)) / total if total else 0.0,
    }


# ---------------------------------------------------------------------------
# Part 2: self/local data -> its own real DNA helix
# ---------------------------------------------------------------------------

SELF_DATA_TEXT = "Chase Allen Ringquist | Bixby, Oklahoma | dna-chain-project"


def build_self_helix(data: bytes) -> dict:
    fingerprint = hashlib.sha256(data).digest()
    strand = encode_to_dna(fingerprint)
    complement = complement_strand(strand)
    complement_bytes = decode_from_dna(complement)
    bitwise_not = bytes(b ^ 0xFF for b in fingerprint)
    return {
        "source_bytes_len": len(data),
        "fingerprint_hex": fingerprint.hex(),
        "strand": strand,
        "complement": complement,
        "helix_verified": complement_bytes == bitwise_not,
        "gc_content": gc_content(strand),
    }


# ---------------------------------------------------------------------------
# Part 3: live 2-node network — gossip the self-helix, encrypted, verified
# ---------------------------------------------------------------------------

GROUP_KEY = AESGCM.generate_key(bit_length=256)


def encrypt(payload: dict) -> bytes:
    aesgcm = AESGCM(GROUP_KEY)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, json.dumps(payload).encode(), None)
    return nonce + ciphertext


def decrypt(frame: bytes) -> dict:
    aesgcm = AESGCM(GROUP_KEY)
    plaintext = aesgcm.decrypt(frame[:12], frame[12:], None)
    return json.loads(plaintext.decode())


async def receiver(port: int, result: dict, ready: asyncio.Event):
    async def handle(reader, writer):
        header = await reader.readexactly(4)
        length = int.from_bytes(header, "big")
        frame = await reader.readexactly(length)
        block = decrypt(frame)
        expected = bytes.fromhex(block["fingerprint_hex"])
        decoded_strand = decode_from_dna(block["strand"])
        decoded_complement = decode_from_dna(block["complement"])
        bitwise_not = bytes(b ^ 0xFF for b in expected)
        result["received"] = block
        result["strand_verified"] = decoded_strand == expected
        result["helix_verified"] = decoded_complement == bitwise_not
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    ready.set()
    async with server:
        await server.serve_forever()


async def sender(port: int, block: dict, ready: asyncio.Event):
    await ready.wait()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    frame = encrypt(block)
    writer.write(len(frame).to_bytes(4, "big") + frame)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def run_live_exchange(self_helix: dict):
    ready = asyncio.Event()
    result = {}
    recv_task = asyncio.create_task(receiver(PORT_B, result, ready))
    await sender(PORT_B, {
        "fingerprint_hex": self_helix["fingerprint_hex"],
        "strand": self_helix["strand"],
        "complement": self_helix["complement"],
    }, ready)
    await asyncio.sleep(0.5)  # let the handler run
    recv_task.cancel()
    try:
        await recv_task
    except asyncio.CancelledError:
        pass
    return result


def main():
    print("=" * 78)
    print("PART 1 — REAL public research/educational DNA data (NCBI RefSeq)")
    print("=" * 78)
    try:
        ref = fetch_real_reference_sequence()
        stats = analyze_real_sequence(ref["sequence"])
        print(f"Accession: {ref['accession']}")
        print(f"Header:    {ref['header'][:90]}")
        print(f"Length:    {stats['length']} real nucleotide bases")
        print(f"Base counts: {stats['base_counts']}")
        print(f"Real GC content: {stats['gc_content']:.4f}")
    except Exception as e:
        print(f"FAILED to fetch real NCBI data: {e}")
        stats = None

    print("\n" + "=" * 78)
    print("PART 2 — YOUR self/local data, mirrored into its own DNA helix")
    print("=" * 78)
    self_bytes = SELF_DATA_TEXT.encode("utf-8")
    print(f"Source text: {SELF_DATA_TEXT!r}")
    self_helix = build_self_helix(self_bytes)
    print(f"SHA-256 fingerprint: {self_helix['fingerprint_hex']}")
    print(f"DNA strand:          {self_helix['strand']}")
    print(f"Complement strand:   {self_helix['complement']}")
    print(f"Helix relationship verified (complement == bitwise NOT): {self_helix['helix_verified']}")
    print(f"Strand GC content:   {self_helix['gc_content']:.4f}")

    print("\n" + "=" * 78)
    print("PART 3 — putting YOUR helix on a live 2-node encrypted network")
    print("=" * 78)
    result = asyncio.run(run_live_exchange(self_helix))
    if result:
        print(f"Node B received the block over a real TCP socket, decrypted it")
        print(f"(AES-256-GCM), and independently verified:")
        print(f"  strand decodes to exact fingerprint: {result['strand_verified']}")
        print(f"  complement is exact bitwise-NOT (real helix property): {result['helix_verified']}")
    else:
        print("Live exchange did not complete.")

    print("\n" + "=" * 78)
    print("HONEST COMPARISON — these are two different kinds of real data:")
    if stats:
        print(f"  NCBI reference sequence: {stats['length']} REAL biological bases, "
              f"GC={stats['gc_content']:.4f} — an actual gene transcript.")
    print(f"  Your self-data helix:   {len(self_helix['strand'])} bases, "
          f"GC={self_helix['gc_content']:.4f} — a digital mirror of a SHA-256")
    print(f"  hash of the text above. It is not a biological sequence, and its")
    print(f"  GC content is an artifact of the hash, not of any real genetics.")
    print("=" * 78)


if __name__ == "__main__":
    main()
