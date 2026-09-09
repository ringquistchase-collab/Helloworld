// JavaInteropClient.java
// A real Java client speaking network_os.py's wire protocol (PROTOCOL.md).
// Uses the JDK's built-in Ed25519 (JEP 339, Java 15+), X25519 (JEP 324,
// Java 11+), and AES-256-GCM (javax.crypto) -- no external libraries.
// HKDF-SHA256 is hand-rolled via javax.crypto.Mac since the JDK has no
// built-in HKDF API.
//
// SUPERSEDES an earlier draft of this file that assumed network_os.py had
// no reply to "hello", no signatures, and no encryption -- that was true
// of a much earlier version of this project. network_os.py now runs a
// real Ed25519-signed hello/hello_ack handshake and encrypts everything
// after it with AES-256-GCM under an X25519-derived session key; this
// version matches that.
//
// Run directly (Java 15+ single-file source launcher, no separate
// compile step needed):
//     java JavaInteropClient.java 127.0.0.1 9501

import java.io.*;
import java.net.*;
import java.security.*;
import java.security.spec.*;
import java.util.Arrays;
import java.util.regex.*;
import javax.crypto.*;
import javax.crypto.spec.*;

public class JavaInteropClient {

    static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    static byte[] hexToBytes(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++)
            out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        return out;
    }

    static String sha256Hex(byte[] data) throws Exception {
        return bytesToHex(MessageDigest.getInstance("SHA-256").digest(data));
    }

    // minimal flat-JSON field extraction -- sufficient for this
    // protocol's flat hello/hello_ack messages, not a general JSON parser
    static String extractField(String json, String key) {
        Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"").matcher(json);
        return m.find() ? m.group(1) : null;
    }

    // HKDF-SHA256 (RFC 5869) -- matching crypto_layer.py's derive_shared_key exactly
    static byte[] hkdfSha256(byte[] ikm, byte[] salt, String info, int length) throws Exception {
        Mac extractMac = Mac.getInstance("HmacSHA256");
        extractMac.init(new SecretKeySpec(salt, "HmacSHA256"));
        byte[] prk = extractMac.doFinal(ikm);

        byte[] infoBytes = info.getBytes("UTF-8");
        byte[] t = new byte[0];
        ByteArrayOutputStream okm = new ByteArrayOutputStream();
        int n = (length + 31) / 32;
        for (int i = 1; i <= n; i++) {
            Mac expandMac = Mac.getInstance("HmacSHA256");
            expandMac.init(new SecretKeySpec(prk, "HmacSHA256"));
            expandMac.update(t);
            expandMac.update(infoBytes);
            expandMac.update((byte) i);
            t = expandMac.doFinal();
            okm.write(t);
        }
        return Arrays.copyOf(okm.toByteArray(), length);
    }

    // raw 32-byte public keys travel hex-encoded on the wire; the JDK's
    // KeyFactory/PublicKey API only speaks X.509/SPKI DER, so wrap/unwrap
    // with the standard SPKI prefix for each key type (same trick the
    // Ruby/C++/JS clients use). Both Ed25519 and X25519 raw keys are
    // always the last 32 bytes of their SPKI encoding.
    static final byte[] ED25519_DER_PREFIX = hexToBytes("302a300506032b6570032100");
    static final byte[] X25519_DER_PREFIX = hexToBytes("302a300506032b656e032100");

    static String rawPubHex(PublicKey pub) {
        byte[] der = pub.getEncoded();
        return bytesToHex(Arrays.copyOfRange(der, der.length - 32, der.length));
    }

    static byte[] concat(byte[] a, byte[] b) {
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    static PublicKey ed25519PubFromRawHex(String hex) throws Exception {
        byte[] der = concat(ED25519_DER_PREFIX, hexToBytes(hex));
        return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(der));
    }

    static PublicKey x25519PubFromRawHex(String hex) throws Exception {
        byte[] der = concat(X25519_DER_PREFIX, hexToBytes(hex));
        return KeyFactory.getInstance("X25519").generatePublic(new X509EncodedKeySpec(der));
    }

    public static void main(String[] args) throws Exception {
        String host = args.length > 0 ? args[0] : "127.0.0.1";
        int port = args.length > 1 ? Integer.parseInt(args[1]) : 9501;

        String nodeId = sha256Hex("java-interop-node".getBytes()).substring(0, 16);
        String strandHex = "0".repeat(64);

        KeyPair signingKeyPair = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        KeyPair exchangeKeyPair = KeyPairGenerator.getInstance("X25519").generateKeyPair();

        String mySigningPubHex = rawPubHex(signingKeyPair.getPublic());
        String myExchangePubHex = rawPubHex(exchangeKeyPair.getPublic());

        String toSign = nodeId + "|" + strandHex + "|" + myExchangePubHex;
        Signature signer = Signature.getInstance("Ed25519");
        signer.initSign(signingKeyPair.getPrivate());
        signer.update(toSign.getBytes("UTF-8"));
        String sigHex = bytesToHex(signer.sign());

        String hello = String.format(
            "{\"type\": \"hello\", \"node_id\": \"%s\", \"listen_port\": 0, \"strand_hex\": \"%s\", " +
            "\"signing_pub\": \"%s\", \"exchange_pub\": \"%s\", \"sig\": \"%s\"}",
            nodeId, strandHex, mySigningPubHex, myExchangePubHex, sigHex
        );

        System.out.println("[java] connecting to " + host + ":" + port);
        Socket socket = new Socket(host, port);
        PrintWriter out = new PrintWriter(socket.getOutputStream(), true);
        BufferedReader in = new BufferedReader(new InputStreamReader(socket.getInputStream()));

        out.println(hello);
        System.out.println("[java] sent hello (node_id=" + nodeId + ")");

        String response = in.readLine();
        System.out.println("[java] received: " + response);

        String peerNodeId = extractField(response, "node_id");
        String peerStrandHex = extractField(response, "strand_hex");
        String peerSigningPubHex = extractField(response, "signing_pub");
        String peerExchangePubHex = extractField(response, "exchange_pub");
        String peerSigHex = extractField(response, "sig");

        String toVerify = peerNodeId + "|" + peerStrandHex + "|" + peerExchangePubHex;
        PublicKey peerSigningPub = ed25519PubFromRawHex(peerSigningPubHex);
        Signature verifier = Signature.getInstance("Ed25519");
        verifier.initVerify(peerSigningPub);
        verifier.update(toVerify.getBytes("UTF-8"));
        boolean sigOk = verifier.verify(hexToBytes(peerSigHex));
        System.out.println("[java] Python node signature verified: " + sigOk);
        if (!sigOk) { socket.close(); System.exit(1); }

        PublicKey peerExchangePub = x25519PubFromRawHex(peerExchangePubHex);
        KeyAgreement ka = KeyAgreement.getInstance("X25519");
        ka.init(exchangeKeyPair.getPrivate());
        ka.doPhase(peerExchangePub, true);
        byte[] sharedSecret = ka.generateSecret();

        byte[] salt = new byte[32]; // 32 zero bytes -- matches HKDF's salt=None default (RFC 5869)
        byte[] sessionKey = hkdfSha256(sharedSecret, salt, "network-os-session-v1", 32);
        System.out.println("[java] derived session key: " + bytesToHex(sessionKey));

        String blockId = "java-000001";
        String source = "java_interop_node";
        String featureHash = sha256Hex("java-originated-event".getBytes());
        int confidence = 1;
        long timestamp = System.currentTimeMillis() / 1000L;

        String canonical = String.format(
            "{\"block_id\": \"%s\", \"confidence\": %d, \"feature_hash\": \"%s\", \"source\": \"%s\", \"timestamp\": %d}",
            blockId, confidence, featureHash, source, timestamp
        );
        Signature blockSigner = Signature.getInstance("Ed25519");
        blockSigner.initSign(signingKeyPair.getPrivate());
        blockSigner.update(canonical.getBytes("UTF-8"));
        String blockSigHex = bytesToHex(blockSigner.sign());

        String signedBlock = String.format(
            "{\"block_id\": \"%s\", \"confidence\": %d, \"feature_hash\": \"%s\", \"source\": \"%s\", \"timestamp\": %d, \"sig\": \"%s\"}",
            blockId, confidence, featureHash, source, timestamp, blockSigHex
        );
        String innerPlaintext = "{\"type\": \"block\", \"block\": " + signedBlock + "}";

        byte[] nonce = new byte[12];
        new SecureRandom().nextBytes(nonce);

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(sessionKey, "AES"), new GCMParameterSpec(128, nonce));
        cipher.updateAAD(nodeId.getBytes("UTF-8"));
        byte[] ciphertextAndTag = cipher.doFinal(innerPlaintext.getBytes("UTF-8"));

        byte[] payload = concat(nonce, ciphertextAndTag);

        String envelope = String.format(
            "{\"type\": \"enc\", \"node_id\": \"%s\", \"payload\": \"%s\"}",
            nodeId, bytesToHex(payload)
        );
        out.println(envelope);
        System.out.println("[java] sent signed, AES-256-GCM-encrypted block to Python node");

        Thread.sleep(300);
        socket.close();
        System.out.println("[java] done, closing");
    }
}
