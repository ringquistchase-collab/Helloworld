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
