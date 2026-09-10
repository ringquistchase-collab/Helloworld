"""
research_art_generator.py
================
Turns real network/research statistics — MOS score components, topic
count, and any extra_signal_stats a caller supplies (EEG/hormone
stats, connection_behavior_stats.py's reconnection metrics, whatever
else already went through this project's consent-gated pipelines) —
into an abstract text-to-image prompt, and optionally a real
generated image via Stable Diffusion + this project's own LoRA house
style (lora_style_training.py's output).

ABSTRACTION BOUNDARY (the part TRUST.md points at)
--------------------------------------------------------
This file only ever receives already-abstracted numbers: a MOS score
is a rolled-up quality/connectivity/agreement metric, not raw traffic;
extra_signal_stats entries (EEG band power, connection_behavior_stats
reconnect counts, etc.) are themselves aggregate statistics computed
elsewhere, never raw biosignal or raw personal data. This file adds
one more layer of abstraction on top (numbers -> visual descriptors
-> an image), and never claims the output depicts anything biological,
emotional, or personal -- same rule maxwell_style_images.py's captions
already follow. If you're tempted to pass this file something that
isn't already a small set of aggregate numbers, that's the sign it
doesn't belong here -- add the abstraction step upstream instead.

WHAT'S FULLY REAL AND TESTABLE HERE (no GPU needed)
------------------------------------------------------------
_build_art_prompt() -- deterministic: the same input stats always
produce the same prompt string. Pure string-building, no external
calls, no dependencies beyond the stdlib.

HONEST STATUS OF generate_network_art()
--------------------------------------------
Follows diffusers' actual documented StableDiffusionPipeline API
(same real pattern lora_style_training.py uses) and will load LoRA
weights from lora_style_training.py's output_dir if present. NOT run
to completion in this environment -- needs a GPU, same disclosed gap
as lora_style_training.py. See KNOWN_GAPS.md.

Usage
-----
    from research_art_generator import _build_art_prompt, generate_network_art

    prompt = _build_art_prompt(mos_score, topic_count=3, style="abstract, calm blues")

    result = generate_network_art(mos_score, topic_count=3, extra_signal_stats=stats)
    # -> {"prompt": ..., "output_path": "network_art.png", "device": "cuda"|"cpu"}
"""

from __future__ import annotations
import os


def _build_art_prompt(
    mos: dict, topic_count: int, style: str = "abstract geometric",
    extra_signal_stats: dict | None = None,
) -> str:
    """
    Deterministic mapping from real numeric stats to a text-to-image
    prompt. No randomness, no LLM call -- the same inputs always
    produce the same prompt, so this is fully unit-testable without a
    GPU or any external service.
    """
    quality = mos.get("quality", 3.0)
    connectivity = mos.get("connectivity", 3.0)
    agreement = mos.get("agreement", 3.0)
    overall = mos.get("mos", 3.0)

    density = (
        "dense, intricate" if quality >= 4.0
        else "moderate density" if quality >= 2.5
        else "sparse, minimal"
    )
    connection_lines = (
        "many converging lines" if connectivity >= 4.0
        else "some connecting lines" if connectivity >= 2.0
        else "few isolated forms"
    )
    harmony = (
        "harmonious, aligned composition" if agreement >= 4.0
        else "some visual tension" if agreement >= 2.0
        else "fragmented, discordant composition"
    )

    parts = [
        f"{style} composition",
        density,
        connection_lines,
        harmony,
        f"{topic_count} distinct focal elements",
        f"overall_score={overall:.2f}",
    ]

    if extra_signal_stats:
        for key in sorted(extra_signal_stats):
            value = extra_signal_stats[key]
            if value is not None:
                parts.append(f"{key}={value}")

    return ", ".join(parts)


def generate_network_art(
    mos: dict, topic_count: int, style: str = "abstract geometric",
    extra_signal_stats: dict | None = None, output_path: str = "network_art.png",
    lora_weights_dir: str | None = "lora_output",
    base_model: str = "runwayml/stable-diffusion-v1-5",
) -> dict:
    """
    Real Stable Diffusion generation from the prompt _build_art_prompt()
    builds. Loads LoRA weights from lora_weights_dir if that directory
    exists (see lora_style_training.py) for a consistent house style.
    Needs a GPU to run in reasonable time -- see module docstring.
    """
    import torch
    from diffusers import StableDiffusionPipeline

    prompt = _build_art_prompt(mos, topic_count, style=style, extra_signal_stats=extra_signal_stats)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("WARNING: no GPU detected. This will be extremely slow (minutes to "
              "hours) or may not complete in reasonable time on CPU. This is "
              "expected in a sandbox -- run on real GPU hardware for an actual "
              "generation.")

    pipe = StableDiffusionPipeline.from_pretrained(base_model, torch_dtype=torch.float32)
    pipe = pipe.to(device)

    if lora_weights_dir and os.path.exists(lora_weights_dir):
        pipe.load_lora_weights(lora_weights_dir)

    image = pipe(prompt).images[0]
    image.save(output_path)

    return {"prompt": prompt, "output_path": output_path, "device": device}


if __name__ == "__main__":
    print("=== Real prompt-building from real stats (no GPU needed) ===\n")

    mos_score = {"mos": 4.5, "quality": 4.8, "connectivity": 5.0, "agreement": 3.5}
    prompt = _build_art_prompt(mos_score, topic_count=3, style="abstract, calm blues")
    print(f"MOS input:  {mos_score}")
    print(f"Prompt out: {prompt}")

    print("\n=== Same call again -- confirming determinism ===")
    prompt2 = _build_art_prompt(mos_score, topic_count=3, style="abstract, calm blues")
    print(f"Identical output: {prompt == prompt2}")

    print("\n=== With extra_signal_stats folded in ===")
    stats = {"conn_reconnect_count": 2, "conn_unique_addresses": 3, "conn_uptime_seconds": 1.0}
    prompt3 = _build_art_prompt(mos_score, topic_count=3, extra_signal_stats=stats)
    print(f"Prompt out: {prompt3}")

    print("\n=== To actually generate an image ===")
    print("1. Fix/verify torch + diffusers work on YOUR machine (needs a real GPU to be practical)")
    print("2. result = generate_network_art(mos_score, topic_count=3, extra_signal_stats=stats)")
