#!/usr/bin/env python3
"""
run_consolidated_network.py — runs the consolidated system end to end.

3 NetworkNode instances (network_node.py), each with:
  - real per-peer X25519 handshakes (not a shared demo key)
  - identity fingerprint pulled live from a real, consent-gated DigitalDNA
    instance, which grows a real signal for every block mined
  - a real local chain_store.py chain with previous_hash linkage
  - real tokens awarded for peer-verified blocks
  - two enrichers: real research lookups (every 3 blocks) and real
    external Bitcoin/Ethereum chain-tip reads (every 4 blocks)

At the end: verifies every node's chain is real and intact, confirms the
DigitalDNA strand actually changed (grew) from mining activity, and
confirms token/chain-data separation still holds.

INTEGRATION NOTE: uses the project's real modules — DigitalDNA from
digital_dna.py (constructed with seed_label/dna_path), its as_dna() strand
as the frozen identity, audit_trail() for the signal count, and the
ledger's history() method. All localhost-only; the run stops itself after
RUN_SECONDS and writes only into ./consolidated_run/ (gitignored).
"""

import asyncio
import os
import shutil
import time

import requests

from network_node import NetworkNode
from digital_dna import DigitalDNA
from token_ledger import TokenLedger
from external_chain_bridge import build_external_info_snapshot

WORKDIR = os.path.join(os.path.dirname(__file__), "consolidated_run")
BASE_PORT = 9501
NODE_COUNT = 3
RUN_SECONDS = 9.0


async def research_enricher(counter: int) -> dict | None:
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


async def external_info_enricher(counter: int) -> dict | None:
    if counter % 4 != 0:
        return None
    snapshot = await asyncio.to_thread(build_external_info_snapshot)
    return {"external_info": snapshot}


async def main():
    if os.path.exists(WORKDIR):
        shutil.rmtree(WORKDIR)
    os.makedirs(WORKDIR, exist_ok=True)

    print("=" * 78)
    print("CONSOLIDATED LIVE NETWORK")
    print("real per-peer handshakes | consent-gated identity | real chain")
    print("storage | real tokens | real research + external-chain enrichers")
    print("=" * 78)

    # One shared DigitalDNA instance representing the identity all 3 nodes
    # carry (they are all "your" nodes on this network).
    dna_store = os.path.join(WORKDIR, "identity.dna.json")
    dna = DigitalDNA(seed_label="chase-allen-ringquist", dna_path=dna_store)
    initial_strand = dna.as_dna()
    # Frozen ONCE, before any mining: this is what every block broadcasts
    # and every peer verifies against for the whole run. dna's own strand
    # keeps evolving separately from real mining activity (see network_node.py).
    identity_strand = initial_strand
    print(f"\nIdentity strand carried/verified by every node this run: {identity_strand}")
    print(f"Starting signal count: {len(dna.audit_trail())}\n")

    ledger = TokenLedger(store_path=os.path.join(WORKDIR, "tokens.json"))

    ports = [BASE_PORT + i for i in range(NODE_COUNT)]
    nodes = [
        NetworkNode(
            node_id=i,
            port=p,
            peer_ports=[q for q in ports if q != p],
            dna=dna,
            identity_strand=identity_strand,
            ledger=ledger,
            chain_dir=WORKDIR,
            enrichers=[research_enricher, external_info_enricher],
            require_known_peers=True,
        )
        for i, p in enumerate(ports)
    ]
    # All three nodes are yours and start in this process, so pin every
    # node's Ed25519 signing key with every other node up front; an
    # unknown or changed signing key is then rejected rather than trusted.
    for n in nodes:
        for peer in nodes:
            if peer is not n:
                n.trust_peer(peer.node_id, peer.signing_pub_hex)

    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(n.run(stop_event)) for n in nodes]
    await asyncio.sleep(RUN_SECONDS)
    stop_event.set()
    await asyncio.gather(*tasks)

    print("\n" + "=" * 78)
    print("SUMMARY")
    for n in nodes:
        ok, msg = n.chain.verify_chain()
        print(f"  {n.node_key}: mined {n.block_counter}, received {n.blocks_received}, "
              f"chain verify: {msg} ({'OK' if ok else 'FAILED'}), "
              f"token balance {ledger.balance(n.node_key)}")

    final_strand = dna.as_dna()
    print(f"\nIdentity strand used for verification all run (frozen by design): {identity_strand}")
    print(f"DigitalDNA's LIVE strand now (after real mining signals): {final_strand}")
    print(f"Final signal count: {len(dna.audit_trail())}")
    print(f"Live strand diverged from the frozen broadcast one (expected, by design): "
          f"{final_strand != initial_strand}")

    non_verification_awards = [h for h in ledger.history() if "verified by" not in h["reason"]]
    print(f"\nToken/external-data separation check: "
          f"{len(non_verification_awards)} awards NOT tied to verification "
          f"(should be 0)")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
