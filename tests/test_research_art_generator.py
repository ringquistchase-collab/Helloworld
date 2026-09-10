from research_art_generator import _build_art_prompt


def test_deterministic_same_input_same_output():
    mos = {"mos": 4.5, "quality": 4.8, "connectivity": 5.0, "agreement": 3.5}
    p1 = _build_art_prompt(mos, topic_count=3, style="abstract, calm blues")
    p2 = _build_art_prompt(mos, topic_count=3, style="abstract, calm blues")
    assert p1 == p2


def test_style_appears_in_prompt():
    mos = {"mos": 3.0, "quality": 3.0, "connectivity": 3.0, "agreement": 3.0}
    prompt = _build_art_prompt(mos, topic_count=1, style="abstract, calm blues")
    assert "abstract, calm blues" in prompt


def test_high_quality_produces_dense_descriptor():
    mos = {"mos": 4.5, "quality": 4.8, "connectivity": 3.0, "agreement": 3.0}
    prompt = _build_art_prompt(mos, topic_count=1)
    assert "dense" in prompt


def test_low_quality_produces_sparse_descriptor():
    mos = {"mos": 1.5, "quality": 1.0, "connectivity": 3.0, "agreement": 3.0}
    prompt = _build_art_prompt(mos, topic_count=1)
    assert "sparse" in prompt


def test_extra_signal_stats_are_included():
    mos = {"mos": 3.0, "quality": 3.0, "connectivity": 3.0, "agreement": 3.0}
    stats = {"conn_reconnect_count": 5}
    prompt = _build_art_prompt(mos, topic_count=1, extra_signal_stats=stats)
    assert "conn_reconnect_count=5" in prompt


def test_none_valued_extra_stats_are_skipped():
    mos = {"mos": 3.0, "quality": 3.0, "connectivity": 3.0, "agreement": 3.0}
    stats = {"conn_avg_seconds_between_reconnects": None}
    prompt = _build_art_prompt(mos, topic_count=1, extra_signal_stats=stats)
    assert "conn_avg_seconds_between_reconnects" not in prompt


def test_missing_mos_keys_fall_back_to_defaults():
    prompt = _build_art_prompt({}, topic_count=0)
    assert "overall_score=3.00" in prompt
