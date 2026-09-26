"""
digital_dna.py
================
Persistent peer identity for a research node, PLUS a "digital DNA"
chain-fingerprint / mutation / live-signal engine layered onto the
same identity file.

MERGE NOTE: this file used to hold only the simple peer-identity piece
(seed_label/salt/node_id) that network_os.py's NetworkNode depends on
directly (`self.node_id = dna.node_id`) -- and that run_network_peer.py,
run_integrated_cycle.py, run_research_query.py, run_listening_explorer.py
(and the live peer listener process) already use. A separately-pasted,
richer "digital DNA" module had a DigitalDNA class with NO node_id at
all and a different constructor -- incompatible with every one of
those. This merges the two: seed_label/dna_path/salt/node_id/to_dict/
__repr__ behave EXACTLY as before (every existing caller needs zero
code changes), and the fingerprint/mutation/live-signal/messaging
capability is added on top, persisted into the SAME dna_path file by
extending its schema. Old identity files (just seed_label/salt/
created_at) load fine -- missing strand/history/blocks default to
"nothing has happened yet", no migration step required.

IMPORTANT SCOPE NOTE
---------------------
This does NOT read, infer, or represent real biological/genetic DNA.
It is a deterministic identity fingerprint built entirely from your
existing chain data (block content, chain id, DAS score, timestamp)
-- i.e. it is a "digital DNA" in the metaphorical sense (blocks =
genes, chains = chromosomes, DAS score = expression level). Think of
it as a cryptographic identity strand, not a genome.

What it does
------------
1. IDENTITY      - node_id derived from (seed_label, salt) -- UNCHANGED,
                    this is what NetworkNode uses for peer handshakes.
2. FINGERPRINT   - build_fingerprint() builds a deterministic 256-bit
                    binary strand from loaded chain block data.
3. MUTATION       - each build_fingerprint() call with new/changed
                    blocks derives a new strand and records the diff
                    (Hamming distance) as a MutationEvent.
4. SEND/RECEIVE   - derives a symmetric keystream from the current
                    strand and uses it to encode outgoing messages /
                    decode incoming ones (HMAC-based stream cipher).
5. LIVE SIGNALS   - add_live_signal() folds one real-time behavioral
                    event into the strand as an O(1) hash-chain step
                    -- no full rebuild needed.

CONSENT GATE (do not remove): add_live_signal() only accepts a source
that is both (a) listed in ALLOWED_SOURCES below and (b) called with
consent_verified=True. This module never captures raw camera frames,
raw keystrokes, or raw account data itself -- the caller must already
have gone through a real permission prompt and pass in only a derived
feature_hash, never the raw signal.

Honest security note: the cipher here is a simple HMAC-SHA256
keystream cipher (deterministic, symmetric, no external deps). It's
solid for tamper-evidence and functional encode/decode inside your own
pipeline, but it has NOT been audited and is not a substitute for a
vetted library (e.g. `cryptography`'s AES-GCM) for real third-party
sensitive traffic over untrusted channels.

Usage
-----
    from digital_dna import DigitalDNA

    dna = DigitalDNA(seed_label="chase-allen-ringquist-dna-chain",
                      dna_path=r"C:\\Users\\odaat\\chase-allen-ringquist-dna-chain.dna.json")
    print(dna.node_id)   # unchanged -- NetworkNode etc. still just work

    dna.load_chain_data("chain_export.json")
    event = dna.build_fingerprint()   # persisted into dna_path automatically

    key = dna.derive_key()
    ciphertext = dna.encode_message("status update to a node")
    plaintext  = dna.decode_message(ciphertext)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict

from dna_binary_codec import encode_to_dna, decode_from_dna, gc_content


STRAND_BITS = 256
STRAND_BYTES = STRAND_BITS // 8

# Sources this module will accept a live signal from, and the consent
# mechanism each one REQUIRES upstream before calling add_live_signal().
# This is documentation + a validation gate, not an enforcement engine --
# it can't verify your caller actually did the consent step, only that
# the caller is asserting it did (consent_verified=True) for a
# recognized source. Keep this list honest; add new sources here rather
# than bypassing it.
ALLOWED_SOURCES = {
    "keyboard_cadence": "local opt-in app, typing rhythm only — never literal keystrokes",
    "camera_liveness":  "OS camera permission prompt, one-shot capture — never continuous recording",
    "work_login":       "OAuth token the person granted to their own account",
    "school_login":     "OAuth token the person granted to their own account",
    "social_media":     "official platform API + the person's own access token",
    "habit_summary":    "explicit export/import from a health or activity app",
    "manual_entry":     "the person typing/confirming a value themselves",
    "ai_insight":        "system-generated — derived analysis of already-consented blocks, not new personal capture",
    "maxwell_telemetry": "system-generated — intermediate field-oscillation signal from your own Maxwell-chain generator, not personal capture",
    "research_query":    "the person explicitly submitting a condition/biomarker search — never a diagnosis inferred automatically",
    "research_update":   "system-generated — periodic re-check of an existing consented query, not a new query the person didn't ask for",
    "topic_expansion":   "system-generated — exploring a topic related to one you explicitly queried; disclosed and capped, not open-ended browsing",
    "ai_interpretation": "system-generated — an LLM's summary of already-found real research records, clearly separate from the primary source data it summarizes",
    "video_generation": "system-generated — an AI video interpretation of real network activity data (block counts, MOS scores), never personal or biological content",
    "research_match":    "system-generated — re-queries a condition/biomarker term YOU explicitly registered against public ClinicalTrials.gov data; no patient identity involved",
    "eeg_telemetry":     "medical-grade EEG device with informed consent — only aggregate statistics (variance, band power) ever leave this source, never raw waveforms or anything claimed to depict brain state",
    "hormone_telemetry": "lab hormone panel with informed consent — only aggregate statistics ever leave this source, never raw values or anything claimed to depict endocrine state",
    "network_mining": "system-generated — this node's own block-mining events on its local network (hash of a block it just mined), not new personal capture; lets the identity's signal history grow with real network activity",
}


@dataclass
class MutationEvent:
    timestamp: float
    block_count: int
    hamming_distance: int
    mutation_rate: float          # hamming_distance / STRAND_BITS
    strand_hex: str


def _canonical_block_string(block: dict) -> str:
    """
    Deterministic string form of one chain block, independent of key
    order. Expected (flexible) fields — anything missing is skipped:
        block_id, chain_id, das_score, timestamp, content / content_hash
    """
    parts = []
    for key in ("block_id", "chain_id", "das_score", "timestamp"):
        if key in block:
            parts.append(f"{key}={block[key]}")
    for content_key in ("content", "content_hash", "text", "payload"):
        if content_key in block:
            parts.append(f"{content_key}={block[content_key]}")
            break
    return "|".join(parts)


def load_blocks(path: str) -> list[dict]:
    """
    Loads block data from a JSON file for fingerprinting. Accepts either:
      - a top-level list of block dicts, or
      - a dict with a "blocks" key holding that list.
    """
    with open(path, "r") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "blocks" in raw:
        return raw["blocks"]
    if isinstance(raw, list):
        return raw
    raise ValueError("Unrecognized chain data format — expected a list of blocks or {'blocks': [...]}")


class DigitalDNA:
    """Stable, disk-persisted peer identity, extended with a chain-
    fingerprint / mutation / live-signal engine persisted into the
    same file.

    node_id is a short hex digest derived from (seed_label, salt) --
    UNCHANGED behavior, still what NetworkNode uses for peer handshakes.
    strand/history/self.blocks are the fingerprint engine's state,
    persisted alongside salt/created_at in the same dna_path JSON file.
    (self.blocks here means fingerprint input / live-signal audit log --
    NOT the mined chain blocks tracked elsewhere in this project, e.g.
    by MaxwellChainAgent.)
    """

    def __init__(self, seed_label: str = "chase-allen-ringquist-dna-chain", dna_path: str | None = None):
        self.seed_label = seed_label
        self.dna_path = dna_path or f"{seed_label}.dna.json"

        self.strand: bytes = b"\x00" * STRAND_BYTES
        self.history: list[MutationEvent] = []
        self.blocks: list[dict] = []

        self.salt, self.created_at = self._load_or_create()
        self.node_id = self._derive_node_id()

    def _load_or_create(self) -> tuple[str, float]:
        if os.path.exists(self.dna_path):
            with open(self.dna_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "strand_hex" in data:
                self.strand = bytes.fromhex(data["strand_hex"])
            self.history = [MutationEvent(**e) for e in data.get("history", [])]
            self.blocks = data.get("blocks", [])
            return data["salt"], data["created_at"]

        salt = uuid.uuid4().hex
        created_at = time.time()
        self._write(salt, created_at)
        return salt, created_at

    def _derive_node_id(self) -> str:
        digest = hashlib.sha256(f"{self.seed_label}:{self.salt}".encode("utf-8")).hexdigest()
        return digest[:16]

    def to_dict(self) -> dict:
        return {
            "seed_label": self.seed_label,
            "node_id": self.node_id,
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        return f"DigitalDNA(node_id={self.node_id!r}, seed_label={self.seed_label!r})"

    # ---- persistence (same file as identity; schema extended) ----
    def _write(self, salt: str | None = None, created_at: float | None = None) -> None:
        state = {
            "seed_label": self.seed_label,
            "salt": salt if salt is not None else self.salt,
            "created_at": created_at if created_at is not None else self.created_at,
            "strand_hex": self.strand.hex(),
            "history": [asdict(e) for e in self.history],
            "blocks": self.blocks,
        }
        tmp_path = self.dna_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, self.dna_path)

    def _autosave(self) -> None:
        self._write()

    # ---- chain-fingerprint data ingestion ----
    def load_chain_data(self, path: str) -> None:
        self.blocks = load_blocks(path)

    def load_blocks_direct(self, blocks: list[dict]) -> None:
        self.blocks = blocks

    # ---- fingerprint construction ----
    def _das_weighted_hash(self, block: dict) -> bytes:
        """
        Hash one block, then let its DAS score (0.0-6.0 in this
        project's scoring system) control how strongly it 'expresses'
        into the strand — higher-quality blocks contribute more bit-
        weight, mirroring gene expression level.
        """
        base = hashlib.sha256(_canonical_block_string(block).encode()).digest()
        das = float(block.get("das_score", 3.0))
        weight = max(0.05, min(1.0, das / 6.0))   # normalize 0-6 -> 0.05-1.0
        return bytes(int(b * weight) % 256 for b in base)

    def build_fingerprint(self) -> MutationEvent:
        if not self.blocks:
            raise ValueError("No block data loaded — call load_chain_data() first.")

        acc = bytearray(STRAND_BYTES)
        for block in sorted(self.blocks, key=lambda b: (b.get("chain_id", ""), b.get("block_id", ""))):
            h = self._das_weighted_hash(block)
            for i in range(STRAND_BYTES):
                acc[i] ^= h[i % len(h)]

        binder = hashlib.sha256((self.seed_label + acc.hex()).encode()).digest()
        new_strand = bytes(a ^ b for a, b in zip(acc, binder * (STRAND_BYTES // len(binder) + 1)))
        new_strand = new_strand[:STRAND_BYTES]

        old_strand = self.strand
        hamming = sum(bin(a ^ b).count("1") for a, b in zip(old_strand, new_strand))
        event = MutationEvent(
            timestamp=time.time(),
            block_count=len(self.blocks),
            hamming_distance=hamming,
            mutation_rate=round(hamming / STRAND_BITS, 4),
            strand_hex=new_strand.hex(),
        )
        self.strand = new_strand
        self.history.append(event)
        self._autosave()
        return event

    # ---- real-time / streaming signals ----
    def add_live_signal(
        self,
        source: str,
        feature_hash_hex: str,
        confidence: float = 1.0,
        consent_verified: bool = False,
        timestamp: float | None = None,
    ) -> MutationEvent:
        """
        Fold ONE real-time behavioral event into the strand as a hash-
        chain step: next_strand = HMAC(current_strand, seed + event).
        O(1) — no rebuild of the full block history.

        source           : must be a key in ALLOWED_SOURCES.
        feature_hash_hex : a hex-encoded hash/feature vector the CALLER
                            already derived from the raw signal. Never
                            pass raw camera frames, raw keystrokes, or
                            raw account data here.
        confidence        : 0.0-1.0, how strong/reliable this sample is.
        consent_verified  : must be True. Asserts a real permission/
                             consent step already happened upstream.
        """
        if source not in ALLOWED_SOURCES:
            raise ValueError(
                f"Unrecognized source '{source}'. Add it to ALLOWED_SOURCES "
                f"with its required consent mechanism before using it."
            )
        if not consent_verified:
            raise PermissionError(
                f"add_live_signal() refused: consent_verified=False for source "
                f"'{source}'. Required consent: {ALLOWED_SOURCES[source]}. "
                f"Complete that step in your capture code first."
            )

        confidence = max(0.0, min(1.0, float(confidence)))
        ts = timestamp if timestamp is not None else time.time()
        event_str = f"{source}|{feature_hash_hex}|{confidence}|{ts}"

        old_strand = self.strand
        new_strand = hmac.new(
            old_strand, (self.seed_label + "|" + event_str).encode(), hashlib.sha256
        ).digest()

        hamming = sum(bin(a ^ b).count("1") for a, b in zip(old_strand, new_strand))
        event = MutationEvent(
            timestamp=ts,
            block_count=len(self.blocks) + 1,
            hamming_distance=hamming,
            mutation_rate=round(hamming / STRAND_BITS, 4),
            strand_hex=new_strand.hex(),
        )

        self.strand = new_strand
        self.history.append(event)
        self.blocks.append({
            "block_id": f"live-{len(self.blocks):06d}",
            "source": source,
            "feature_hash": feature_hash_hex,
            "confidence": confidence,
            "timestamp": ts,
        })
        self._autosave()
        return event

    def audit_trail(self, source: str | None = None) -> list[dict]:
        """Every live signal folded in so far (feature hashes only), optionally filtered by source."""
        return [b for b in self.blocks if "source" in b and (source is None or b["source"] == source)]

    # ---- keying / messaging ----
    def derive_key(self, purpose: str = "send-receive") -> bytes:
        """HKDF-style key derivation from the current strand."""
        return hmac.new(self.strand, purpose.encode(), hashlib.sha256).digest()

    def _keystream(self, key: bytes, length: int) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            out += hmac.new(key, counter.to_bytes(4, "big"), hashlib.sha256).digest()
            counter += 1
        return bytes(out[:length])

    def encode_message(self, message: str, purpose: str = "send-receive") -> str:
        key = self.derive_key(purpose)
        data = message.encode()
        ks = self._keystream(key, len(data))
        cipher = bytes(a ^ b for a, b in zip(data, ks))
        return cipher.hex()

    def decode_message(self, hex_ciphertext: str, purpose: str = "send-receive") -> str:
        key = self.derive_key(purpose)
        cipher = bytes.fromhex(hex_ciphertext)
        ks = self._keystream(key, len(cipher))
        data = bytes(a ^ b for a, b in zip(cipher, ks))
        return data.decode(errors="replace")

    # ---- binary DNA views ----
    def as_binary_string(self) -> str:
        return "".join(f"{byte:08b}" for byte in self.strand)

    def as_hex(self) -> str:
        return self.strand.hex()

    # ---- DNA-letter mirror of the strand (dna_binary_codec.py) ----
    # Additive only: these expose the SAME strand bytes as as_hex(), just
    # mirrored to an A/C/G/T alphabet via dna_binary_codec.encode_to_dna().
    # This is an exact, reversible ENCODING of the strand bytes, not a
    # biological claim (see dna_binary_codec.py's own docstring). Nothing
    # here changes the strand, the consent gate, ALLOWED_SOURCES, node_id,
    # or the persisted state format.
    def as_dna(self) -> str:
        """The current 256-bit strand as A/C/G/T letters. Exact inverse of
        the hex form: decode_from_dna(as_dna()) == bytes.fromhex(as_hex())."""
        return encode_to_dna(self.strand)

    def dna_report(self) -> dict:
        """Both representations of the current strand, plus GC content of
        the letter form — computed once, for callers that want both without
        recomputing."""
        dna = encode_to_dna(self.strand)
        return {
            "node_id": self.node_id,
            "strand_hex": self.strand.hex(),
            "strand_dna": dna,
            "dna_gc_content": gc_content(dna),
            "dna_base_count": len(dna),
        }

    @staticmethod
    def verify_dna_mirror_matches_hex(strand_hex: str, strand_dna: str) -> bool:
        """For a peer (any OS/language) that receives both forms: confirm the
        DNA-letter form decodes back to the exact bytes of the hex form."""
        return decode_from_dna(strand_dna) == bytes.fromhex(strand_hex)


# ----------------------------------------------------------------- #
# Demo with synthetic sample data (stand-in for a real chain export).
# Uses a tempdir identity file so running this directly never creates
# a stray identity file in whatever directory you run it from.
# ----------------------------------------------------------------- #

if __name__ == "__main__":
    import random
    import tempfile

    random.seed(7)
    sample_blocks = [
        {
            "block_id": f"blk-{i:04d}",
            "chain_id": f"chain-{i % 19:02d}",
            "das_score": round(random.uniform(2.0, 5.5), 2),
            "timestamp": 1_725_000_000 + i * 3600,
            "content_hash": hashlib.sha256(f"sample-content-{i}".encode()).hexdigest()[:16],
        }
        for i in range(399)
    ]

    with tempfile.TemporaryDirectory() as tmp:
        dna = DigitalDNA(seed_label="digital-dna-demo", dna_path=os.path.join(tmp, "demo.dna.json"))
        print(f"node_id (unchanged identity behavior): {dna.node_id}")

        dna.load_blocks_direct(sample_blocks)
        event1 = dna.build_fingerprint()
        print("\n=== Initial strand ===")
        print("hex   :", dna.as_hex())
        print("binary:", dna.as_binary_string()[:64], "...")
        print("event :", event1)

        more_blocks = sample_blocks + [
            {
                "block_id": f"blk-{i:04d}",
                "chain_id": f"chain-{i % 19:02d}",
                "das_score": round(random.uniform(2.0, 5.5), 2),
                "timestamp": 1_725_000_000 + i * 3600,
                "content_hash": hashlib.sha256(f"sample-content-{i}".encode()).hexdigest()[:16],
            }
            for i in range(399, 420)
        ]
        dna.load_blocks_direct(more_blocks)
        event2 = dna.build_fingerprint()
        print("\n=== After mining 20 new blocks ===")
        print("hex   :", dna.as_hex())
        print(f"mutation rate: {event2.mutation_rate*100:.1f}% of strand changed "
              f"({event2.hamming_distance}/{STRAND_BITS} bits)")

        print("\n=== Send/receive demo ===")
        msg = "node-7: voice sovereignty handshake ok"
        ct = dna.encode_message(msg)
        pt = dna.decode_message(ct)
        print("plaintext :", msg)
        print("ciphertext:", ct)
        print("decoded   :", pt)
        assert pt == msg

        print("\n=== Live signal demo (consent-gated streaming updates) ===")
        strand_before = dna.as_hex()

        login_hash = hashlib.sha256(b"work-login-event-example").hexdigest()
        ev1 = dna.add_live_signal("work_login", login_hash, confidence=0.95, consent_verified=True)
        print(f"work_login   -> mutation {ev1.mutation_rate*100:.1f}% ({ev1.hamming_distance}/{STRAND_BITS} bits)")

        cadence_hash = hashlib.sha256(b"typing-rhythm-feature-vector").hexdigest()
        ev2 = dna.add_live_signal("keyboard_cadence", cadence_hash, confidence=0.7, consent_verified=True)
        print(f"keyboard_cadence -> mutation {ev2.mutation_rate*100:.1f}% ({ev2.hamming_distance}/{STRAND_BITS} bits)")

        print("strand before live signals:", strand_before)
        print("strand after  live signals:", dna.as_hex())
        print("audit trail:", dna.audit_trail())

        try:
            dna.add_live_signal("camera_liveness", "deadbeef", consent_verified=False)
        except PermissionError as e:
            print("\nconsent gate fired as expected:", e)

        try:
            dna.add_live_signal("raw_video_stream", "deadbeef", consent_verified=True)
        except ValueError as e:
            print("unknown-source gate fired as expected:", e)

        print("\n=== Reload from disk: identity + strand + history survive a restart ===")
        dna2 = DigitalDNA(seed_label="digital-dna-demo", dna_path=os.path.join(tmp, "demo.dna.json"))
        print(f"node_id matches:  {dna2.node_id == dna.node_id}")
        print(f"strand matches:   {dna2.as_hex() == dna.as_hex()}")
        print(f"history length:   {len(dna2.history)}")
