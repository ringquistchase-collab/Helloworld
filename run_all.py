"""
run_all.py
================
Entry point that starts what's actually real in this project today:
the P2P chain (digital_dna.py + network_os.py), under one verified
project identifier (project_identifier.py).

    python3 run_all.py

WHAT THIS ACTUALLY STARTS
------------------------------
1. Computes and prints the real project_id (see project_identifier.py)
   — so every run is tied to a specific, verifiable state of the code
2. One NetworkNode + DigitalDNA (real P2P chain, real crypto)

WHAT THIS DOESN'T DO (YET)
---------------------------------
This project doesn't have a token_ledger.py, ai_gossip.py,
integrated_research_agent.py, or audit_trail.py module. An earlier
draft of this file imported and wired all four in — that draft can't
run against what actually exists here. When those modules exist,
wire them in the same way NetworkNode already accepts an optional
`audit=` parameter (see network_os.py's own NetworkNode.__init__).

Doesn't connect to any peer automatically (uncomment the
connect_peer line if you have one). Nothing here silently reaches
further than what's explicitly configured below.
"""

import asyncio
import signal

from digital_dna import DigitalDNA
from network_os import NetworkNode
from project_identifier import compute_project_identifier, save_manifest

# ---- edit these for your real use ----
SEED_LABEL = "my-research-node"
HOST, PORT = "0.0.0.0", 8765
# ----------------------------------------


async def main():
    manifest = compute_project_identifier()
    save_manifest(manifest)
    print(f"=== project_id: {manifest['project_id']} ===")
    print(f"({manifest['file_count']} files verified — this run is tied to this exact code state)\n")

    dna = DigitalDNA(seed_label=SEED_LABEL, dna_path="dna_state.json")
    node = NetworkNode(dna, host=HOST, port=PORT, ledger_store_path="network_ledger.json")
    await node.start()
    print(f"[node] listening on {HOST}:{PORT}, node_id={node.node_id}")

    # uncomment to connect to a real peer:
    # await node.connect_peer("<their IP>", <their port>)

    print(f"\n[run_all] running under project_id {manifest['project_id'][:16]}...")
    print("[run_all] Ctrl+C to stop\n")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[shutdown] stopping cleanly...")
        await node.stop()
        print("[shutdown] done. All state saved — rerun to continue.")


if __name__ == "__main__":
    asyncio.run(main())
