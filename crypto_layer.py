"""
crypto_layer.py
================
Real cryptographic primitives backing network_os.py's authenticated,
encrypted peer protocol. Built on the `cryptography` package (PyCA) --
audited, standard, not a hand-rolled implementation of any of these
primitives.

WHAT'S REAL HERE
--------------------
  - Ed25519 keypair generation, signing, verification (RFC 8032)
  - X25519 Diffie-Hellman key exchange (RFC 7748)
  - HKDF-SHA256 (RFC 5869) to turn a raw ECDH shared secret into a
    real symmetric key, not used directly
  - AES-256-GCM authenticated encryption (NIST SP 800-38D) -- gives
    both confidentiality AND tamper-evidence (a modified ciphertext
    or wrong key fails to decrypt rather than silently returning
    garbage)

HONEST LIMITS (matches network_os.py's own docstring)
-----------------------------------------------------------
No certificate authority, no trust-on-first-use pinning -- a peer's
Ed25519 signing key is trusted the moment its signed hello verifies.
Fine for known devices on a LAN you control; add pinning before
trusting arbitrary peers over an open network. No replay-window or
rekeying policy either -- this is session-key crypto for one
long-lived TCP connection, not a hardened multi-session protocol like
TLS/SSH.

Wire format
-----------
Public keys travel as 32-byte raw values, hex-encoded (64 hex chars).
aead_encrypt()'s output is `nonce(12 bytes) || ciphertext+tag`,
concatenated as one bytes object -- aead_decrypt() expects exactly
that shape back.

Usage
-----
    import crypto_layer as ck

    signing_priv, signing_pub = ck.generate_signing_keypair()
    exchange_priv, exchange_pub = ck.generate_exchange_keypair()

    sig = ck.sign(signing_priv, b"some data")
    ck.verify(signing_pub, b"some data", sig)   # -> True

    # both sides compute the SAME shared_key from their own priv + the
    # other side's pub -- that's the whole point of Diffie-Hellman
    shared_key = ck.derive_shared_key(my_exchange_priv, their_exchange_pub)

    payload = ck.aead_encrypt(shared_key, b"secret message", aad=b"context")
    plaintext = ck.aead_decrypt(shared_key, payload, aad=b"context")
"""

from __future__ import annotations
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidSignature, InvalidTag

_NONCE_BYTES = 12
_AES_KEY_BYTES = 32
_HKDF_INFO = b"network-os-session-v1"


# ----------------------------------------------------------------- #
# Signing (Ed25519) -- authenticates "this really came from node X"
# ----------------------------------------------------------------- #

def generate_signing_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def signing_pub_to_hex(pub: Ed25519PublicKey) -> str:
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def signing_pub_from_hex(hex_str: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_str))


def sign(priv: Ed25519PrivateKey, data: bytes) -> bytes:
    return priv.sign(data)


def verify(pub: Ed25519PublicKey, data: bytes, signature: bytes) -> bool:
    try:
        pub.verify(signature, data)
        return True
    except InvalidSignature:
        return False


# ----------------------------------------------------------------- #
# Key exchange (X25519 ECDH) -- lets two peers agree on a shared
# secret over an insecure channel without ever transmitting it
# ----------------------------------------------------------------- #

def generate_exchange_keypair() -> tuple[X25519PrivateKey, X25519PublicKey]:
    priv = X25519PrivateKey.generate()
    return priv, priv.public_key()


def generate_exchange_keypair_raw() -> tuple[X25519PrivateKey, bytes]:
    """Same X25519 keypair, but returns the public key as raw 32-byte bytes
    instead of a key object -- convenience for byte-oriented callers (a
    per-peer session layer that ships raw pubkeys around). The object-based
    generate_exchange_keypair() above is unchanged for existing callers."""
    priv = X25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, pub_bytes


def exchange_pub_to_hex(pub: X25519PublicKey) -> str:
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def exchange_pub_from_hex(hex_str: str) -> X25519PublicKey:
    return X25519PublicKey.from_public_bytes(bytes.fromhex(hex_str))


