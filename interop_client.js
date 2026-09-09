// interop_client.js
// A real Node.js client speaking network_os.py's wire protocol (PROTOCOL.md).
// Uses Node's built-in crypto module (Ed25519, X25519, AES-256-GCM,
// SHA-256) and the net module for the TCP transport. No npm dependencies.
// Requires Node.js 13.9+ (crypto.diffieHellman on X25519 KeyObjects).
//
// Run: node interop_client.js 127.0.0.1 9501

'use strict';
const crypto = require('crypto');
const net = require('net');

function sha256Hex(data) {
  return crypto.createHash('sha256').update(data).digest('hex');
}

// HKDF-SHA256 (RFC 5869) -- matching crypto_layer.py's derive_shared_key
// exactly. Hand-rolled via createHmac rather than crypto.hkdfSync so this
// also runs on Node versions older than the built-in HKDF API (15+).
function hkdfSha256(ikm, salt, infoStr, length) {
  const info = Buffer.from(infoStr);
  const prk = crypto.createHmac('sha256', salt).update(ikm).digest();
  let t = Buffer.alloc(0);
  let okm = Buffer.alloc(0);
  const n = Math.ceil(length / 32);
  for (let i = 1; i <= n; i++) {
    t = crypto.createHmac('sha256', prk)
      .update(Buffer.concat([t, info, Buffer.from([i])]))
      .digest();
    okm = Buffer.concat([okm, t]);
  }
  return okm.subarray(0, length);
}

// raw 32-byte public keys travel hex-encoded on the wire; Node's KeyObject
// API only speaks X.509/SPKI DER, so wrap/unwrap with the standard SPKI
// prefix for each key type (same trick the Ruby/C++ clients use).
const ED25519_DER_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');
const X25519_DER_PREFIX = Buffer.from('302a300506032b656e032100', 'hex');

function rawPubHex(keyObject) {
  const der = keyObject.export({ type: 'spki', format: 'der' });
  return der.subarray(der.length - 32).toString('hex');
}

function ed25519PubFromRawHex(hex) {
  const der = Buffer.concat([ED25519_DER_PREFIX, Buffer.from(hex, 'hex')]);
  return crypto.createPublicKey({ key: der, format: 'der', type: 'spki' });
}

function x25519PubFromRawHex(hex) {
  const der = Buffer.concat([X25519_DER_PREFIX, Buffer.from(hex, 'hex')]);
  return crypto.createPublicKey({ key: der, format: 'der', type: 'spki' });
}

const host = process.argv[2] || '127.0.0.1';
const port = parseInt(process.argv[3] || '9501', 10);

const nodeId = sha256Hex('js-interop-node').slice(0, 16);
const strandHex = '0'.repeat(64);

const { publicKey: signingPub, privateKey: signingPriv } = crypto.generateKeyPairSync('ed25519');
const { publicKey: exchangePub, privateKey: exchangePriv } = crypto.generateKeyPairSync('x25519');

const mySigningPubHex = rawPubHex(signingPub);
const myExchangePubHex = rawPubHex(exchangePub);

const toSign = `${nodeId}|${strandHex}|${myExchangePubHex}`;
const sig = crypto.sign(null, Buffer.from(toSign), signingPriv);

const hello = {
  type: 'hello', node_id: nodeId, listen_port: 0, strand_hex: strandHex,
  signing_pub: mySigningPubHex, exchange_pub: myExchangePubHex, sig: sig.toString('hex'),
};

console.log(`[js] connecting to ${host}:${port}`);
const socket = net.createConnection({ host, port }, () => {
  socket.write(JSON.stringify(hello) + '\n');
  console.log(`[js] sent hello (node_id=${nodeId})`);
});

let buffered = '';
socket.on('data', (chunk) => {
  buffered += chunk.toString('utf8');
  const nl = buffered.indexOf('\n');
  if (nl === -1) return;
  const line = buffered.slice(0, nl);
  buffered = buffered.slice(nl + 1);
  console.log('[js] received:', line);

  const ack = JSON.parse(line);
  const toVerify = `${ack.node_id}|${ack.strand_hex}|${ack.exchange_pub}`;
  const peerSigningPub = ed25519PubFromRawHex(ack.signing_pub);
  const sigOk = crypto.verify(null, Buffer.from(toVerify), peerSigningPub, Buffer.from(ack.sig, 'hex'));
  console.log('[js] Python node signature verified:', sigOk);
  if (!sigOk) { socket.destroy(); process.exit(1); }

  const peerExchangePub = x25519PubFromRawHex(ack.exchange_pub);
  const sharedSecret = crypto.diffieHellman({ privateKey: exchangePriv, publicKey: peerExchangePub });

  const salt = Buffer.alloc(32); // 32 zero bytes -- matches HKDF's salt=None default (RFC 5869)
  const sessionKey = hkdfSha256(sharedSecret, salt, 'network-os-session-v1', 32);
  console.log('[js] derived session key:', sessionKey.toString('hex'));

  const blockId = 'js-000001';
  const source = 'js_interop_node';
  const featureHash = sha256Hex('js-originated-event');
  const confidence = 1;
  const timestamp = Math.floor(Date.now() / 1000);

  const canonical = `{"block_id": "${blockId}", "confidence": ${confidence}, "feature_hash": "${featureHash}", "source": "${source}", "timestamp": ${timestamp}}`;
  const blockSig = crypto.sign(null, Buffer.from(canonical), signingPriv);

  const signedBlock = {
    block_id: blockId, confidence, feature_hash: featureHash,
    source, timestamp, sig: blockSig.toString('hex'),
  };
  const innerPlaintext = JSON.stringify({ type: 'block', block: signedBlock });

  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', sessionKey, nonce);
  cipher.setAAD(Buffer.from(nodeId));
  const ciphertext = Buffer.concat([cipher.update(innerPlaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();

  const payload = Buffer.concat([nonce, ciphertext, tag]);
  const envelope = { type: 'enc', node_id: nodeId, payload: payload.toString('hex') };
  socket.write(JSON.stringify(envelope) + '\n');
  console.log('[js] sent signed, AES-256-GCM-encrypted block to Python node');

  setTimeout(() => {
    socket.end();
    console.log('[js] done, closing');
  }, 300);
});

socket.on('error', (err) => {
  console.error('[js] connect failed:', err.message);
  process.exit(1);
});
