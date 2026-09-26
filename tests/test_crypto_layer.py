import crypto_layer as ck


def test_ed25519_sign_verify_roundtrip():
    priv, pub = ck.generate_signing_keypair()
    msg = b"real message"
    sig = ck.sign(priv, msg)
    assert ck.verify(pub, msg, sig) is True


def test_ed25519_rejects_tampered_message():
    priv, pub = ck.generate_signing_keypair()
    sig = ck.sign(priv, b"original")
    assert ck.verify(pub, b"tampered", sig) is False


def test_ed25519_rejects_wrong_key():
    priv_a, pub_a = ck.generate_signing_keypair()
    priv_b, pub_b = ck.generate_signing_keypair()
    sig = ck.sign(priv_a, b"message")
    assert ck.verify(pub_b, b"message", sig) is False


def test_x25519_both_sides_derive_same_key():
    priv_a, pub_a = ck.generate_exchange_keypair()
    priv_b, pub_b = ck.generate_exchange_keypair()
    key_a = ck.derive_shared_key(priv_a, pub_b)
    key_b = ck.derive_shared_key(priv_b, pub_a)
    assert key_a == key_b
    assert len(key_a) == 32


def test_hex_roundtrip_produces_same_key():
    priv_a, pub_a = ck.generate_exchange_keypair()
    priv_b, pub_b = ck.generate_exchange_keypair()
    hex_pub_a = ck.exchange_pub_to_hex(pub_a)
    restored = ck.exchange_pub_from_hex(hex_pub_a)
    assert ck.derive_shared_key(priv_b, restored) == ck.derive_shared_key(priv_b, pub_a)


def test_aead_encrypt_decrypt_roundtrip():
    key = b"0" * 32
    plaintext = b"a real secret"
    payload = ck.aead_encrypt(key, plaintext, aad=b"context")
    assert ck.aead_decrypt(key, payload, aad=b"context") == plaintext


def test_aead_detects_tampered_ciphertext():
    import pytest
    from cryptography.exceptions import InvalidTag

    key = b"0" * 32
    payload = bytearray(ck.aead_encrypt(key, b"secret", aad=b"ctx"))
    payload[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        ck.aead_decrypt(key, bytes(payload), aad=b"ctx")


def test_aead_rejects_wrong_key():
    import pytest
    from cryptography.exceptions import InvalidTag

    key_a, key_b = b"0" * 32, b"1" * 32
    payload = ck.aead_encrypt(key_a, b"secret", aad=b"ctx")
    with pytest.raises(InvalidTag):
        ck.aead_decrypt(key_b, payload, aad=b"ctx")


# -- additive: raw-bytes keypair + context-scoped derivation ----------------

def test_generate_exchange_keypair_raw_returns_32_public_bytes():
    priv, pub_bytes = ck.generate_exchange_keypair_raw()
    assert isinstance(pub_bytes, bytes) and len(pub_bytes) == 32


def test_derive_accepts_raw_bytes_equivalently_to_object():
    # default context must match the old fixed behavior exactly, whether the
    # peer key is passed as an object or as raw bytes (interop unchanged)
    a_priv, a_pub = ck.generate_exchange_keypair()
    b_priv, b_pub_bytes = ck.generate_exchange_keypair_raw()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    a_pub_bytes = a_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)

    # object-arg path and bytes-arg path on the same peer key agree
    key_obj = ck.derive_shared_key(b_priv, a_pub)
    key_bytes = ck.derive_shared_key(b_priv, a_pub_bytes)
    assert key_obj == key_bytes

    # both sides still derive the same key with the default context
    a_side = ck.derive_shared_key(a_priv, b_pub_bytes)
    assert a_side == key_obj


def test_context_scopes_the_key():
    a_priv, a_pub = ck.generate_exchange_keypair()
    b_priv, b_pub = ck.generate_exchange_keypair()
    ctx1, ctx2 = b"session/purpose-1", b"session/purpose-2"

    # same context on both sides -> same key
    assert ck.derive_shared_key(a_priv, b_pub, ctx1) == ck.derive_shared_key(b_priv, a_pub, ctx1)
    # different context -> different key from the same ECDH secret
    assert ck.derive_shared_key(a_priv, b_pub, ctx1) != ck.derive_shared_key(a_priv, b_pub, ctx2)
