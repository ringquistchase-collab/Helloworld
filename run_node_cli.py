#!/usr/bin/env python3
"""
run_node_cli.py — run ONE real network node, for real cross-machine use.

Where run_consolidated_network.py runs 3 nodes in one process on
127.0.0.1 (a local test), this runs a single node that can bind to a real
network interface and connect out to peers on OTHER machines' real IPs.

Setup, once per node:
    python run_node_cli.py --id 0 --show-key
prints this node's Ed25519 public signing key (creating the key file under
--workdir if needed). Give each node every other node's key via --trust.

Run (machine A, 192.168.1.10):
    python run_node_cli.py --id 0 --port 9601 --bind 0.0.0.0 \\
        --peers 192.168.1.20:9601,192.168.1.21:9601 \\
        --trust 1=<node-1 key> --trust 2=<node-2 key>

and likewise on the other machines with their own --id and the other
nodes' addresses/keys. Every node must share the same --identity-text (or
the identity check will correctly fail — that means they're not the same
network).

Trust model: by default only peers whose keys were given with --trust (or
pinned on an earlier run, saved in <workdir>/keys/) are accepted. --tofu
instead pins whatever key a new node_id first presents, which is fine for a
quick test on a trusted LAN but lets anyone who reaches the port first claim
an unused node_id. A pinned key that later changes is always rejected.

Honest limitation: --bind 0.0.0.0 makes the port reachable from your whole
network (or the internet, if forwarded). Signatures and pinning stop
unknown nodes from getting blocks accepted, but anyone can still connect and
attempt a handshake. For anything beyond a trusted LAN, add a firewall rule
restricting the port to known peer IPs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os

from network_node import NetworkNode
from digital_dna import DigitalDNA
from token_ledger import TokenLedger
from dna_binary_codec import encode_to_dna
from run_consolidated_network import research_enricher, external_info_enricher

DEFAULT_IDENTITY_TEXT = "dna-chain-project default network"


def parse_peers(peers_str: str) -> list[tuple[str, int]]:
    """'host:port,host:port' -> [(host, port), ...]. IPv6 hosts go in
    brackets: [::1]:9601."""
    result = []
    for entry in (peers_str or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, sep, port_str = entry.rpartition(":")
        if not sep or not host:
            raise ValueError(f"peer '{entry}' is not host:port")
        host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        port = int(port_str) if port_str.isdigit() else 0
        if not 1 <= port <= 65535:
            raise ValueError(f"peer '{entry}' has an invalid port")
        result.append((host, port))
    return result


def parse_trust(entries: list[str]) -> dict[int, str]:
    """['1=<64 hex>', '2=<64 hex>,3=<64 hex>', ...] -> {1: '<64 hex>', ...}
    (each --trust may also hold a comma-separated list)"""
    result = {}
    for entry in (e.strip() for group in entries or [] for e in group.split(",")):
        if not entry:
            continue
        node_str, sep, key_hex = entry.partition("=")
        if not sep:
            raise ValueError(f"--trust '{entry}' is not ID=KEY")
        key_hex = key_hex.strip().lower()
        if len(key_hex) != 64 or any(c not in "0123456789abcdef" for c in key_hex):
            raise ValueError(f"--trust '{entry}': key must be 64 hex characters")
        result[int(node_str)] = key_hex
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run one real dna-chain-project network node.")
    p.add_argument("--id", type=int, required=True, help="this node's integer id")
    p.add_argument("--port", type=int, default=9601, help="port this node listens on")
    p.add_argument("--bind", default="127.0.0.1",
                   help="interface to bind (127.0.0.1 for local-only, 0.0.0.0 for LAN/real network)")
    p.add_argument("--peers", default="", help="comma-separated host:port list of OTHER nodes")
    p.add_argument("--trust", action="append", default=[], metavar="ID=KEY",
                   help="pin another node's Ed25519 public key (repeatable, or comma-separated); see --show-key")
    p.add_argument("--tofu", action="store_true",
                   help="also accept (and pin) nodes on first contact without --trust")
    p.add_argument("--show-key", action="store_true", help="print this node's public signing key and exit")
    p.add_argument("--identity-text", default=DEFAULT_IDENTITY_TEXT,
                   help="text every node in this network must share identically")
    p.add_argument("--mine-interval", type=float, default=1.5)
    p.add_argument("--duration", type=float, default=0.0, help="seconds to run, 0 = run until Ctrl+C")
    p.add_argument("--no-research", action="store_true", help="disable the ClinicalTrials.gov enricher")
    p.add_argument("--no-external-info", action="store_true", help="disable the Bitcoin/Ethereum enricher")
    p.add_argument("--workdir", default="./node_data", help="where this node's keys/chain/identity/token files live")
    return p


async def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.id < 2 ** 32:
        parser.error("--id must be between 0 and 4294967295")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        peers = parse_peers(args.peers)
        trusted = parse_trust(args.trust)
    except ValueError as e:
        parser.error(str(e))

    os.makedirs(args.workdir, exist_ok=True)
    keys_dir = os.path.join(args.workdir, "keys")
    passphrase = os.environ.get("DNA_NODE_KEY_PASSPHRASE", "").encode() or None

    identity_strand = encode_to_dna(hashlib.sha256(args.identity_text.encode("utf-8")).digest())
    dna = DigitalDNA(seed_label=f"node-{args.id}",
                     dna_path=os.path.join(args.workdir, f"identity_node-{args.id}.dna.json"))
    ledger = TokenLedger(store_path=os.path.join(args.workdir, f"tokens_node-{args.id}.json"))

    enrichers = []
    if not args.no_research:
        enrichers.append(research_enricher)
    if not args.no_external_info:
        enrichers.append(external_info_enricher)

    try:
        node = NetworkNode(
            node_id=args.id,
            port=args.port,
            peer_ports=None,
            peers=peers,
            bind_host=args.bind,
            dna=dna,
            identity_strand=identity_strand,
            ledger=ledger,
            chain_dir=args.workdir,
            mine_interval=args.mine_interval,
            enrichers=enrichers,
            require_known_peers=not args.tofu,
            signing_key_path=os.path.join(keys_dir, f"node-{args.id}.ed25519.pem"),
            signing_key_passphrase=passphrase,
            known_peers_path=os.path.join(keys_dir, f"node-{args.id}.known_peers.json"),
        )
        for peer_id, key_hex in trusted.items():
            node.trust_peer(peer_id, key_hex)
    except ValueError as e:
        print(f"REFUSING TO START: {e}")
        print(f"If a peer's key change is intended, remove its entry from "
              f"{os.path.join(keys_dir, f'node-{args.id}.known_peers.json')}.")
        return 1

    if args.show_key:
        print(f"node-{args.id} public signing key: {node.signing_pub_hex}")
        print(f"Other nodes trust it with:  --trust {args.id}={node.signing_pub_hex}")
        return 0

    print("=" * 78)
    print(f"REAL NETWORK NODE  id={args.id}  bind={args.bind}:{args.port}")
    print(f"This node's public signing key: {node.signing_pub_hex}")
    print(f"Peers: {peers if peers else '(none — will just mine its own local chain)'}")
    print(f"Pinned peer keys: {sorted(node.peer_signing_keys) or '(none)'}"
          f"{'  [+ trust on first use]' if args.tofu else ''}")
    print(f"Identity strand (must match every peer's): {identity_strand}")
    if args.bind not in ("127.0.0.1", "localhost", "::1"):
        print(f"NOTE: listening on {args.bind} — reachable from other machines. "
              f"Restrict port {args.port} to your peers' IPs with a firewall rule.")
        if args.tofu:
            print("WARNING: --tofu on a network interface lets whoever connects first claim an unused node id.")
    if peers and not node.peer_signing_keys and not args.tofu:
        print("WARNING: no peer keys pinned and --tofu is off, so every peer will be rejected. "
              "Pass --trust ID=KEY for each peer.")
    print("=" * 78)

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(node.run(stop_event))
    try:
        if args.duration > 0:
            await asyncio.sleep(args.duration)
        else:
            # shield: Ctrl+C cancels main(), not the node, so the finally
            # below can stop it cleanly (server closed, chain saved)
            await asyncio.shield(run_task)
    except asyncio.CancelledError:
        pass
    finally:
        stop_event.set()
        if not run_task.done():
            await run_task

    ok, msg = node.chain.verify_chain()
    print("\n" + "=" * 78)
    print(f"node-{args.id} stopped. mined={node.block_counter} received={node.blocks_received}")
    print(f"chain verify: {msg} ({'OK' if ok else 'FAILED'})")
    # Tokens are awarded to a block's ORIGIN by whichever peer verifies it,
    # so this node's ledger tracks what IT awarded to others, never its own
    # balance; a network-wide balance would need peers to gossip ledger
    # updates, which isn't implemented.
    awarded = {f"node-{pid}": ledger.balance(f"node-{pid}") for pid in sorted(node.peer_signing_keys)}
    print(f"tokens THIS node awarded to peers (local view only): {awarded}")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