def derive_shared_key(
    my_priv: X25519PrivateKey,
    their_pub: X25519PublicKey | bytes,
    context: bytes = _HKDF_INFO,
) -> bytes:
    """
    Real Diffie-Hellman: my_priv.exchange(their_pub) and
    their_priv.exchange(my_pub) mathematically produce the SAME raw
    shared secret on both sides, without either side's private key
    ever leaving its own process. HKDF-SHA256 turns that raw ECDH
    output into a proper 256-bit symmetric key (raw DH output isn't
    uniformly random enough to use directly as a cipher key).

    `their_pub` may be an X25519PublicKey OR raw 32-byte public-key bytes
    (both accepted for caller convenience). `context` is the HKDF `info`
    binding; it DEFAULTS to the project's fixed session label so existing
    callers and the cross-language interop clients derive exactly the same
    key as before -- pass a distinct context to scope a key to a different
    purpose/session.
    """
    if isinstance(their_pub, (bytes, bytearray)):
        their_pub = X25519PublicKey.from_public_bytes(bytes(their_pub))
    shared_secret = my_priv.exchange(their_pub)
    return HKDF(
        algorithm=hashes.SHA256(), length=_AES_KEY_BYTES,
        salt=None, info=context,
    ).derive(shared_secret)


# ----------------------------------------------------------------- #
# Authenticated encryption (AES-256-GCM)
# ----------------------------------------------------------------- #

def aead_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Returns nonce || ciphertext+tag, one bytes object. A fresh random
    nonce every call -- AES-GCM's security guarantee depends on never
    reusing a (key, nonce) pair."""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce + ciphertext


def aead_decrypt(key: bytes, payload: bytes, aad: bytes = b"") -> bytes:
    """Raises InvalidTag if the payload was tampered with, or if `key`
    doesn't match the key it was encrypted under -- that's the real
    tamper-evidence AES-GCM provides, not a soft failure."""
    nonce, ciphertext = payload[:_NONCE_BYTES], payload[_NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


if __name__ == "__main__":
    print("=== Ed25519 signing: real sign + verify ===")
    priv_a, pub_a = generate_signing_keypair()
    msg = b"node-a says hello"
    sig = sign(priv_a, msg)
    print(f"verify with correct key:   {verify(pub_a, msg, sig)}")
    print(f"verify with tampered msg:  {verify(pub_a, msg + b'!', sig)}")
    priv_b, pub_b = generate_signing_keypair()
    print(f"verify with WRONG key:     {verify(pub_b, msg, sig)}")

    print("\n=== X25519 ECDH: both sides derive the SAME shared key ===")
    ex_priv_a, ex_pub_a = generate_exchange_keypair()
    ex_priv_b, ex_pub_b = generate_exchange_keypair()
    key_a = derive_shared_key(ex_priv_a, ex_pub_b)
    key_b = derive_shared_key(ex_priv_b, ex_pub_a)
    print(f"key_a == key_b: {key_a == key_b}")
    print(f"key length: {len(key_a)} bytes ({len(key_a)*8}-bit)")

    print("\n=== Hex round-trip for wire transport ===")
    hex_pub = exchange_pub_to_hex(ex_pub_a)
    restored = exchange_pub_from_hex(hex_pub)
    key_a2 = derive_shared_key(ex_priv_b, restored)
    print(f"key derived from hex-round-tripped pubkey matches: {key_a2 == key_b}")

    print("\n=== AES-256-GCM: real authenticated encryption ===")
    plaintext = b"a real secret message between two peers"
    payload = aead_encrypt(key_a, plaintext, aad=b"node-a")
    decrypted = aead_decrypt(key_b, payload, aad=b"node-a")
    print(f"round-trip matches:        {decrypted == plaintext}")

    print("\n=== Tamper detection: flipping one byte of ciphertext ===")
    tampered = bytearray(payload)
    tampered[-1] ^= 0xFF
    try:
        aead_decrypt(key_b, bytes(tampered), aad=b"node-a")
        print("UH OH: tampered payload decrypted without error -- this should never happen")
    except InvalidTag:
        print("correctly rejected: InvalidTag raised, tamper-evidence works")

    print("\n=== Wrong key: using node-a's key to decrypt what was meant for someone else ===")
    ex_priv_c, ex_pub_c = generate_exchange_keypair()
    key_c = derive_shared_key(ex_priv_c, ex_pub_b)
    try:
        aead_decrypt(key_c, payload, aad=b"node-a")
        print("UH OH: decrypted with the wrong key -- this should never happen")
    except InvalidTag:
        print("correctly rejected: InvalidTag raised for wrong key")
