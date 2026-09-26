"""
Offline tests for self_and_research_helix_live's pure logic:
  - analyze_real_sequence (base counts + GC on real nucleotide letters)
  - build_self_helix (SHA-256 -> strand + Watson-Crick complement, with the
    complement == bitwise-NOT helix property)
  - fetch_real_reference_sequence's FASTA parsing (requests.get mocked, so
    no network is touched)
"""
import pytest

import self_and_research_helix_live as mod
from self_and_research_helix_live import (
    analyze_real_sequence, build_self_helix, fetch_real_reference_sequence,
)
from dna_binary_codec import decode_from_dna


def test_analyze_real_sequence_counts_and_gc():
    stats = analyze_real_sequence("ACGTACGT")
    assert stats["length"] == 8
    assert stats["base_counts"] == {"A": 2, "C": 2, "G": 2, "T": 2}
    assert stats["gc_content"] == 0.5


def test_analyze_real_sequence_empty():
    stats = analyze_real_sequence("")
    assert stats["length"] == 0
    assert stats["gc_content"] == 0.0


def test_build_self_helix_is_verified_and_reversible():
    h = build_self_helix(b"some identity bytes")
    # SHA-256 = 32 bytes -> 128 DNA bases
    assert len(h["strand"]) == 128
    # the strand decodes back to the exact fingerprint bytes
    assert decode_from_dna(h["strand"]) == bytes.fromhex(h["fingerprint_hex"])
    # the helix property holds: complement == bitwise NOT of the fingerprint
    assert h["helix_verified"] is True
    assert h["source_bytes_len"] == len(b"some identity bytes")


def test_fetch_parses_fasta_without_network(monkeypatch):
    class _FakeResp:
        text = ">NM_000207.1 Homo sapiens insulin (INS), mRNA\nACGTacgt\nGGCC\n"
        def raise_for_status(self):
            return None

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp())
    ref = fetch_real_reference_sequence()
    assert ref["accession"] == "NM_000207"
    assert ref["header"].startswith(">NM_000207")
    # sequence lines are joined and upper-cased; header dropped
    assert ref["sequence"] == "ACGTACGTGGCC"
