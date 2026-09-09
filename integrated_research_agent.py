"""
integrated_research_agent.py
================
Extends the real growing_research_agent.py (ClinicalTrials.gov +
PubMed + ClinVar + HGNC fetching, topic queueing, persistence -- all
of that is reused as-is, not duplicated) with three more real steps
whenever a topic explore finds something new:

  1. MINING    — folds the discovery into the node's own DigitalDNA
                 strand and gossips a signed block to peers, via
                 node.mine_and_broadcast(). Uses "research_query" as
                 the source for a topic explored for the first time
                 (an explicit ask) and "research_update" for a
                 background re-check of an existing topic -- matching
                 the distinction digital_dna.py's ALLOWED_SOURCES
                 already documents between those two sources.
  2. CORPUS    — appends a real record (condition, biomarker, the
                 IDs actually found, the resulting block's id) to a
                 local corpus.json, independent of the topic-state
                 file growing_research_agent.py already maintains.
  3. VISUALS   — best-effort only, and OFF unless you pass a real
                 openai_api_key: makes one real DALL-E REST call
                 (images/generations) captioned from the real finding,
                 saves the PNG to visuals_dir. HONEST STATUS: this
                 code follows OpenAI's documented REST shape but has
                 NOT been exercised against a live key in this
                 environment (no key is configured here) -- same
                 "structurally correct, not execution-verified"
                 status lora_style_training.py already discloses for
                 its own GPU-dependent path. runway_api_key (video)
                 is accepted but deliberately left unimplemented
                 rather than writing untested code against a less
                 standardized API -- see NotImplementedError note
                 below if you pass one.

Usage
-----
    from integrated_research_agent import IntegratedResearchAgent

    agent = IntegratedResearchAgent(node, dna, store_path="research_store.json",
                                     corpus_path="corpus.json", visuals_dir="visuals",
                                     interval_s=3600, audit=audit)
    await agent.start()
    key = await agent.seed_topic("breast cancer", biomarker="BRCA1")
    ...
    await agent.stop()
"""

from __future__ import annotations
import hashlib
import json
import os
import time
import urllib.request

from growing_research_agent import GrowingResearchAgent


