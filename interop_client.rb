#!/usr/bin/env ruby
# interop_client.rb
# A real Ruby client speaking network_os.py's wire protocol (PROTOCOL.md).
# Uses Ruby's built-in openssl and json libraries — no gems needed.
#
# Run: ruby interop_client.rb 127.0.0.1 9501

require 'openssl'
require 'json'
require 'socket'
require 'securerandom'

def sha256_hex(data)
  Digest::SHA256.hexdigest(data)
end

require 'digest'

# HKDF-SHA256 (RFC 5869) — matching crypto_layer.py's derive_shared_key exactly
def hkdf_sha256(ikm, salt, info, length)
  prk = OpenSSL::HMAC.digest('SHA256', salt, ikm)
  t = ''
  okm = ''
  n = (length.to_f / 32).ceil
  (1..n).each do |i|
    t = OpenSSL::HMAC.digest('SHA256', prk, t + info + [i].pack('C'))
    okm += t
  end
  okm[0, length]
end

ED25519_DER_PREFIX = ['302a300506032b6570032100'].pack('H*')
X25519_DER_PREFIX  = ['302a300506032b656e032100'].pack('H*')

def raw_pub_hex(pkey)
  pkey.public_to_der[-32, 32].unpack1('H*')
end

def ed25519_pub_from_raw_hex(hex)
  OpenSSL::PKey.read(ED25519_DER_PREFIX + [hex].pack('H*'))
end

def x25519_pub_from_raw_hex(hex)
  OpenSSL::PKey.read(X25519_DER_PREFIX + [hex].pack('H*'))
end

host = ARGV[0] || '127.0.0.1'
port = (ARGV[1] || 9501).to_i

node_id = sha256_hex('ruby-interop-node')[0, 16]
strand_hex = '0' * 64

signing_key = OpenSSL::PKey.generate_key('ED25519')
exchange_key = OpenSSL::PKey.generate_key('X25519')

my_signing_pub_hex = raw_pub_hex(signing_key)
my_exchange_pub_hex = raw_pub_hex(exchange_key)

to_sign = "#{node_id}|#{strand_hex}|#{my_exchange_pub_hex}"
sig = signing_key.sign(nil, to_sign)
sig_hex = sig.unpack1('H*')

hello = {
  type: 'hello', node_id: node_id, listen_port: 0, strand_hex: strand_hex,
  signing_pub: my_signing_pub_hex, exchange_pub: my_exchange_pub_hex, sig: sig_hex,
}

puts "[ruby] connecting to #{host}:#{port}"
socket = TCPSocket.new(host, port)
socket.puts(JSON.generate(hello))
puts "[ruby] sent hello (node_id=#{node_id})"

response = socket.gets
puts "[ruby] received: #{response}"
ack = JSON.parse(response)

to_verify = "#{ack['node_id']}|#{ack['strand_hex']}|#{ack['exchange_pub']}"
peer_signing_pub = ed25519_pub_from_raw_hex(ack['signing_pub'])
peer_sig = [ack['sig']].pack('H*')
sig_ok = peer_signing_pub.verify(nil, peer_sig, to_verify)
puts "[ruby] Python node signature verified: #{sig_ok}"
exit(1) unless sig_ok

peer_exchange_pub = x25519_pub_from_raw_hex(ack['exchange_pub'])
shared_secret = exchange_key.derive(peer_exchange_pub)

salt = "\x00" * 32
session_key = hkdf_sha256(shared_secret, salt, 'network-os-session-v1', 32)
puts "[ruby] derived session key: #{session_key.unpack1('H*')}"

block_id = 'ruby-000001'
source = 'ruby_interop_node'
feature_hash = sha256_hex('ruby-originated-event')
confidence = 1
timestamp = Time.now.to_i

canonical = "{\"block_id\": \"#{block_id}\", \"confidence\": #{confidence}, " \
            "\"feature_hash\": \"#{feature_hash}\", \"source\": \"#{source}\", \"timestamp\": #{timestamp}}"
block_sig = signing_key.sign(nil, canonical)

signed_block = {
  block_id: block_id, confidence: confidence, feature_hash: feature_hash,
  source: source, timestamp: timestamp, sig: block_sig.unpack1('H*'),
}
inner_plaintext = JSON.generate({ type: 'block', block: signed_block })

nonce = SecureRandom.random_bytes(12)
cipher = OpenSSL::Cipher.new('aes-256-gcm')
cipher.encrypt
cipher.key = session_key
cipher.iv = nonce
cipher.auth_data = node_id
ciphertext = cipher.update(inner_plaintext) + cipher.final
tag = cipher.auth_tag

payload = nonce + ciphertext + tag

envelope = { type: 'enc', node_id: node_id, payload: payload.unpack1('H*') }
socket.puts(JSON.generate(envelope))
puts '[ruby] sent signed, AES-256-GCM-encrypted block to Python node'

sleep(0.3)
socket.close
puts '[ruby] done, closing'
