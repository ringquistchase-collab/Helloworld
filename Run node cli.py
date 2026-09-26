#!/usr/bin/env python3
"""
run_node_cli.py — run ONE real network node, for real cross-machine use.

Where run_consolidated_network.py runs 3 nodes in one process on
127.0.0.1 (a local test), this runs a single node that can bind to a real
network interface and connect out to peers on OTHER machines' real IPs —
actual network operation, not a localhost simulation.

Usage:
    python3 run_node_cli.py --id 0 --port 9601 --bind 0.0.0.0 \\
        --peers 192.168.1.20:9601,192.168.1.21:9601

    On another machine on the same LAN:
    python3 run_node_cli.py --id 1 --port 9601 --bind 0.0.0.0 \\
        --peers 192.168.1.10:9601,192.168.1.21:9601

Every node needs every OTHER node's real IP:port in --peers. All nodes
must share the same --identity-text (or the identity check will correctly
fail — that's not a bug, it means they're not "your" network).

Honest limitation: --bind 0.0.0.0 makes this reachable from your whole
network (or the internet, if the port is forwarded) with only the AES-GCM
session encryption protecting it — no firewall rule, no allowlist of
who's allowed to even attempt a handshake. For anything beyond a trusted
LAN test, add a firewall rule restricting the port to known peer IPs.
"""

import argparse
import asyncio
import hashlib
import os

from network_node import NetworkNode
from digital_dna_mirror_addon import DigitalDNA
from token_ledger import TokenLedger
from dna_binary_codec import encode_to_dna
from external_chain_bridge import build_external_info_snapshot
import requests


def parse_peers(peers_str: str) -> list[tuple]:
    if not peers_str:
        return []
    result = []
    for entry in peers_str.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, port_str = entry.rsplit(":", 1)
        result.append((host, int(port_str)))
    return result


async def research_enricher(counter: int):
    if counter % 3 != 0:
        return None
    try:
        resp = await asyncio.to_thread(
            requests.get,
            "https://clinicaltrials.gov/api/v2/studies",
            params={"query.cond": "leukemia", "pageSize": 1, "format": "json"},
            timeout=8,
        )
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
        ident = studies[0]["protocolSection"]["identificationModule"] if studies else {}
        return {"research": {"source": "clinicaltrials.gov", "id": ident.get("nctId", "?")}}
    except Exception as e:
        return {"research": {"source": "none", "error": str(e)}}


async def external_info_enricher(counter: int):
    if counter % 4 != 0:
        return None
    snapshot = await asyncio.to_thread(build_external_info_snapshot)
    return {"external_info": snapshot}


async def main():
    parser = argparse.ArgumentParser(description="Run one real dna-chain-project network node.")
    parser.add_argument("--id", type=int, required=True, help="this node's integer id")
    parser.add_argument("--port", type=int, required=True, help="port this node listens on")
    parser.add_argument("--bind", default="127.0.0.1", help="interface to bind (127.0.0.1 for local-only, 0.0.0.0 for LAN/real network)")
    parser.add_argument("--peers", default="", help="comma-separated host:port list of OTHER nodes")
    parser.add_argument("--identity-text", default="Chase Allen Ringquist | Bixby, Oklahoma | dna-chain-project",
                         help="text every node in this network must share identically")
    parser.add_argument("--mine-interval", type=float, default=1.5)
    parser.add_argument("--duration", type=float, default=0.0, help="seconds to run, 0 = run until Ctrl+C")
    parser.add_argument("--no-research", action="store_true", help="disable the ClinicalTrials.gov enricher")
    parser.add_argument("--no-external-info", action="store_true", help="disable the Bitcoin/Ethereum enricher")
    parser.add_argument("--workdir", default="./node_data", help="where this node's chain/identity/token files live")
    args = parser.parse_args()

    os.makedirs(args.workdir, exist_ok=True)

    identity_fingerprint = hashlib.sha256(args.identity_text.encode("utf-8")).digest()
    identity_strand = encode_to_dna(identity_fingerprint)

    dna = DigitalDNA(owner_id=f"node-{args.id}", store_path=os.path.join(args.workdir, f"identity_node-{args.id}.json"))
    ledger = TokenLedger(store_path=os.path.join(args.workdir, f"tokens_node-{args.id}.json"))

    enrichers = []
    if not args.no_research:
        enrichers.append(research_enricher)
    if not args.no_external_info:
        enrichers.append(external_info_enricher)

    peers = parse_peers(args.peers)

    print("=" * 78)
    print(f"REAL NETWORK NODE  id={args.id}  bind={args.bind}:{args.port}")
    print(f"Peers: {peers if peers else '(none — will just mine its own local chain)'}")
    print(f"Identity strand (must match every peer's): {identity_strand}")
    print("=" * 78)

    node = NetworkNode(
        node_id=args.id,
        port=args.port,
        peers=peers,
        dna=dna,
        identity_strand=identity_strand,
        ledger=ledger,
        chain_dir=args.workdir,
        bind_host=args.bind,
        mine_interval=args.mine_interval,
        enrichers=enrichers,
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(node.run(stop_event))

    try:
        if args.duration > 0:
            await asyncio.sleep(args.duration)
            stop_event.set()
            await run_task
        else:
            await run_task  # runs until Ctrl+C (KeyboardInterrupt)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_event.set()
        await run_task

    ok, msg = node.chain.verify_chain()
    print("\n" + "=" * 78)
    print(f"node-{args.id} stopped. mined={node.block_counter} received={node.blocks_received}")
    print(f"chain verify: {msg} ({'OK' if ok else 'FAILED'})")
    # NOTE, found while testing this as real separate processes: tokens are
    # awarded to a block's ORIGIN by whichever peer verifies it — so this
    # node's own local ledger tracks what IT awarded to others, never a
    # balance for itself. A node's real "network-wide" balance would need
    # peers to gossip ledger updates back, which isn't implemented — each
    # node only sees the tokens IT personally handed out.
    print(f"tokens THIS node awarded to peers (local view only): {dict(ledger.balances)}")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
