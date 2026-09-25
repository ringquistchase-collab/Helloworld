"""
run_local_network.py
================
ONE command, ONE terminal, no setup: starts a small LOCAL network of two
nodes you control, connects them, runs the research agent on node one,
and keeps going until you press Ctrl+C.

    python run_local_network.py

WHY THIS EXISTS
-------------------
run_agent.py / run_all.py each start a single node, and connecting a
second peer meant a second terminal and hand-editing a connect_peer
line. This does all of that for you in one process: two real NetworkNode
peers, a real Ed25519/X25519/AES-GCM handshake between them, the research
agent seeding a topic on node one and gossiping it (encrypted) to node
two — everything this project does, running together, from one command.

WHERE THE LINE STAYS (read this)
------------------------------------
Both nodes bind to 127.0.0.1 (localhost) — they are NOT reachable from
outside this computer, not exposed to the internet, and not hosted
anywhere. They live in THIS process only and stop the instant you press
Ctrl+C. This is the "networked" version of the project done the safe way:
peers you run, on your own machine, that you start and stop. To add a
peer on a SECOND computer you own, see README / run_agent.py's
connect_peer line — same idea, a LAN IP instead of 127.0.0.1.

All state (identities, ledgers, research store) is saved next to the code
and reloaded on the next run, so stopping and restarting picks up where
you left off.
"""

import asyncio
import signal

from digital_dna import DigitalDNA
from network_os import NetworkNode
from integrated_research_agent import IntegratedResearchAgent
from ai_gossip import AIParticipant
from token_ledger import TokenLedger
from audit_trail import AuditTrail
from project_identifier import compute_project_identifier, save_manifest

# ---- change these if you like; the defaults just work ----
CONDITION = "breast cancer"
BIOMARKER = "BRCA1"
INTERVAL_SECONDS = 3600            # how often the background loop re-checks (hourly is polite to the APIs)
HOST = "127.0.0.1"                 # localhost only — NOT reachable from outside this machine
PORT_ONE, PORT_TWO = 8765, 8766
# ----------------------------------------------------------


async def main():
    manifest = compute_project_identifier()
    save_manifest(manifest)
    print(f"=== project_id: {manifest['project_id'][:16]}...  ({manifest['file_count']} files verified) ===\n")

    audit = AuditTrail("system_audit.jsonl")

    # --- node one: the research node (identity + agent + AI participant) ---
    dna1 = DigitalDNA(seed_label="local-node-one", dna_path="node_one.dna.json")
    node1 = NetworkNode(dna1, host=HOST, port=PORT_ONE, audit=audit,
                        ledger_store_path="node_one_ledger.json")
    await node1.start()
    print(f"[node-one] listening on {HOST}:{PORT_ONE}  id={node1.node_id}")

    # --- node two: a second peer you control, in this same process ---
    dna2 = DigitalDNA(seed_label="local-node-two", dna_path="node_two.dna.json")
    node2 = NetworkNode(dna2, host=HOST, port=PORT_TWO, audit=audit,
                        ledger_store_path="node_two_ledger.json")
    await node2.start()
    print(f"[node-two] listening on {HOST}:{PORT_TWO}  id={node2.node_id}")

    # node two records any topic it hears, so you can watch gossip land
    heard_topics: list[str] = []
    node2.on_topic(lambda t: heard_topics.append(t.get("condition")))

    # --- connect them (a real signed + encrypted handshake) ---
    await node2.connect_peer(HOST, PORT_ONE)
    await asyncio.sleep(0.6)
    linked = (node1.node_id in node2.peers) and (node2.node_id in node1.peers)
    print(f"[net] handshake complete, nodes linked: {linked}\n")

    # --- research agent + AI participant on node one ---
    ledger = TokenLedger(audit=audit, store_path="token_ledger.json")
    agent = IntegratedResearchAgent(
        node1, dna1, store_path="research_store.json",
        corpus_path="corpus.json", visuals_dir="visuals",
        interval_s=INTERVAL_SECONDS, audit=audit,
    )
    await agent.start()
    ai = AIParticipant(node1, dna1, ledger, interval_s=600, audit=audit)
    await ai.start()

    print(f"[agent] seeding '{CONDITION}' / {BIOMARKER}  (real ClinicalTrials.gov + PubMed + ClinVar + HGNC)...")
    key = await agent.seed_topic(CONDITION, biomarker=BIOMARKER)
    topic = agent.topics[key]
    print(f"[agent] {len(topic['new_ids_last_run'])} real result(s) found; "
          f"{len(agent.queue)} related topic(s) queued")

    await asyncio.sleep(0.6)  # let the gossip land on node two
    print(f"[net] node-two received topic gossip: {heard_topics}")
    print(f"[mos] node-one score={node1.mos_score()['mos']}   node-two score={node2.mos_score()['mos']}")

    print("\n[run] Both nodes are live on this machine. It will keep exploring "
          f"related topics every {INTERVAL_SECONDS//60} min.")
    print("[run] Press Ctrl+C to stop — everything saves and you can rerun anytime.\n")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # Windows: Ctrl+C still raises KeyboardInterrupt below

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[shutdown] stopping both nodes cleanly...")
        await agent.stop()
        await ai.stop()
        await node1.stop()
        await node2.stop()
        print("[shutdown] done. All state saved — rerun `python run_local_network.py` to continue.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
