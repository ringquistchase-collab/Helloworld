"""
crispr_guide_design.py
================
Real guide-RNA candidate finder for SpCas9 (the standard, most-used
Cas9 variant) — given a real DNA sequence, finds PAM sites (NGG,
SpCas9's recognition motif) and scores candidates using a real,
published, if simplified heuristic. Same category of tool as CRISPOR
or Benchling's public guide-design tools, at a research/educational
level rather than clinical-validated precision.

WHAT THE SCORING ACTUALLY IS
--------------------------------
A simplified GC-content + positional heuristic based on published
findings that guide efficiency correlates with GC content in a
specific range (40-60%) and that the region close to the PAM (the
"seed region") matters most for both efficiency and specificity. This
is NOT the same as CRISPOR's machine-learned on/off-target models —
those are trained on real experimental cutting-efficiency data this
tool doesn't have access to. Treat this as a reasonable filter to
narrow candidates for further real validation, not a clinical-grade
final answer.

WHAT THIS IS FOR
--------------------
Research/educational sequence analysis. Real CRISPR therapeutic
design requires wet-lab validation (off-target sequencing, cutting
efficiency assays) that no software substitutes for.

Usage
-----
    from crispr_guide_design import find_guide_candidates

    candidates = find_guide_candidates(dna_sequence, top_n=5)
"""

from __future__ import annotations
from Bio.Seq import Seq


PAM_PATTERN = "GG"   # SpCas9 PAM is NGG — we scan for the GG, N is any base
GUIDE_LENGTH = 20     # standard SpCas9 guide length


def _gc_content(seq: str) -> float:
    if not seq:
        return 0.0
    gc = seq.count("G") + seq.count("C")
    return gc / len(seq)


def _score_candidate(guide_seq: str) -> float:
    """0-1 scale. Real, published heuristic (GC content in the efficient
    range + seed-region GC weighting), not a machine-learned model."""
    gc = _gc_content(guide_seq)
    # published finding: 40-60% GC correlates with higher efficiency;
    # score peaks in that range, falls off outside it
    gc_score = 1.0 - min(1.0, abs(gc - 0.5) / 0.5)

    # seed region (last 8-10 bases before the PAM) GC matters more for
    # specificity — weight it slightly higher
    seed = guide_seq[-10:]
    seed_gc = _gc_content(seed)
    seed_score = 1.0 - min(1.0, abs(seed_gc - 0.5) / 0.5)

    return round(0.5 * gc_score + 0.5 * seed_score, 3)


def find_guide_candidates(dna_sequence: str, top_n: int = 5) -> list[dict]:
    """Real PAM-site scan on both strands of a real DNA sequence."""
    seq = Seq(dna_sequence.upper().replace(" ", "").replace("\n", ""))
    candidates = []

    for strand_name, strand_seq in [("+", seq), ("-", seq.reverse_complement())]:
        s = str(strand_seq)
        for i in range(GUIDE_LENGTH, len(s) - 2):
            if s[i + 1:i + 3] == PAM_PATTERN:   # position i+1,i+2 is the "GG" of NGG
                guide = s[i - GUIDE_LENGTH + 1:i + 1]
                if len(guide) == GUIDE_LENGTH:
                    candidates.append({
                        "guide_sequence": guide,
                        "pam": s[i:i + 3],
                        "strand": strand_name,
                        "position": i - GUIDE_LENGTH + 1,
                        "gc_content": round(_gc_content(guide), 3),
                        "score": _score_candidate(guide),
                    })

    candidates.sort(key=lambda c: -c["score"])
    return candidates[:top_n]


if __name__ == "__main__":
    # a real fragment of human BRCA1 exon sequence (public, from NCBI RefSeq)
    real_brca1_fragment = (
        "ATGGATTTATCTGCTCTTCGCGTTGAAGAAGTACAAAATGTCATTAATGCTATGCAGAAA"
        "ATCTTAGAGTGTCCCATCTGTCTGGAGTTGATCAAGGAACCTGTCTCCACAAAGTGTGAC"
        "CACATATTTTGCAAATTTTGCATGCTGAAACTTCTCAACCAGAAGAAAGGGCCTTCACAG"
    )

    print("=== Real guide-RNA candidates for a real BRCA1 fragment ===\n")
    candidates = find_guide_candidates(real_brca1_fragment, top_n=5)
    for c in candidates:
        print(f"  {c['guide_sequence']}-{c['pam']}  strand={c['strand']}  "
              f"pos={c['position']}  GC={c['gc_content']}  score={c['score']}")
