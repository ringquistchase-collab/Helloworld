"""
Tests for dna_binary_codec -- the exact, reversible binary <-> A/C/G/T
encoding (base-4 alphabet, 2 bits per base). Pins the round-trip
guarantee, the base mapping, empty/edge inputs, error handling, and
gc_content -- all offline and deterministic.
"""
import os

import pytest

from dna_binary_codec import (
    encode_to_dna, decode_from_dna, gc_content,
    complement_strand, double_helix_view,
)


def test_empty_roundtrip():
    assert encode_to_dna(b"") == ""
    assert decode_from_dna("") == b""


def test_known_base_mapping():
    # 0x00 -> 00 00 00 00 -> AAAA ; 0xFF -> 11 11 11 11 -> TTTT
    assert encode_to_dna(b"\x00") == "AAAA"
    assert encode_to_dna(b"\xff") == "TTTT"
    # 0x1B -> 0001 1011 -> 00 01 10 11 -> A C G T
    assert encode_to_dna(b"\x1b") == "ACGT"


def test_each_byte_is_four_bases():
    for n in (1, 5, 32, 100):
        assert len(encode_to_dna(os.urandom(n))) == 4 * n


@pytest.mark.parametrize("sample", [
    b"Hello, DNA mirror.",
    "Chase Allen Ringquist".encode("utf-8"),
    b"A",
    b"\x00\x01\x02\xff\xfe",
    bytes(range(256)),
])
def test_roundtrip_exact(sample):
    dna = encode_to_dna(sample)
    assert set(dna) <= set("ACGT")
    assert decode_from_dna(dna) == sample


def test_random_roundtrip_is_lossless():
    for _ in range(50):
        raw = os.urandom(16)
        assert decode_from_dna(encode_to_dna(raw)) == raw


def test_decode_is_case_insensitive():
    assert decode_from_dna("acgt") == decode_from_dna("ACGT")
    assert decode_from_dna("aCgT") == decode_from_dna("ACGT")


def test_decode_rejects_invalid_symbol():
    with pytest.raises(ValueError):
        decode_from_dna("ACGX")


def test_decode_rejects_bad_length():
    # every 4 bases = 1 byte, so length must be a multiple of 4
    with pytest.raises(ValueError):
        decode_from_dna("ACG")


def test_encode_rejects_non_bytes():
    with pytest.raises(TypeError):
        encode_to_dna("not bytes")  # type: ignore[arg-type]


def test_encode_accepts_bytearray():
    assert decode_from_dna(encode_to_dna(bytearray(b"hi"))) == b"hi"


def test_gc_content():
    assert gc_content("") == 0.0
    assert gc_content("AAAA") == 0.0      # no G/C
    assert gc_content("GCGC") == 1.0      # all G/C
    assert gc_content("ACGT") == 0.5      # G and C out of 4
    assert gc_content("acgt") == 0.5      # case-insensitive


# -- Watson-Crick complementary strand --------------------------------------

def test_complement_pairs():
    assert complement_strand("ACGT") == "TGCA"   # A<->T, C<->G
    assert complement_strand("AAAA") == "TTTT"


def test_complement_is_its_own_inverse():
    for s in ("ACGT", "GGCCAATT", "TACG"):
        assert complement_strand(complement_strand(s)) == s


def test_complement_rejects_invalid_symbol():
    with pytest.raises(ValueError):
        complement_strand("ACGX")


def test_complement_equals_bitwise_not_of_bytes():
    # complementing every base == flipping both bits of its 2-bit codon
    # == a bitwise NOT of the underlying bytes. Verify on concrete data.
    for sample in (b"\x00\xff\x0f\xf0", b"Hello", bytes(range(32))):
        comp_dna = complement_strand(encode_to_dna(sample))
        assert decode_from_dna(comp_dna) == bytes(b ^ 0xFF for b in sample)


def test_double_helix_view_fields_and_relationship():
    sample = b"Hello, DNA mirror."
    helix = double_helix_view(sample)
    assert helix["strand_5to3"] == encode_to_dna(sample)
    assert len(helix["strand_complement_3to5"]) == len(helix["strand_5to3"])
    assert helix["complement_equals_bitwise_not"] is True
    assert helix["gc_content"] == gc_content(helix["strand_5to3"])


def test_double_helix_view_empty():
    helix = double_helix_view(b"")
    assert helix["strand_5to3"] == ""
    assert helix["strand_complement_3to5"] == ""
    assert helix["complement_equals_bitwise_not"] is True
    assert helix["gc_content"] == 0.0
