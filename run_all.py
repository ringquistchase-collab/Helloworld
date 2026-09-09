"""
run_all.py
================
The single entry point that carries and launches everything built in
this project together: the chain (digital_dna.py + network_os.py),
the research agent (growing/integrated), the AI participant
(ai_gossip.py), the CRISPR research suite, and the audit trail, all
under one verified project identifier (project_identifier.py).

    python3 run_all.py

WHAT THIS ACTUALLY STARTS
------------------------------
1. Computes and prints the real project_id (see project_identifier.py)
   — so every run is tied to a specific, verifiable state of the code
2. One NetworkNode + DigitalDNA (real P2P chain, real crypto)
3. One IntegratedResearchAgent (real ClinicalTrials.gov/PubMed/ClinVar
   research + mining + corpus; visuals only if OPENAI_API_KEY is set)
4. One AIParticipant (periodic real analysis of the network ledger)
5. One shared AuditTrail — every module above logs to the SAME file,
   so the whole run is one tamper-evident, chronologically-ordered
   record (see audit_trail.py)
6. One CrisprResearchSuite — literature tracking + guide design,
   deliberately NEVER mining into the chain (see
   crispr_research_suite.py's docstring for why that boundary is
   intentional, not a missing feature)

WHAT IT DOESN'T DO BY ITSELF
---------------------------------
Doesn't connect to any peer automatically (uncomment the
connect_peer line if you have one), doesn't enable image/video
generation unless you set the relevant API key env vars (and video
generation isn't implemented at all yet -- see
integrated_research_agent.py). Nothing here silently reaches further
than what's explicitly configured below.
"""

import asyncio
import os
import signal

from digital_dna import DigitalDNA
from network_os import NetworkNode
from token_ledger import TokenLedger
from ai_gossip import AIParticipant
from integrated_research_agent import IntegratedResearchAgent
from crispr_research_suite import CrisprResearchSuite
from audit_trail import AuditTrail
from project_identifier import compute_project_identifier, save_manifest

# ---- edit these for your real use ----
SEED_LABEL = "my-research-node"
HOST, PORT = "0.0.0.0", 8765
CONDITION = "breast cancer"
BIOMARKER = "BRCA1"
INTERVAL_SECONDS = 3600
AI_PARTICIPANT_INTERVAL_S = 600
# ----------------------------------------


async def main():
    manifest = compute_project_identifier()
    save_manifest(manifest)
    print(f"=== project_id: {manifest['project_id']} ===")
    print(f"({manifest['file_count']} files verified — this run is tied to this exact code state)\n")

    audit = AuditTrail("system_audit.jsonl")

    dna = DigitalDNA(seed_label=SEED_LABEL, dna_path="dna_state.json")
    node = NetworkNode(dna, host=HOST, port=PORT, audit=audit, ledger_store_path="network_ledger.json")
    await node.start()
    print(f"[node] listening on {HOST}:{PORT}, node_id={node.node_id}")

    ledger = TokenLedger(audit=audit, store_path="token_ledger.json")

    agent = IntegratedResearchAgent(
        node, dna, store_path="research_store.json",
        corpus_path="corpus.json", visuals_dir="visuals",
        interval_s=INTERVAL_SECONDS, audit=audit,
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        runway_api_key=os.environ.get("RUNWAYML_API_SECRET"),
    )
    await agent.start()
    print(f"[agent] research agent started, checking every {INTERVAL_SECONDS}s")

    ai = AIParticipant(node, dna, ledger, interval_s=AI_PARTICIPANT_INTERVAL_S, audit=audit)
    await ai.start()
    print(f"[ai] AI participant started, checking every {AI_PARTICIPANT_INTERVAL_S}s")

    key = await agent.seed_topic(CONDITION, biomarker=BIOMARKER)
    t = agent.topics[key]
    print(f"[agent] seeded '{key}': {len(t['new_ids_last_run'])} real results found")

    # CRISPR research suite — SAME shared audit trail, but deliberately
    # standalone: guide-design/gene-annotation/literature-tracking never
    # mine into the chain (see crispr_research_suite.py's docstring for
    # why that boundary is intentional, not a missing feature)
    crispr = CrisprResearchSuite(crispr_store_path="crispr_store.json", audit=audit)
    crispr_result = await crispr.check_research(CONDITION, biomarker=BIOMARKER)
    print(f"[crispr] literature check for '{CONDITION}': {crispr_result['total_ids']} items tracked "
          f"({len(crispr_result['new_ids'])} new this run)")

    # uncomment to connect to a real peer:
    # await node.connect_peer("<their IP>", <their port>)

    print(f"\n[run_all] everything running under project_id {manifest['project_id'][:16]}...")
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
        await agent.stop()
        await ai.stop()
        await node.stop()
        print("[shutdown] done. All state saved — rerun to continue.")


if __name__ == "__main__":
    asyncio.run(main())
