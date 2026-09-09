# PROTOCOL.md — the network_os wire protocol

This is the open spec for the peer protocol implemented in
`network_os.py` / `crypto_layer.py`. Any language can implement a
client against this document alone — the interop clients
(`interop_client.go`, `interop_client.rb`, `interop_client.cpp`,
`JavaInteropClient.java`, `interop_client.js`, and a standalone
`interop_client.py` that deliberately avoids importing
`network_os.py`/`crypto_layer.py`) exist to prove that claim against
the reference Python implementation, not to be the only way in. Per
[[TRUST.md]]:
capture and consent logic (`digital_dna.py`) must stay open; a client
that only speaks this protocol is free to be closed-source, as long
as it actually implements what's written here.

If this document and `network_os.py`/`crypto_layer.py` ever disagree,
**the code is authoritative** — file a fix here.

## Transport

Plain TCP. One connection per peer pair, held open for the life of
the session (no reconnect/backoff policy defined here — that's a
client concern). Messages are newline-delimited JSON: each message is
a single JSON object serialized with no embedded newline, followed by
`\n`. Read with a line-buffered reader.

There is no length prefix and no framing beyond the newline — a
message's JSON must not itself contain a literal `\n` byte.

## Message envelope: two layers

Every message on the wire has a top-level `"type"`. Only two
top-level types exist before a session key is established:

- `hello` — outbound handshake, sent immediately after connecting
- `hello_ack` — the reply to a `hello`

After the handshake, every other message is wrapped in one more
top-level type:

- `enc` — an AES-256-GCM-encrypted envelope carrying an **inner**
  message (`block`, `ping`, `pong`, `topic`, or any custom inner type
  a peer registers a handler for)

Any other top-level `type`, or a plaintext (non-`enc`) message
received after the handshake, is silently dropped. There is no error
response on the wire for malformed or untrusted input — a peer that
sends garbage simply gets ignored, not told why.

## Identity

Each node has:
- `node_id` — a stable short hex id (16 hex chars in the reference
  implementation: the first 16 hex chars of
  `sha256(f"{seed_label}:{salt}")`), persisted locally, NOT
  renegotiated per-connection
- an Ed25519 signing keypair, generated fresh per process (not
  persisted in the reference implementation — a restarted node gets a
  new signing key, and peers must re-verify it on reconnect)
- an X25519 key-exchange keypair, also generated fresh per process

Public keys travel on the wire as **raw 32-byte values, hex-encoded**
(64 hex characters) — no PEM, no DER, no compression flags.

## Handshake

1. The connecting side sends `hello`:

   ```json
   {
     "type": "hello",
     "node_id": "<hex node id>",
     "listen_port": <int>,
     "strand_hex": "<hex identity strand>",
     "signing_pub": "<64 hex chars, Ed25519 public key>",
     "exchange_pub": "<64 hex chars, X25519 public key>",
     "sig": "<hex signature>"
   }
   ```

2. The listening side verifies `sig`, and if valid, replies
   `hello_ack` with the same shape (minus `listen_port`).

3. Both sides now hold the peer's `signing_pub` and `exchange_pub` and
   consider the peer authenticated for this connection. Neither side
   sends any further plaintext message.

### What `sig` signs

Not the whole `hello` object — a specific pipe-joined string, in this
exact field order, encoded as UTF-8 before signing:

```
sig = Ed25519_sign(signing_priv, f"{node_id}|{strand_hex}|{exchange_pub}")
```

A verifier reconstructs the identical string from the received
message and calls `Ed25519_verify(signing_pub, that_string, sig)`.
`listen_port` is never part of the signed data — it's informational
only, so a NAT/proxy can rewrite it without invalidating the
handshake.

A `hello` or `hello_ack` whose signature doesn't verify against its
own claimed `signing_pub` is dropped with no reply and no error
frame. There is no retry or renegotiation — the connection is simply
never trusted.

### Deriving the session key

Both sides independently compute the same shared secret via X25519
ECDH using their own private exchange key and the peer's public
exchange key, then run it through **HKDF-SHA256** (RFC 5869) to get a
32-byte (256-bit) symmetric key:

```
shared_secret = X25519_ECDH(my_exchange_priv, their_exchange_pub)
session_key   = HKDF_SHA256(ikm=shared_secret, salt=None, info="network-os-session-v1", length=32)
```

`salt=None` in the reference implementation means an all-zero salt of
the hash's block size — an implementation must match this exactly, or
the derived keys won't agree. `info` must be the literal ASCII bytes
`network-os-session-v1`.

There is no key confirmation step — if two sides derive different
session keys (bug, or ECDH on mismatched curves), the failure surfaces
later as every `enc` payload failing AES-GCM tag verification, not as
an explicit handshake error.

## The `enc` envelope

Every message after the handshake:

```json
{
  "type": "enc",
  "node_id": "<sender's node_id>",
  "payload": "<hex-encoded ciphertext>"
}
```

`payload` decodes to `nonce (12 bytes) || ciphertext+tag`, one
contiguous byte string — AES-256-GCM with a fresh random 12-byte nonce
per message (nonce reuse under the same key breaks AES-GCM's security
guarantee entirely; never cache or predict nonces). Decrypt with:

