import hashlib
import os

import pytest

from digital_dna import DigitalDNA


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
