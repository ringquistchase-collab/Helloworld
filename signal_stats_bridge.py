"""
signal_stats_bridge.py
================
The ONLY sanctioned path by which EEG or hormone telemetry may reach
the rest of this project (the identity strand via digital_dna.py, or
research_art_generator.py / research_video_generator.py) — and it only
ever lets ABSTRACT NUMERIC STATISTICS through (count, mean, variance),
never raw readings, never anything claimed to represent brain activity
or endocrine state.

READ THIS BEFORE MODIFYING THIS FILE
-----------------------------------------
This file exists because of an explicit boundary set earlier in this
project, and it's named in TRUST.md as one of the files that "decides
what gets hashed vs. stored raw, or what leaves a device vs. stays
local." Two rules it exists to hold:

  1. Raw readings never leave. Whatever numeric samples your consented
     capture pipeline produced go IN; only their summary statistics
     (and a hash of those statistics) ever come OUT. The raw list is
     never returned, never hashed, never folded into the strand.
  2. No claim of biological meaning. AI-generated images/video have no
     scientific relationship to a person's EEG or hormone readings, no
     matter how they're prompted. What consent legitimately unlocks is
     treating these signals as abstract data channels — same status as
     a MOS score — for abstract art/telemetry. It does NOT unlock
     imagery that claims to "show" someone's brain or body. Do not add
     anatomical language or physiological interpretation here; that's
     the exact line this file holds.

WHAT THIS ACTUALLY COMPUTES
--------------------------------
Ordinary statistics — count, mean, variance — nothing more. No
frequency-band analysis claiming to detect "focus" or "stress," no
hormone-level interpretation claiming physiological meaning. Just
numbers, the same category of thing a MOS score already is.

TWO EXITS, BOTH STATS-ONLY
-------------------------------
    from signal_stats_bridge import signal_channel_stats, fold_signal_into_strand

    # (a) toward abstract art/video — pass stats, never raw readings:
    stats = signal_channel_stats(eeg_readings, label="eeg")
    # -> {"eeg_count": 40, "eeg_mean": 12.3, "eeg_variance": 0.4}
    result = generate_network_art(mos_score, topic_count, extra_signal_stats=stats)

    # (b) toward the identity strand — folds ONLY a hash of the stats
    # through digital_dna.py's real consent gate (add_live_signal):
    event, stats = fold_signal_into_strand(
        dna, eeg_readings, source="eeg_telemetry", consent_verified=True)
    # the raw eeg_readings are summarized, the summary is hashed, and
    # only that hash reaches the strand -- the readings themselves do not.
"""

from __future__ import annotations

import hashlib
import json
import statistics

# The only two ALLOWED_SOURCES (see digital_dna.py) that represent raw
# personal biosignal channels, and whose own consent descriptions in that
# file promise "only aggregate statistics ever leave this source." This
# bridge enforces that promise in code: fold_signal_into_strand() refuses
# any other source, so a raw waveform can't be routed in under a label
# that never made that promise.
TELEMETRY_SOURCES = ("eeg_telemetry", "hormone_telemetry")


def signal_channel_stats(readings: list[float], label: str) -> dict:
    """Real, boring statistics — nothing that implies biological meaning.

    `readings` should already be whatever numeric values your consented
    capture pipeline produced (e.g. a band-power number, a hormone
    concentration); this function doesn't care what they represent, it
    just summarizes them. The raw list is not retained or returned."""
    if not readings:
        return {f"{label}_count": 0}
    return {
        f"{label}_count": len(readings),
        f"{label}_mean": round(statistics.mean(readings), 4),
        f"{label}_variance": round(statistics.variance(readings), 4) if len(readings) > 1 else 0.0,
    }


def _feature_hash_from_stats(stats: dict) -> str:
    """SHA-256 over the canonical JSON of the STATS ONLY.

    This is what gets folded into the strand. It is derived from the
    aggregate statistics, never from the raw readings, so the strand can
    never be reversed back toward an individual sample — by construction,
    not by promise."""
    canonical = json.dumps(stats, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def fold_signal_into_strand(
    dna,
    readings: list[float],
    source: str,
    *,
    consent_verified: bool,
    confidence: float = 1.0,
    timestamp: float | None = None,
):
    """Summarize consented telemetry `readings`, hash the summary, and
    fold ONLY that hash into `dna` via digital_dna.py's real consent gate.

    Returns (MutationEvent, stats). The raw `readings` are never returned,
    never hashed directly, and never passed to add_live_signal — only the
    stats-derived feature hash is.

    Enforced here:
      - `source` must be one of TELEMETRY_SOURCES (a raw biosignal channel
        whose consent description promises stats-only). Any other source
        is rejected, so this bridge can't be used to smuggle raw data in
        under a label that never made that promise.
      - `readings` must be non-empty (there's nothing to consent to fold
        otherwise).
    Consent itself is still enforced downstream by add_live_signal, which
    raises unless consent_verified=True and the source is in ALLOWED_SOURCES
    — this function forwards, it does not bypass, that gate."""
    if source not in TELEMETRY_SOURCES:
        raise ValueError(
            f"signal_stats_bridge only folds raw biosignal telemetry "
            f"({', '.join(TELEMETRY_SOURCES)}); refusing source '{source}'. "
            f"Non-telemetry sources go through digital_dna.add_live_signal() "
            f"directly with their own already-derived feature hash."
        )
    if not readings:
        raise ValueError("no readings to summarize; nothing to fold.")

    label = source.replace("_telemetry", "")
    stats = signal_channel_stats(readings, label=label)
    feature_hash = _feature_hash_from_stats(stats)

    event = dna.add_live_signal(
        source=source,
        feature_hash_hex=feature_hash,
        confidence=confidence,
        consent_verified=consent_verified,
        timestamp=timestamp,
    )
    return event, stats


if __name__ == "__main__":
    # structural demo with placeholder numbers — proves the math is
    # ordinary statistics, and that only stats (never raw) leave.
    fake_eeg = [12.1, 12.4, 11.9, 12.6, 12.0, 12.3]
    fake_hormone = [3.2, 3.4, 3.1, 3.5]

    eeg_stats = signal_channel_stats(fake_eeg, label="eeg")
    hormone_stats = signal_channel_stats(fake_hormone, label="hormone")

    print("=== EEG stats (abstract numbers only) ===")
    print(eeg_stats)
    print(f"feature hash folded into strand would be: {_feature_hash_from_stats(eeg_stats)}")
    print("\n=== Hormone stats (abstract numbers only) ===")
    print(hormone_stats)
    print("\nThese stats — and a hash of them — are the ONLY values that ever")
    print("reach the strand or an art/video prompt. No raw readings, no claim")
    print("about what they mean biologically.")
