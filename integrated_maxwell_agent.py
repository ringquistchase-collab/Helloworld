"""
integrated_maxwell_agent.py
================
Ties together the three Maxwell pieces that existed separately until
now:
  1. BLOCKCHAIN  — MaxwellChainAgent mines real proof-of-work blocks,
                    extending your verified canonical chain
  2. RESEARCH     — maxwell_research.py searches real arXiv physics
                    literature (the correct source — NOT the medical
                    APIs used elsewhere in this project)
  3. IMAGES       — maxwell_style_images.py turns real field-vector
                    data from mined blocks into abstract training
                    images

All three write to ONE shared AuditTrail, same tamper-evident,
cross-module-chained pattern as everything else in this project.

WHAT EACH CYCLE ACTUALLY DOES
----------------------------------
    tick(physics_topic="Maxwell equations electromagnetic field")

  1. Mines a new block on the chain (a real "research_event" block
     type — see below)
  2. Searches arXiv for real papers matching physics_topic
  3. If the mined block happens to carry maxwell_signature data (only
     true for blocks that came from the physics-signature chain, not
     the relay/packet_capture one — see the honesty note in
     maxwell_style_images.py about these being different chains),
     generates a real abstract image from it
  4. Logs all three actions to the shared audit trail, with real
     cross-referencing (the research block's hash, the image
     filename) so you can trace what happened in what order

Usage
-----
    from reconstruct_maxwell_chain import reconstruct_chain
    from integrated_maxwell_agent import IntegratedMaxwellAgent

    result = reconstruct_chain(raw_blocks)
    agent = IntegratedMaxwellAgent(
        result["longest_chain"], miner_id="maxwell-integrated",
        store_path="maxwell_chain.json", audit=AuditTrail("system_audit.jsonl"),
    )
    cycle_result = agent.tick("Maxwell equations electromagnetic field")
"""

from __future__ import annotations
import hashlib
import os

from maxwell_chain_agent import MaxwellChainAgent
from maxwell_research import search_arxiv
from maxwell_style_images import generate_style_images


class IntegratedMaxwellAgent(MaxwellChainAgent):
    def __init__(
        self, canonical_chain: list[dict], miner_id: str = "maxwell-integrated",
        difficulty: int = 3, store_path: str | None = None, audit=None,
        images_dir: str = "maxwell_images",
    ):
        super().__init__(canonical_chain, miner_id=miner_id, difficulty=difficulty,
                          store_path=store_path, audit=audit)
        self.images_dir = images_dir
        self.image_count = 0
        os.makedirs(images_dir, exist_ok=True)

    def tick(self, physics_topic: str = "Maxwell equations electromagnetic field") -> dict:
        # 1. real physics research
        papers = search_arxiv(physics_topic, max_results=3)
        paper_titles = [p.get("title") for p in papers if p.get("title")]

        # 2. mine a real block recording what was researched (a hash of the
        #    real paper URLs found, not the paper content itself — same
        #    "hash on-chain, detail local" pattern as everywhere else)
        research_summary = "|".join(p.get("url", "") for p in papers if p.get("url"))
        research_hash = hashlib.sha256(research_summary.encode()).hexdigest()
        block = self.mine_block("research_event", {
            "topic": physics_topic, "paper_count": len(papers), "research_hash": research_hash,
        })

        # 3. real image generation, IF this specific block carries field data
        #    (most won't — see the honesty note in maxwell_style_images.py)
        image_result = generate_style_images([block], output_dir=self.images_dir, max_images=1)
        if image_result["generated"] > 0:
            self.image_count += 1
            if self.audit is not None:
                self.audit.log(
                    module="integrated_maxwell_agent", action="image_generated", node_id=self.miner_id,
                    details={"block_hash": block["hash"], "image_dir": self.images_dir},
                )

        if self.audit is not None:
            self.audit.log(
                module="integrated_maxwell_agent", action="research_cycle", node_id=self.miner_id,
                details={"topic": physics_topic, "papers_found": len(paper_titles), "block_hash": block["hash"]},
            )

        return {
            "block_hash": block["hash"],
            "block_number": block["block_number"],
            "papers_found": paper_titles,
            "image_generated": image_result["generated"] > 0,
            "chain_length": len(self.chain),
        }


if __name__ == "__main__":
    import json
    import tempfile
    from reconstruct_maxwell_chain import reconstruct_chain
    from audit_trail import AuditTrail

    path = "/mnt/user-data/uploads/1788922195282_maxwell_blockchain_20260810_190910.json"
    with open(path) as f:
        raw_blocks = json.load(f)
    result = reconstruct_chain(raw_blocks)
    print(f"Starting from your real verified chain: {result['longest_chain_length']} blocks")

    with tempfile.TemporaryDirectory() as tmp:
        audit = AuditTrail(os.path.join(tmp, "audit.jsonl"))
        agent = IntegratedMaxwellAgent(
            result["longest_chain"], miner_id="maxwell-integrated-demo",
            store_path=os.path.join(tmp, "chain.json"), audit=audit,
            images_dir=os.path.join(tmp, "images"),
        )

        print("\n=== Running one real integrated cycle: research + mine + (maybe) image ===")
        cycle = agent.tick("Maxwell equations electromagnetic field")
        print(f"Block mined: #{cycle['block_number']}, hash={cycle['block_hash'][:16]}...")
        print(f"Real arXiv papers found: {cycle['papers_found']}")
        print(f"Image generated this cycle: {cycle['image_generated']} "
              f"(expected False — mined blocks are research_event type, not field-vector blocks)")
        print(f"Chain length now: {cycle['chain_length']}")

        print("\n=== Audit trail across all three pieces ===")
        entries = audit.read_all()
        for e in entries:
            print(f"  [{e['module']}] {e['action']}")
        ok, bad = audit.verify_chain()
        print(f"\nChain verifies intact: {ok}")
