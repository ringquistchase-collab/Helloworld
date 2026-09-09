// interop_client.cpp
// A real C++ client speaking network_os.py's wire protocol (PROTOCOL.md).
// Uses OpenSSL (libssl/libcrypto) for Ed25519/X25519/AES-256-GCM/SHA-256,
// and raw POSIX sockets for the TCP transport.
//
// Build:  g++ -std=c++17 interop_client.cpp -lssl -lcrypto -o interop_client
// Run:    ./interop_client 127.0.0.1 9501

#include <iostream>
#include <string>
#include <sstream>
#include <iomanip>
#include <regex>
#include <cstring>
#include <chrono>
#include <thread>

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/sha.h>
#include <openssl/hmac.h>

std::string bytesToHex(const unsigned char* data, size_t len) {
    std::ostringstream oss;
    for (size_t i = 0; i < len; i++)
        oss << std::hex << std::setw(2) << std::setfill('0') << (int)data[i];
    return oss.str();
}

std::vector<unsigned char> hexToBytes(const std::string& hex) {
    std::vector<unsigned char> out(hex.size() / 2);
    for (size_t i = 0; i < out.size(); i++)
        out[i] = (unsigned char)std::stoi(hex.substr(i * 2, 2), nullptr, 16);
    return out;
}

std::string sha256Hex(const std::string& data) {
    unsigned char hash[SHA256_DIGEST_LENGTH];
    SHA256((const unsigned char*)data.data(), data.size(), hash);
    return bytesToHex(hash, SHA256_DIGEST_LENGTH);
}

