"""
crispr_research_suite.py
================
Ties together the two real CRISPR-adjacent pieces in this project:

  1. LITERATURE TRACKING — reuses growing_research_agent.py's real
     fetch pipeline (ClinicalTrials.gov, PubMed, ClinVar, HGNC) to
     track new results for a condition/biomarker over time, via an
     internal GrowingResearchAgent instance.
  2. GUIDE DESIGN — crispr_guide_design.py's real PAM-site scan, for
     scoring candidate guide RNAs against a DNA sequence you provide.

DELIBERATE BOUNDARY: never mines into the chain
------------------------------------------------------
The internal GrowingResearchAgent is constructed with node=None and
dna=None. Looking at growing_research_agent.py's own code: node is
only ever used for two optional hooks (`node.on_topic(...)` at
construction, `node.broadcast_topic(...)` after each explore step),
both guarded by `hasattr(...)` checks that fail safely on None; dna
is stored but never read anywhere in that class. So this suite gets
the exact same real ClinicalTrials.gov/PubMed/ClinVar/HGNC fetching
integrated_research_agent.py uses, WITHOUT that class's mining step —
guide-design and literature-tracking results never become a signed
block or a live DNA signal. That's intentional: CRISPR guide
candidates are computational suggestions for further real wet-lab
validation (see crispr_guide_design.py's own docstring), not
consented personal signals, so they don't belong in the identity
strand or the gossiped chain.

Usage
-----
    from crispr_research_suite import CrisprResearchSuite

    suite = CrisprResearchSuite(crispr_store_path="crispr_store.json", audit=audit)
    result = await suite.check_research("breast cancer", biomarker="BRCA1")
    # result: {"total_ids": int, "new_ids": [...]}

    candidates = suite.design_guides(dna_sequence, top_n=5)
"""

from __future__ import annotations

from crispr_guide_design import find_guide_candidates
from growing_research_agent import GrowingResearchAgent


class CrisprResearchSuite:
    def __init__(self, crispr_store_path: str = "crispr_store.json", audit=None, max_topics: int = 50):
        self.audit = audit
        # node=None, dna=None: see module docstring -- this keeps literature
        # tracking working fully while guaranteeing it never mines a block
        # or touches an identity strand.
        self._research = GrowingResearchAgent(
            node=None, dna=None, store_path=crispr_store_path, max_topics=max_topics,
        )

    async def check_research(self, condition: str, biomarker: str | None = None) -> dict:
        """Runs one real explore step (ClinicalTrials.gov + PubMed + ClinVar
        + HGNC) for condition/biomarker and returns a summary. Async --
        this does real network I/O, same as growing_research_agent.py's
        own seed_topic()."""
        key = await self._research.seed_topic(condition, biomarker=biomarker)
        topic = self._research.topics[key]
        total_ids = sum(len(ids) for ids in topic["all_ids"].values())

        if self.audit is not None:
            self.audit.log(
                module="crispr_research_suite", action="check_research", node_id="local",
                details={
                    "condition": condition, "biomarker": biomarker,
                    "total_ids": total_ids, "new_id_count": len(topic["new_ids_last_run"]),
                },
            )

        return {"total_ids": total_ids, "new_ids": topic["new_ids_last_run"]}

    def design_guides(self, dna_sequence: str, top_n: int = 5) -> list[dict]:
        """Synchronous, real PAM-site scan -- no network I/O, see
        crispr_guide_design.py for the scoring heuristic and its limits."""
        candidates = find_guide_candidates(dna_sequence, top_n=top_n)

        if self.audit is not None:
            self.audit.log(
                module="crispr_research_suite", action="design_guides", node_id="local",
                details={"sequence_length": len(dna_sequence), "candidates_found": len(candidates)},
            )

        return candidates


if __name__ == "__main__":
    import asyncio
    import tempfile
    import os

    async def _demo():
        with tempfile.TemporaryDirectory() as tmp:
            suite = CrisprResearchSuite(crispr_store_path=os.path.join(tmp, "crispr_store.json"))

            print("=== Real literature check: breast cancer / BRCA1 ===")
            result = await suite.check_research("breast cancer", biomarker="BRCA1")
            print(f"Total IDs tracked: {result['total_ids']}")
            print(f"New this run: {len(result['new_ids'])}")

            print("\n=== Real guide design: BRCA1 fragment ===")
            brca1_fragment = (
                "ATGGATTTATCTGCTCTTCGCGTTGAAGAAGTACAAAATGTCATTAATGCTATGCAGAAA"
                "ATCTTAGAGTGTCCCATCTGTCTGGAGTTGATCAAGGAACCTGTCTCCACAAAGTGTGAC"
            )
            candidates = suite.design_guides(brca1_fragment, top_n=3)
            for c in candidates:
                print(f"  {c['guide_sequence']}-{c['pam']}  score={c['score']}")

            print(f"\nUnderlying research agent has node/dna: "
                  f"{suite._research.node is None and suite._research.dna is None} (expected True)")

    asyncio.run(_demo())
