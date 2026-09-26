import hashlib
import os

import pytest

from digital_dna import DigitalDNA
from dna_binary_codec import decode_from_dna


def _new_dna(tmp_path, label="test-node"):
    return DigitalDNA(seed_label=label, dna_path=os.path.join(str(tmp_path), f"{label}.dna.json"))


def test_add_live_signal_requires_consent(tmp_path):
    dna = _new_dna(tmp_path)
    feature_hash = hashlib.sha256(b"event").hexdigest()
    with pytest.raises(PermissionError):
        dna.add_live_signal("work_login", feature_hash, consent_verified=False)


def test_add_live_signal_rejects_unknown_source(tmp_path):
    dna = _new_dna(tmp_path)
    feature_hash = hashlib.sha256(b"event").hexdigest()
    with pytest.raises(ValueError):
        dna.add_live_signal("raw_video_stream", feature_hash, consent_verified=True)


def test_add_live_signal_mutates_strand(tmp_path):
    dna = _new_dna(tmp_path)
    strand_before = dna.as_hex()
    feature_hash = hashlib.sha256(b"event").hexdigest()
    dna.add_live_signal("work_login", feature_hash, confidence=0.9, consent_verified=True)
    assert dna.as_hex() != strand_before


def test_node_id_and_strand_survive_reload(tmp_path):
    dna = _new_dna(tmp_path)
    feature_hash = hashlib.sha256(b"event").hexdigest()
    dna.add_live_signal("work_login", feature_hash, confidence=0.9, consent_verified=True)

    dna2 = DigitalDNA(seed_label="test-node", dna_path=dna.dna_path)
    assert dna2.node_id == dna.node_id
    assert dna2.as_hex() == dna.as_hex()


def test_node_id_depends_on_salt_not_just_label(tmp_path):
    dna_a = _new_dna(tmp_path, label="node-a")
    dna_b = _new_dna(tmp_path, label="node-b")
    assert dna_a.node_id != dna_b.node_id


def test_encode_decode_message_roundtrip(tmp_path):
    dna = _new_dna(tmp_path)
    msg = "a real message"
    assert dna.decode_message(dna.encode_message(msg)) == msg


# -- DNA-letter mirror of the strand (dna_binary_codec integration) --

def test_as_dna_is_exact_mirror_of_hex_strand(tmp_path):
    dna = _new_dna(tmp_path)
    dna.add_live_signal("work_login", hashlib.sha256(b"e").hexdigest(),
                        confidence=0.9, consent_verified=True)
    mirror = dna.as_dna()
    assert set(mirror) <= set("ACGT")
    assert len(mirror) == 128  # 32 strand bytes * 4 bases each
    # exact, reversible: the letters decode back to the same bytes as the hex
    assert decode_from_dna(mirror) == bytes.fromhex(dna.as_hex())


def test_dna_report_fields_and_verifier(tmp_path):
    dna = _new_dna(tmp_path)
    dna.add_live_signal("manual_entry", hashlib.sha256(b"x").hexdigest(),
                        confidence=1.0, consent_verified=True)
    rep = dna.dna_report()
    assert rep["dna_base_count"] == 128
    assert rep["node_id"] == dna.node_id
    assert DigitalDNA.verify_dna_mirror_matches_hex(rep["strand_hex"], rep["strand_dna"]) is True
    # a mirror that doesn't decode to the same bytes is rejected
    assert DigitalDNA.verify_dna_mirror_matches_hex(rep["strand_hex"], "A" * 128) is False


def test_new_identity_strand_is_seeded_not_blank(tmp_path):
    a = _new_dna(tmp_path, "node-a")
    b = _new_dna(tmp_path, "node-b")
    assert a.as_dna() != "A" * 128
    assert a.as_hex() != b.as_hex()


def test_seeded_strand_persists_across_reload(tmp_path):
    first = _new_dna(tmp_path)
    second = _new_dna(tmp_path)
    assert second.as_hex() == first.as_hex()


def test_legacy_file_without_strand_gets_seeded(tmp_path):
    import json
    path = os.path.join(str(tmp_path), "legacy.dna.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"seed_label": "legacy", "salt": "abc123", "created_at": 0.0}, f)
    dna = DigitalDNA(seed_label="legacy", dna_path=path)
    assert any(bytes.fromhex(dna.as_hex()))
