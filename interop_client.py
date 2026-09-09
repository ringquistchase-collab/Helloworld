#!/usr/bin/env python3
"""
interop_client.py
==================
A standalone Python client speaking network_os.py's wire protocol
(PROTOCOL.md) -- written WITHOUT importing network_os.py or
crypto_layer.py. The point isn't to reuse the reference implementation's
own code path; it's to prove PROTOCOL.md alone is enough to build a
conforming client, in the same language as the reference implementation.

Uses the `cryptography` package (the same dependency network_os.py
itself uses) for Ed25519 / X25519 / HKDF-SHA256 / AES-256-GCM.

Run: python3 interop_client.py 127.0.0.1 9501
"""
from __future__ import annotations
import hashlib
import json
import os
import socket
import sys
import time

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


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9501

    node_id = sha256_hex(b"py-interop-node")[:16]
    strand_hex = "0" * 64

    signing_priv = Ed25519PrivateKey.generate()
    signing_pub = signing_priv.public_key()
    exchange_priv = X25519PrivateKey.generate()
    exchange_pub = exchange_priv.public_key()

    my_signing_pub_hex = signing_pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    my_exchange_pub_hex = exchange_pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    to_sign = f"{node_id}|{strand_hex}|{my_exchange_pub_hex}".encode()
    sig = signing_priv.sign(to_sign)

    hello = {
        "type": "hello", "node_id": node_id, "listen_port": 0, "strand_hex": strand_hex,
        "signing_pub": my_signing_pub_hex, "exchange_pub": my_exchange_pub_hex, "sig": sig.hex(),
    }

    print(f"[py] connecting to {host}:{port}")
    sock = socket.create_connection((host, port))
    sock.sendall((json.dumps(hello) + "\n").encode())
    print(f"[py] sent hello (node_id={node_id})")

    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    line, _, _ = buf.partition(b"\n")
    print("[py] received:", line.decode())
    ack = json.loads(line)

    to_verify = f"{ack['node_id']}|{ack['strand_hex']}|{ack['exchange_pub']}".encode()
    peer_signing_pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(ack["signing_pub"]))
    try:
        peer_signing_pub.verify(bytes.fromhex(ack["sig"]), to_verify)
        sig_ok = True
    except Exception:
        sig_ok = False
    print("[py] Python node signature verified:", sig_ok)
    if not sig_ok:
        sock.close()
        sys.exit(1)

    peer_exchange_pub = X25519PublicKey.from_public_bytes(bytes.fromhex(ack["exchange_pub"]))
    shared_secret = exchange_priv.exchange(peer_exchange_pub)

    salt = b"\x00" * 32  # matches HKDF's salt=None default (HashLen zero bytes, RFC 5869)
    session_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt, info=b"network-os-session-v1",
    ).derive(shared_secret)
    print("[py] derived session key:", session_key.hex())

    block_id = "py-000001"
    source = "py_interop_node"
    feature_hash = sha256_hex(b"py-originated-event")
    confidence = 1
    timestamp = int(time.time())

    canonical = (
        f'{{"block_id": "{block_id}", "confidence": {confidence}, '
        f'"feature_hash": "{feature_hash}", "source": "{source}", "timestamp": {timestamp}}}'
    )
    block_sig = signing_priv.sign(canonical.encode())

    signed_block = {
        "block_id": block_id, "confidence": confidence, "feature_hash": feature_hash,
        "source": source, "timestamp": timestamp, "sig": block_sig.hex(),
    }
    inner_plaintext = json.dumps({"type": "block", "block": signed_block})

    nonce = os.urandom(12)
    ciphertext = AESGCM(session_key).encrypt(nonce, inner_plaintext.encode(), node_id.encode())
    payload = nonce + ciphertext

    envelope = {"type": "enc", "node_id": node_id, "payload": payload.hex()}
    sock.sendall((json.dumps(envelope) + "\n").encode())
    print("[py] sent signed, AES-256-GCM-encrypted block to Python node")

    time.sleep(0.3)
    sock.close()
    print("[py] done, closing")


if __name__ == "__main__":
    main()
