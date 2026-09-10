from crispr_guide_design import find_guide_candidates, _gc_content, _score_candidate

REAL_BRCA1_FRAGMENT = (
    "ATGGATTTATCTGCTCTTCGCGTTGAAGAAGTACAAAATGTCATTAATGCTATGCAGAAA"
    "ATCTTAGAGTGTCCCATCTGTCTGGAGTTGATCAAGGAACCTGTCTCCACAAAGTGTGAC"
    "CACATATTTTGCAAATTTTGCATGCTGAAACTTCTCAACCAGAAGAAAGGGCCTTCACAG"
)


def test_finds_at_least_one_candidate():
    candidates = find_guide_candidates(REAL_BRCA1_FRAGMENT, top_n=5)
    assert len(candidates) > 0


def test_candidates_have_valid_pam():
    candidates = find_guide_candidates(REAL_BRCA1_FRAGMENT, top_n=5)
    for c in candidates:
        assert c["pam"][1:] == "GG"  # NGG -- last two bases are always GG


def test_candidates_are_correct_length():
    candidates = find_guide_candidates(REAL_BRCA1_FRAGMENT, top_n=5)
    for c in candidates:
        assert len(c["guide_sequence"]) == 20


def test_results_sorted_by_score_descending():
    candidates = find_guide_candidates(REAL_BRCA1_FRAGMENT, top_n=5)
    scores = [c["score"] for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_top_n_is_respected():
    candidates = find_guide_candidates(REAL_BRCA1_FRAGMENT, top_n=2)
    assert len(candidates) <= 2


def test_gc_content_of_all_gc_sequence():
    assert _gc_content("GCGCGC") == 1.0


def test_gc_content_of_no_gc_sequence():
    assert _gc_content("ATATAT") == 0.0


def test_score_peaks_near_50_percent_gc():
    balanced = "A" * 10 + "G" * 10  # 50% GC
    extreme = "G" * 20               # 100% GC
    assert _score_candidate(balanced) > _score_candidate(extreme)