// minimal flat-JSON field extraction — matches the same approach used
// in the Java client, sufficient for this protocol's flat messages
std::string extractField(const std::string& json, const std::string& key) {
    std::regex re("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
    std::smatch m;
    if (std::regex_search(json, m, re)) return m[1].str();
    return "";
}

// raw 32-byte public key from an OpenSSL EVP_PKEY (Ed25519 or X25519 —
// both use the same raw-public-key extraction call)
std::string rawPubHex(EVP_PKEY* pkey) {
    unsigned char raw[32];
    size_t len = 32;
    EVP_PKEY_get_raw_public_key(pkey, raw, &len);
    return bytesToHex(raw, len);
}

EVP_PKEY* pkeyFromRawPubHex(const std::string& hex, int nid) {
    auto raw = hexToBytes(hex);
    return EVP_PKEY_new_raw_public_key(nid, nullptr, raw.data(), raw.size());
}

// HKDF-SHA256 (RFC 5869) — matching crypto_layer.py's derive_shared_key exactly
std::vector<unsigned char> hkdfSha256(const std::vector<unsigned char>& ikm,
                                       const std::vector<unsigned char>& salt,
                                       const std::string& info, size_t length) {
    unsigned char prk[EVP_MAX_MD_SIZE];
    unsigned int prkLen;
    HMAC(EVP_sha256(), salt.data(), salt.size(), ikm.data(), ikm.size(), prk, &prkLen);

    std::vector<unsigned char> okm;
    std::vector<unsigned char> t;
    size_t n = (length + 31) / 32;
    for (size_t i = 1; i <= n; i++) {
        std::vector<unsigned char> input(t);
        input.insert(input.end(), info.begin(), info.end());
        input.push_back((unsigned char)i);
        unsigned char out[EVP_MAX_MD_SIZE];
        unsigned int outLen;
        HMAC(EVP_sha256(), prk, prkLen, input.data(), input.size(), out, &outLen);
        t.assign(out, out + outLen);
        okm.insert(okm.end(), t.begin(), t.end());
    }
    okm.resize(length);
    return okm;
}

int main(int argc, char** argv) {
    std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    int port = argc > 2 ? std::stoi(argv[2]) : 9501;

    std::string nodeId = sha256Hex("cpp-interop-node").substr(0, 16);
    std::string strandHex(64, '0');

    // generate Ed25519 signing keypair
    EVP_PKEY* signingKey = EVP_PKEY_Q_keygen(nullptr, nullptr, "ED25519");
    // generate X25519 exchange keypair
    EVP_PKEY* exchangeKey = EVP_PKEY_Q_keygen(nullptr, nullptr, "X25519");

    std::string mySigningPubHex = rawPubHex(signingKey);
    std::string myExchangePubHex = rawPubHex(exchangeKey);

    std::string toSign = nodeId + "|" + strandHex + "|" + myExchangePubHex;
    unsigned char sig[64];
    size_t sigLen = sizeof(sig);
    EVP_MD_CTX* mdctx = EVP_MD_CTX_new();
    EVP_DigestSignInit(mdctx, nullptr, nullptr, nullptr, signingKey);
    EVP_DigestSign(mdctx, sig, &sigLen, (const unsigned char*)toSign.data(), toSign.size());
    EVP_MD_CTX_free(mdctx);
    std::string sigHex = bytesToHex(sig, sigLen);

    std::ostringstream hello;
    hello << "{\"type\": \"hello\", \"node_id\": \"" << nodeId
          << "\", \"listen_port\": 0, \"strand_hex\": \"" << strandHex
          << "\", \"signing_pub\": \"" << mySigningPubHex
          << "\", \"exchange_pub\": \"" << myExchangePubHex
          << "\", \"sig\": \"" << sigHex << "\"}";

    std::cout << "[cpp] connecting to " << host << ":" << port << std::endl;

    int sock = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    inet_pton(AF_INET, host.c_str(), &addr.sin_addr);
    if (connect(sock, (sockaddr*)&addr, sizeof(addr)) != 0) {
        std::cerr << "[cpp] connect failed" << std::endl;
        return 1;
    }

    std::string helloLine = hello.str() + "\n";
    send(sock, helloLine.data(), helloLine.size(), 0);
    std::cout << "[cpp] sent hello (node_id=" << nodeId << ")" << std::endl;

    char buf[65536];
    ssize_t n = recv(sock, buf, sizeof(buf) - 1, 0);
    buf[n] = 0;
    std::string response(buf);
    std::cout << "[cpp] received: " << response << std::endl;

    std::string peerNodeId = extractField(response, "node_id");
    std::string peerStrandHex = extractField(response, "strand_hex");
    std::string peerSigningPubHex = extractField(response, "signing_pub");
    std::string peerExchangePubHex = extractField(response, "exchange_pub");
    std::string peerSigHex = extractField(response, "sig");

    std::string toVerify = peerNodeId + "|" + peerStrandHex + "|" + peerExchangePubHex;
    EVP_PKEY* peerSigningPub = pkeyFromRawPubHex(peerSigningPubHex, EVP_PKEY_ED25519);
    auto sigBytes = hexToBytes(peerSigHex);
    EVP_MD_CTX* vctx = EVP_MD_CTX_new();
    EVP_DigestVerifyInit(vctx, nullptr, nullptr, nullptr, peerSigningPub);
    int sigOk = EVP_DigestVerify(vctx, sigBytes.data(), sigBytes.size(),
                                   (const unsigned char*)toVerify.data(), toVerify.size());
    EVP_MD_CTX_free(vctx);
    std::cout << "[cpp] Python node signature verified: " << (sigOk == 1 ? "true" : "false") << std::endl;
    if (sigOk != 1) { close(sock); return 1; }

    EVP_PKEY* peerExchangePub = pkeyFromRawPubHex(peerExchangePubHex, EVP_PKEY_X25519);
    EVP_PKEY_CTX* ectx = EVP_PKEY_CTX_new(exchangeKey, nullptr);
    EVP_PKEY_derive_init(ectx);
    EVP_PKEY_derive_set_peer(ectx, peerExchangePub);
    size_t secretLen;
    EVP_PKEY_derive(ectx, nullptr, &secretLen);
    std::vector<unsigned char> sharedSecret(secretLen);
    EVP_PKEY_derive(ectx, sharedSecret.data(), &secretLen);
    EVP_PKEY_CTX_free(ectx);

    std::vector<unsigned char> salt(32, 0);
    auto sessionKey = hkdfSha256(sharedSecret, salt, "network-os-session-v1", 32);
    std::cout << "[cpp] derived session key: " << bytesToHex(sessionKey.data(), sessionKey.size()) << std::endl;

    // build + sign a real block
    std::string blockId = "cpp-000001";
    std::string source = "cpp_interop_node";
    std::string featureHash = sha256Hex("cpp-originated-event");
    int confidence = 1;
    long timestamp = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();

    std::ostringstream canonical;
    canonical << "{\"block_id\": \"" << blockId << "\", \"confidence\": " << confidence
               << ", \"feature_hash\": \"" << featureHash << "\", \"source\": \"" << source
               << "\", \"timestamp\": " << timestamp << "}";

    unsigned char blockSig[64];
    size_t blockSigLen = sizeof(blockSig);
    EVP_MD_CTX* bctx = EVP_MD_CTX_new();
    EVP_DigestSignInit(bctx, nullptr, nullptr, nullptr, signingKey);
    std::string canonicalStr = canonical.str();
    EVP_DigestSign(bctx, blockSig, &blockSigLen, (const unsigned char*)canonicalStr.data(), canonicalStr.size());
    EVP_MD_CTX_free(bctx);

    std::ostringstream signedBlock;
    signedBlock << "{\"block_id\": \"" << blockId << "\", \"confidence\": " << confidence
                 << ", \"feature_hash\": \"" << featureHash << "\", \"source\": \"" << source
                 << "\", \"timestamp\": " << timestamp
                 << ", \"sig\": \"" << bytesToHex(blockSig, blockSigLen) << "\"}";

    std::string innerPlaintext = "{\"type\": \"block\", \"block\": " + signedBlock.str() + "}";

    unsigned char nonce[12];
    RAND_bytes(nonce, sizeof(nonce));

    EVP_CIPHER_CTX* cctx = EVP_CIPHER_CTX_new();
    EVP_EncryptInit_ex(cctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr);
    EVP_CIPHER_CTX_ctrl(cctx, EVP_CTRL_GCM_SET_IVLEN, 12, nullptr);
    EVP_EncryptInit_ex(cctx, nullptr, nullptr, sessionKey.data(), nonce);

    int outLen;
    std::vector<unsigned char> ciphertext(innerPlaintext.size() + 16);
    EVP_EncryptUpdate(cctx, nullptr, &outLen, (const unsigned char*)nodeId.data(), nodeId.size()); // AAD
    EVP_EncryptUpdate(cctx, ciphertext.data(), &outLen, (const unsigned char*)innerPlaintext.data(), innerPlaintext.size());
    int ciphertextLen = outLen;
    EVP_EncryptFinal_ex(cctx, ciphertext.data() + outLen, &outLen);
    ciphertextLen += outLen;

    unsigned char tag[16];
    EVP_CIPHER_CTX_ctrl(cctx, EVP_CTRL_GCM_GET_TAG, 16, tag);
    EVP_CIPHER_CTX_free(cctx);

    std::vector<unsigned char> payload;
    payload.insert(payload.end(), nonce, nonce + 12);
    payload.insert(payload.end(), ciphertext.begin(), ciphertext.begin() + ciphertextLen);
    payload.insert(payload.end(), tag, tag + 16);

    std::ostringstream envelope;
    envelope << "{\"type\": \"enc\", \"node_id\": \"" << nodeId
              << "\", \"payload\": \"" << bytesToHex(payload.data(), payload.size()) << "\"}\n";

    std::string envStr = envelope.str();
    send(sock, envStr.data(), envStr.size(), 0);
    std::cout << "[cpp] sent signed, AES-256-GCM-encrypted block to Python node" << std::endl;

    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    close(sock);
    std::cout << "[cpp] done, closing" << std::endl;

    return 0;
}
