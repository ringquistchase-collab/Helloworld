#!/usr/bin/env python3
"""
dna_binary_codec.py
====================
A real, exact, reversible encoding between raw binary data and a 4-letter
DNA-style alphabet (A/C/G/T) — the same 2-bits-per-base scheme used in real
DNA-based digital data storage research (e.g. Microsoft/UW's DNA storage
work encodes arbitrary files this way, using A/C/G/T as a base-4 number
system instead of a person's actual genome).

This is a MIRROR/ENCODING, not a biological claim:
  - It does NOT read, model, or represent any real biological genome.
  - It is exact and fully reversible in both directions, like base64 or
    hex, just with a 4-symbol DNA-letter alphabet instead of 16 or 64
    symbols.
  - Any bytes (a hash, a file, a strand hex string, this project's
    DigitalDNA fingerprint, etc.) can be losslessly turned into a
    sequence of A/C/G/T letters and back into the exact same bytes.

Encoding: each byte -> 8 bits -> 4 DNA bases, 2 bits per base.
    00 -> A   01 -> C   10 -> G   11 -> T
"""

from __future__ import annotations

BASE_MAP = {"00": "A", "01": "C", "10": "G", "11": "T"}
REVERSE_MAP = {v: k for k, v in BASE_MAP.items()}


def encode_to_dna(data: bytes) -> str:
    """Exact encoding: every byte -> 8 bits -> 4 DNA bases (2 bits each).

    encode_to_dna(b"") == "" and every other input round-trips exactly
    through decode_from_dna().
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"encode_to_dna expects bytes, got {type(data).__name__}")
    bits = "".join(f"{byte:08b}" for byte in data)
    return "".join(BASE_MAP[bits[i:i + 2]] for i in range(0, len(bits), 2))


def decode_from_dna(dna_sequence: str) -> bytes:
    """Exact inverse of encode_to_dna — real round-trip, not approximate.

    Raises ValueError on any character outside A/C/G/T (case-insensitive),
    since such a sequence could not have come from encode_to_dna().
    """
    seq = dna_sequence.upper()
    bad = set(seq) - set(REVERSE_MAP)
    if bad:
        raise ValueError(
            f"Invalid DNA sequence: unexpected symbol(s) {sorted(bad)}. "
            f"Only A, C, G, T are valid."
        )
    if len(seq) % 4 != 0:
        raise ValueError(
            f"Invalid DNA sequence length ({len(seq)}): every 4 bases decode "
            f"to exactly 1 byte, so the length must be a multiple of 4."
        )
    bits = "".join(REVERSE_MAP[base] for base in seq)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def gc_content(dna_sequence: str) -> float:
    """Fraction of bases that are G or C (a standard real bioinformatics
    metric, computed here on the encoded letters — 0.0 if the sequence is
    empty)."""
    seq = dna_sequence.upper()
    if not seq:
        return 0.0
    gc = sum(1 for base in seq if base in ("G", "C"))
    return gc / len(seq)


# ---------------------------------------------------------------------------
# Real, executed verification (run every time this file is run directly)
# ---------------------------------------------------------------------------

def _run_self_tests() -> None:
    import os

    failures = []

    def check(label, condition):
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {label}")
        if not condition:
            failures.append(label)

    print("=" * 70)
    print("dna_binary_codec.py — real round-trip verification")
    print("=" * 70)

    # 1. Empty input
    print("\n[1] Empty input")
    empty_encoded = encode_to_dna(b"")
    check("encode_to_dna(b'') == ''", empty_encoded == "")
    check("decode_from_dna('') == b''", decode_from_dna("") == b"")

    # 2. Literal text round-trip
    print("\n[2] Literal text round-trip")
    text_samples = [
        b"Hello, DNA mirror.",
        "Chase Allen Ringquist".encode("utf-8"),
        b"A",
        b"\x00\x01\x02\xff\xfe",
    ]
    for sample in text_samples:
        dna = encode_to_dna(sample)
        back = decode_from_dna(dna)
        ok = back == sample
        check(f"round-trip {sample!r} (dna len={len(dna)})", ok)
        # Sanity: every base is a real A/C/G/T letter
        check(f"  all output symbols in ACGT for {sample!r}", set(dna) <= set("ACGT"))

    # 3. Real strand hex from earlier in this project, round-tripped exactly
    print("\n[3] Real project strand-hex round-trip")
    strand_hex = "7e686198119cc40ef9647d2ca42aa7d19e1b23ff615f8652bafd8d4b246a9129"
    # Odd-length hex string can't be parsed as bytes directly; verify honestly
    # rather than silently padding/truncating it.
    if len(strand_hex) % 2 != 0:
        print(f"  [NOTE] strand hex has odd length ({len(strand_hex)} chars) — "
              f"not valid raw hex bytes on its own, so it is encoded here as "
              f"its literal ASCII text instead, which still exercises a real "
              f"round-trip end to end.")
        strand_bytes = strand_hex.encode("ascii")
    else:
        strand_bytes = bytes.fromhex(strand_hex)
    strand_dna = encode_to_dna(strand_bytes)
    strand_back = decode_from_dna(strand_dna)
    check("strand round-trip exact", strand_back == strand_bytes)
    print(f"  strand bytes: {len(strand_bytes)}  ->  DNA bases: {len(strand_dna)}")
    print(f"  DNA (first 60 bases): {strand_dna[:60]}{'...' if len(strand_dna) > 60 else ''}")
    print(f"  GC content: {gc_content(strand_dna):.4f}")

    # 4. Random byte samples (real os.urandom, not fabricated)
    print("\n[4] 20 random 32-byte samples (os.urandom)")
    random_failures = 0
    for i in range(20):
        raw = os.urandom(32)
        dna = encode_to_dna(raw)
        back = decode_from_dna(dna)
        if back != raw or len(dna) != 128:
            random_failures += 1
    check(f"all 20 random samples round-trip exactly (len=128 bases each)",
          random_failures == 0)

    # 5. Error handling on invalid input
    print("\n[5] Error handling")
    try:
        decode_from_dna("ACGX")
        check("rejects invalid symbol 'X'", False)
    except ValueError:
        check("rejects invalid symbol 'X'", True)

    try:
        decode_from_dna("ACG")  # length not a multiple of 4
        check("rejects length not multiple of 4", False)
    except ValueError:
        check("rejects length not multiple of 4", True)

    try:
        encode_to_dna("not bytes")  # type: ignore[arg-type]
        check("rejects non-bytes input to encode_to_dna", False)
    except TypeError:
        check("rejects non-bytes input to encode_to_dna", True)

    # 6. Case-insensitivity of decode
    print("\n[6] Case-insensitive decode")
    mixed = "acGtACgT"
    check("lowercase/mixed-case decodes same as uppercase",
          decode_from_dna(mixed) == decode_from_dna(mixed.upper()))

    print("\n" + "=" * 70)
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    else:
        print("RESULT: all checks PASSED — encode_to_dna/decode_from_dna are")
        print("a real, exact, bidirectional mirror between binary and A/C/G/T.")
    print("=" * 70)


if __name__ == "__main__":
    _run_self_tests()
