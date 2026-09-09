// interop_client.go
// A real Go client speaking network_os.py's wire protocol (PROTOCOL.md).
// Pure standard library — crypto/ed25519, crypto/ecdh (X25519, stdlib
// since Go 1.20), crypto/cipher (AES-GCM), crypto/hmac (for hand-rolled
// HKDF, since HKDF itself isn't in stdlib until Go 1.24). No go.sum,
// no external modules to fetch.
//
// Run:  go run interop_client.go 127.0.0.1 9501

package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"strconv"
	"time"
)

func sha256Hex(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:])
}

// HKDF-SHA256 (RFC 5869) — matching crypto_layer.py's derive_shared_key exactly
func hkdfSha256(ikm, salt []byte, info string, length int) []byte {
	extractMac := hmac.New(sha256.New, salt)
	extractMac.Write(ikm)
	prk := extractMac.Sum(nil)

	var t, okm []byte
	n := (length + 31) / 32
	for i := 1; i <= n; i++ {
		expandMac := hmac.New(sha256.New, prk)
		expandMac.Write(t)
		expandMac.Write([]byte(info))
		expandMac.Write([]byte{byte(i)})
		t = expandMac.Sum(nil)
		okm = append(okm, t...)
	}
	return okm[:length]
}

type HelloMsg struct {
	Type        string `json:"type"`
	NodeID      string `json:"node_id"`
	ListenPort  int    `json:"listen_port"`
	StrandHex   string `json:"strand_hex"`
	SigningPub  string `json:"signing_pub"`
	ExchangePub string `json:"exchange_pub"`
	Sig         string `json:"sig"`
}

type Block struct {
	BlockID     string `json:"block_id"`
	Confidence  int    `json:"confidence"`
	FeatureHash string `json:"feature_hash"`
	Source      string `json:"source"`
	Timestamp   int64  `json:"timestamp"`
	Sig         string `json:"sig,omitempty"`
}

func main() {
	host := "127.0.0.1"
	port := "9501"
	if len(os.Args) > 1 {
		host = os.Args[1]
	}
	if len(os.Args) > 2 {
		port = os.Args[2]
	}

	nodeID := sha256Hex("go-interop-node")[:16]
	strandHex := ""
	for i := 0; i < 64; i++ {
		strandHex += "0"
	}

	signingPub, signingPriv, _ := ed25519.GenerateKey(rand.Reader)
	exchangePriv, _ := ecdh.X25519().GenerateKey(rand.Reader)
	exchangePub := exchangePriv.PublicKey()

	mySigningPubHex := hex.EncodeToString(signingPub)
	myExchangePubHex := hex.EncodeToString(exchangePub.Bytes())

	toSign := nodeID + "|" + strandHex + "|" + myExchangePubHex
	sig := ed25519.Sign(signingPriv, []byte(toSign))

	hello := HelloMsg{
		Type: "hello", NodeID: nodeID, ListenPort: 0, StrandHex: strandHex,
		SigningPub: mySigningPubHex, ExchangePub: myExchangePubHex, Sig: hex.EncodeToString(sig),
	}
	helloJSON, _ := json.Marshal(hello)

	fmt.Printf("[go] connecting to %s:%s\n", host, port)
	conn, err := net.Dial("tcp", host+":"+port)
	if err != nil {
		fmt.Println("[go] connect failed:", err)
		os.Exit(1)
	}
	defer conn.Close()

	conn.Write(append(helloJSON, '\n'))
	fmt.Printf("[go] sent hello (node_id=%s)\n", nodeID)

	buf := make([]byte, 65536)
	n, _ := conn.Read(buf)
	response := buf[:n]
	fmt.Println("[go] received:", string(response))

	var ack map[string]string
	json.Unmarshal(response, &ack)

	toVerify := ack["node_id"] + "|" + ack["strand_hex"] + "|" + ack["exchange_pub"]
	peerSigningPubBytes, _ := hex.DecodeString(ack["signing_pub"])
	peerSig, _ := hex.DecodeString(ack["sig"])
	sigOk := ed25519.Verify(ed25519.PublicKey(peerSigningPubBytes), []byte(toVerify), peerSig)
	fmt.Println("[go] Python node signature verified:", sigOk)
	if !sigOk {
		os.Exit(1)
	}

	peerExchangePubBytes, _ := hex.DecodeString(ack["exchange_pub"])
	peerExchangePub, _ := ecdh.X25519().NewPublicKey(peerExchangePubBytes)
	sharedSecret, _ := exchangePriv.ECDH(peerExchangePub)

	salt := make([]byte, 32)
	sessionKey := hkdfSha256(sharedSecret, salt, "network-os-session-v1", 32)
	fmt.Println("[go] derived session key:", hex.EncodeToString(sessionKey))

	blockID := "go-000001"
	source := "go_interop_node"
	featureHash := sha256Hex("go-originated-event")
	confidence := 1
	timestamp := time.Now().Unix()

	canonical := fmt.Sprintf(
		`{"block_id": "%s", "confidence": %d, "feature_hash": "%s", "source": "%s", "timestamp": %d}`,
		blockID, confidence, featureHash, source, timestamp,
	)
	blockSig := ed25519.Sign(signingPriv, []byte(canonical))

	block := Block{
		BlockID: blockID, Confidence: confidence, FeatureHash: featureHash,
		Source: source, Timestamp: timestamp, Sig: hex.EncodeToString(blockSig),
	}
	blockJSON, _ := json.Marshal(block)
	innerPlaintext := fmt.Sprintf(`{"type": "block", "block": %s}`, blockJSON)

	nonce := make([]byte, 12)
	rand.Read(nonce)

	aesBlock, _ := aes.NewCipher(sessionKey)
	gcm, _ := cipher.NewGCM(aesBlock)
	ciphertext := gcm.Seal(nil, nonce, []byte(innerPlaintext), []byte(nodeID))

	payload := append(nonce, ciphertext...)

	envelope := fmt.Sprintf(`{"type": "enc", "node_id": "%s", "payload": "%s"}`, nodeID, hex.EncodeToString(payload))
	conn.Write([]byte(envelope + "\n"))
	fmt.Println("[go] sent signed, AES-256-GCM-encrypted block to Python node")

	time.Sleep(300 * time.Millisecond)
	fmt.Println("[go] done, closing")
	_ = strconv.Itoa(0)
}