class IntegratedResearchAgent(GrowingResearchAgent):
    def __init__(
        self, node, dna, store_path: str = "research_store.json",
        corpus_path: str = "corpus.json", visuals_dir: str = "visuals",
        interval_s: int = 3600, max_topics: int = 50, audit=None,
        openai_api_key: str | None = None, runway_api_key: str | None = None,
    ):
        super().__init__(node, dna, store_path=store_path, interval_s=interval_s, max_topics=max_topics)
        self.corpus_path = corpus_path
        self.visuals_dir = visuals_dir
        self.audit = audit
        self.openai_api_key = openai_api_key
        self.runway_api_key = runway_api_key

        self.corpus: list[dict] = []
        self._load_corpus()
        os.makedirs(visuals_dir, exist_ok=True)

    # -- override the parent's private explore step so both seed_topic()
    #    AND the background loop (which calls self._explore directly)
    #    get mining/corpus/visuals, not just explicit calls ----------------
    async def _explore(self, condition: str, biomarker: str | None):
        key = self._topic_key(condition, biomarker)
        is_new_topic = key not in self.topics

        await super()._explore(condition, biomarker)

        topic = self.topics[key]
        new_ids = topic.get("new_ids_last_run", [])
        if not new_ids:
            return

        source = "research_query" if is_new_topic else "research_update"
        block_id = await self._mine_finding(condition, biomarker, new_ids, source)
        self._record_corpus(condition, biomarker, new_ids, block_id)

        image_path = None
        if self.openai_api_key:
            image_path = self._try_generate_visual(condition, biomarker, new_ids)
        if self.runway_api_key:
            # deliberately not implemented -- see module docstring
            if self.audit is not None:
                self.audit.log(
                    module="integrated_research_agent", action="video_generation_skipped",
                    node_id=self.node.node_id,
                    details={"reason": "runway integration not implemented in this build"},
                )

        if self.audit is not None:
            self.audit.log(
                module="integrated_research_agent", action="research_cycle",
                node_id=self.node.node_id,
                details={
                    "condition": condition, "biomarker": biomarker, "source": source,
                    "new_id_count": len(new_ids), "block_id": block_id, "image_path": image_path,
                },
            )

    async def _mine_finding(self, condition: str, biomarker: str | None, new_ids: list[str], source: str) -> str:
        summary = f"{condition}|{biomarker or ''}|" + "|".join(sorted(new_ids))
        feature_hash = hashlib.sha256(summary.encode()).hexdigest()
        confidence = min(1.0, len(new_ids) / 10.0) or 0.5
        await self.node.mine_and_broadcast(
            source=source, feature_hash_hex=feature_hash,
            confidence=confidence, consent_verified=True,
        )
        block = self.node.network_ledger[self.node.node_id][-1]
        return block["block_id"]

    def _record_corpus(self, condition: str, biomarker: str | None, new_ids: list[str], block_id: str):
        self.corpus.append({
            "timestamp": time.time(), "condition": condition, "biomarker": biomarker,
            "new_ids": new_ids, "block_id": block_id,
        })
        self._save_corpus()

    def _try_generate_visual(self, condition: str, biomarker: str | None, new_ids: list[str]) -> str | None:
        """Best-effort real DALL-E call. Any failure (network, auth, rate
        limit, malformed response) is caught and logged rather than
        raised -- a broken image API must never take down the research
        pipeline that found real results."""
        prompt = (
            f"abstract scientific illustration representing research findings on "
            f"{condition}" + (f" and {biomarker}" if biomarker else "") +
            f", {len(new_ids)} new results, geometric, blue and orange"
        )
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/images/generations",
                data=json.dumps({"model": "dall-e-3", "prompt": prompt, "n": 1, "size": "1024x1024"}).encode(),
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            image_url = result["data"][0]["url"]

            with urllib.request.urlopen(image_url, timeout=60) as img_resp:
                image_bytes = img_resp.read()

            filename = f"{hashlib.sha256(prompt.encode()).hexdigest()[:16]}.png"
            path = os.path.join(self.visuals_dir, filename)
            with open(path, "wb") as f:
                f.write(image_bytes)
            return path
        except Exception as e:
            if self.audit is not None:
                self.audit.log(
                    module="integrated_research_agent", action="visual_generation_failed",
                    node_id=self.node.node_id, details={"error": str(e)},
                )
            return None

    # -- corpus persistence ------------------------------------------------
    def _load_corpus(self):
        if not os.path.exists(self.corpus_path):
            return
        try:
            with open(self.corpus_path, "r", encoding="utf-8") as f:
                self.corpus = json.load(f)
        except (json.JSONDecodeError, OSError):
            self.corpus = []

    def _save_corpus(self):
        tmp_path = self.corpus_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.corpus, f, indent=2)
        os.replace(tmp_path, self.corpus_path)


if __name__ == "__main__":
    import asyncio
    import sys
    import tempfile

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from digital_dna import DigitalDNA
    from network_os import NetworkNode

    async def _demo():
        with tempfile.TemporaryDirectory() as tmp:
            dna = DigitalDNA(seed_label="integrated-research-demo", dna_path=os.path.join(tmp, "demo.dna.json"))
            node = NetworkNode(dna, host="127.0.0.1", port=8902)
            await node.start()

            agent = IntegratedResearchAgent(
                node, dna, store_path=os.path.join(tmp, "research_store.json"),
                corpus_path=os.path.join(tmp, "corpus.json"), visuals_dir=os.path.join(tmp, "visuals"),
                interval_s=3600,
            )

            print("=== Seeding a real topic: breast cancer / BRCA1 ===")
            key = await agent.seed_topic("breast cancer", biomarker="BRCA1")
            t = agent.topics[key]
            print(f"New results found: {len(t['new_ids_last_run'])}")
            print(f"Chain length after mining: {len(node.network_ledger[node.node_id])}")
            print(f"Corpus entries: {len(agent.corpus)}")
            if agent.corpus:
                print(f"Corpus entry: {agent.corpus[-1]}")

            await node.stop()

    asyncio.run(_demo())
