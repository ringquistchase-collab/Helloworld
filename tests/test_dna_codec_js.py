"""
Cross-language parity: does the independent JavaScript codec (dna_codec.js)
produce byte-identical output to the Python codec (dna_binary_codec.py)?

This is the real proof the DNA/identity computation is a language-agnostic
algorithm, not a Python artifact. It runs Node.js as a subprocess and
compares its output to Python's for the same inputs.

Like the project's other non-Python interop clients (see KNOWN_GAPS.md),
this is skipped when Node.js isn't installed, so it never breaks CI or a
plain `pytest` on a machine without Node -- it just adds real coverage
wherever Node is present.
"""
import hashlib
import json
import os
import shutil
import subprocess

import pytest

from dna_binary_codec import encode_to_dna, decode_from_dna, complement_strand, gc_content

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="Node.js not installed; JS parity check skipped")

IDENTITY_TEXT = "Chase Allen Ringquist | Bixby, Oklahoma | dna-chain-project"


def _run_node(js_code: str) -> dict:
    """Run a Node one-liner that requires dna_codec.js and prints one JSON
    object, and return it parsed."""
    result = subprocess.run(
        [_NODE, "-e", js_code],
        cwd=_PROJECT_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_js_identity_strand_matches_python_byte_for_byte():
    fp = hashlib.sha256(IDENTITY_TEXT.encode("utf-8")).digest()
    py_strand = encode_to_dna(fp)
    js = _run_node(
        "const c=require('./dna_codec.js');"
        f"const t={json.dumps(IDENTITY_TEXT)};"
        "const fp=c.identityFingerprint(t);"
        "const s=c.encodeToDna(fp);"
        "console.log(JSON.stringify({fp:fp.toString('hex'),strand:s,"
        "comp:c.complementStrand(s),gc:c.gcContent(s)}));"
    )
    assert js["fp"] == fp.hex()
    assert js["strand"] == py_strand
    assert js["comp"] == complement_strand(py_strand)
    assert abs(js["gc"] - gc_content(py_strand)) < 1e-9


def test_js_encode_matches_python_for_all_byte_values():
    # bytes 0..255 -> the JS encoding must equal the Python encoding exactly
    all_bytes = bytes(range(256))
    py_dna = encode_to_dna(all_bytes)
    js = _run_node(
        "const c=require('./dna_codec.js');"
        "const b=Buffer.from(Array.from({length:256},(_,i)=>i));"
        "console.log(JSON.stringify({dna:c.encodeToDna(b)}));"
    )
    assert js["dna"] == py_dna


def test_python_can_decode_what_js_encoded():
    # JS encodes a known string; Python must decode it back to the same bytes
    text = "cross-language round trip"
    js = _run_node(
        "const c=require('./dna_codec.js');"
        f"const b=Buffer.from({json.dumps(text)},'utf8');"
        "console.log(JSON.stringify({dna:c.encodeToDna(b)}));"
    )
    assert decode_from_dna(js["dna"]) == text.encode("utf-8")