```
plaintext = AES-256-GCM.decrypt(
    key = session_key_for(envelope.node_id),
    nonce = payload[:12],
    ciphertext_and_tag = payload[12:],
    aad = envelope.node_id.encode("utf-8"),
)
```

The **additional authenticated data (AAD)** is the sender's `node_id`
as UTF-8 bytes, matching the value in the envelope's own `node_id`
field. This binds the ciphertext to who claims to have sent it — an
envelope replayed with a different `node_id` label fails decryption
even with a stolen valid ciphertext, because the AAD used to encrypt
won't match. `plaintext` is UTF-8 JSON: the inner message.

A receiver that has no session key for the claimed `node_id`, or whose
AES-GCM tag check fails for any reason (wrong key, corrupted bytes,
tampered ciphertext, wrong AAD), drops the envelope silently.

## Inner message types

Decrypted `plaintext` is itself a JSON object with its own `"type"`:

### `block`

```json
{
  "type": "block",
  "block": {
    "block_id": "...",
    "source": "...",
    "feature_hash": "...",
    "confidence": <number 0.0-1.0>,
    "timestamp": <number, unix seconds>,
    "sig": "<hex signature>"
  }
}
```

`sig` signs the **canonical JSON** of the block object with `sig`
itself removed, serialized with sorted keys and Python's default
`json.dumps` separators (`", "` between items, `": "` between key and
value — i.e. NOT the compact `,`/`:` form):

```
canonical = json_dumps(block_without_sig, sort_keys=True, separators=(", ", ": "))
sig = Ed25519_sign(signing_priv, canonical.encode("utf-8"))
```

A receiver strips `sig`, rebuilds the same canonical string, and
verifies it against the **sending peer's** `signing_pub` from the
handshake — not a key embedded in the block itself. A block whose
signature doesn't verify is dropped; a block that verifies is scored
(DAS 0-6; trusted as-is if the block already carries `das_score`,
otherwise derived from `confidence` and recency) and appended to that
peer's slot in the receiver's local `network_ledger`.

Blocks received this way never touch the receiver's own
`digital_dna.py` identity strand — see [[TRUST.md]] on why identity
mutation stays local to consented signals only. A gossiped block only
feeds the collective `network_ledger` / MOS rollup.

### `ping` / `pong`

```json
{"type": "ping"}
```
```json
{"type": "pong"}
```

No fields, no signature (the outer `enc` envelope's AES-GCM tag is the
only authentication a ping/pong needs — it can only have come from
whoever holds the shared session key). A `ping` is answered with an
immediate `pong` over the same encrypted channel. Receiving either
updates the sender's last-seen liveness timestamp. There is no
required cadence in the wire protocol itself — the reference
implementation sends one `ping` on a periodic interval, but a
conforming client can ping at whatever rate suits it.

### `topic`

```json
{"type": "topic", "topic": {"...": "arbitrary JSON object"}}
```

No fixed schema for the `topic` payload beyond "a JSON object" — it's
an application-level extension point (used by the research-topic
gossip layer), authenticated and encrypted the same as everything
else via the outer `enc` envelope, but not further validated by the
core protocol.

### Custom inner types

An implementation may define additional inner `"type"` values beyond
`block`/`ping`/`pong`/`topic`. Because they arrive already inside a
verified `enc` envelope, a custom handler does not need to re-check
authenticity or re-derive which peer sent it — that's already been
established by the successful AES-GCM decrypt under that peer's
session key. An inner type nobody has registered a handler for is
simply ignored.

## What this protocol does NOT provide

Documented here so a client author doesn't assume more than is
actually guaranteed:

- **No certificate authority or key pinning.** A peer's Ed25519
  signing key is trusted the moment its `hello`/`hello_ack` signature
  verifies — first-connect trust, not trust-on-first-use with a
  persisted pin. Fine for known devices on a network you control;
  add your own pinning layer before trusting arbitrary peers over an
  open network.
- **No forward secrecy across restarts.** Exchange keys are generated
  fresh per process in the reference implementation, but nothing in
  the wire protocol *requires* that — a client that persists and
  reuses its X25519 key breaks forward secrecy for itself. Regenerate
  per session if you want the guarantee.
- **No replay window.** A captured, still-valid `enc` envelope can be
  resent to the same peer before its session key rotates, and will
  decrypt and process again. There is no sequence number or timestamp
  check on inner messages themselves (blocks carry a `timestamp` field,
  but nothing rejects an old one).
- **No rekeying policy.** One session key lives for the life of the
  TCP connection. Reconnecting derives a fresh key (new X25519
  ephemeral keys); there is no in-band rekey.
- **No peer discovery.** This document only covers what happens once
  two nodes already have each other's `host:port`. Finding peers is
  entirely out of scope.
- **No error/rejection frames.** Every failure mode above (bad
  signature, undecryptable envelope, unknown message type) is a
  silent drop, never a wire-level error message. Don't build a client
  that expects one.

## Conformance

A client conforms to this protocol if it can, at minimum:

1. Complete a `hello`/`hello_ack` handshake with the reference Python
   implementation, with signatures verifying on both sides.
2. Derive a session key that produces mutually decryptable `enc`
   envelopes in both directions.
3. Send a validly signed `block` inner message that the reference
   implementation accepts into its `network_ledger`.

The interop clients in this project exist to exercise exactly that
round trip against a live reference node.
