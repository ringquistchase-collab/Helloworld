"""
maxwell_style_images.py
================
Turns REAL Maxwell-chain field vector data (E_field, B_field, D_field,
H_field — actual numbers from actual blocks) into abstract geometric
training images for lora_style_training.py. This is the bridge that
was missing: the LoRA pipeline had no connection to Maxwell at all
until this file.

SAME ABSTRACTION RULE AS EVERYWHERE ELSE IN THIS PROJECT
--------------------------------------------------------------
These are abstract geometric visualizations of real numeric field
data — vector plots, not claimed to be physically accurate renderings
of electromagnetic fields, and never anything biological. Same rule
that governs research_art_generator.py's EEG/hormone stats: real
numbers in, honest abstract visual out, no overclaiming what it
depicts.

WHAT THIS ACTUALLY GENERATES
---------------------------------
For each real Maxwell block with a maxwell_signature, plots the
E/B/D/H field vectors as a geometric composition (arrows/lines from
origin, colored by field type) — genuinely derived from that block's
real numbers, genuinely different from block to block since the
underlying data differs. Auto-generates a caption from the same real
data (div_B value, field magnitudes) for lora_style_training.py's
captions.json.

Usage
-----
    from reconstruct_maxwell_chain import reconstruct_chain
    from maxwell_style_images import generate_style_images

    result = reconstruct_chain(raw_blocks)
    generate_style_images(result["longest_chain"], output_dir="style_images")
    # -> writes maxwell_001.png, maxwell_002.png, ... + captions.json
    # ready to feed straight into lora_style_training.py
"""

from __future__ import annotations
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


FIELD_COLORS = {"E_field": "tab:blue", "H_field": "tab:orange", "B_field": "tab:green", "D_field": "tab:red"}


def _plot_block_as_art(block: dict, output_path: str) -> str | None:
    sig = block.get("maxwell_signature")
    if not sig:
        return None

    fig, ax = plt.subplots(figsize=(6, 6))
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")

    for field_name, color in FIELD_COLORS.items():
        vec = sig.get(field_name)
        if not vec or len(vec) < 2:
            continue
        # project the 3D field vector onto 2D for the composition —
        # a real, if simplified, geometric derivation from real data
        x, y = vec[0], vec[1]
        magnitude = (vec[0] ** 2 + vec[1] ** 2 + (vec[2] if len(vec) > 2 else 0) ** 2) ** 0.5
        ax.arrow(0, 0, x, y, head_width=0.05, color=color, alpha=0.8, linewidth=2 + magnitude)

    # div_B as a background ring — real data shaping the composition
    div_b = abs(sig.get("div_B", 0))
    theta = np.linspace(0, 2 * np.pi, 100)
    ring_r = min(2.0, 0.5 + div_b)
    ax.plot(ring_r * np.cos(theta), ring_r * np.sin(theta), color="white", alpha=0.3, linewidth=1)

    ax.set_xlim(-2.5, 2.5)
    ax.set_ylim(-2.5, 2.5)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, facecolor="black")
    plt.close(fig)
    return output_path


def _caption_for_block(block: dict) -> str:
    """Real caption derived from this block's actual data — no invented description."""
    sig = block.get("maxwell_signature", {})
    div_b = abs(sig.get("div_B", 0))
    return (
        f"abstract geometric composition, arrows on black background, "
        f"blue orange green red field lines, div_b_magnitude={div_b:.2f}"
    )


def generate_style_images(chain: list[dict], output_dir: str = "style_images", max_images: int = 20) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    captions = {}
    count = 0

    for block in chain:
        if count >= max_images:
            break
        filename = f"maxwell_{count:03d}.png"
        path = os.path.join(output_dir, filename)
        result = _plot_block_as_art(block, path)
        if result:
            captions[filename] = _caption_for_block(block)
            count += 1

    captions_path = os.path.join(output_dir, "captions.json")
    with open(captions_path, "w") as f:
        json.dump(captions, f, indent=2)

    return {"generated": count, "output_dir": output_dir, "captions_path": captions_path}


if __name__ == "__main__":
    import tempfile

    try:
        from reconstruct_maxwell_chain import reconstruct_chain
    except ImportError:
        reconstruct_chain = None
        print("reconstruct_maxwell_chain.py not found in this project -- skipping "
              "the real-chain lookup and using the fallback genesis block below.")

    # sandbox upload path from wherever this file was originally drafted --
    # won't exist here or on most machines, hence the os.path.exists guard
    path = "/mnt/user-data/uploads/1788922195282_maxwell_blockchain_20260810_190910.json"
    chain = []
    if reconstruct_chain is not None and os.path.exists(path):
        import json as _json
        with open(path) as f:
            raw_blocks = _json.load(f)
        result = reconstruct_chain(raw_blocks)
        candidate_chain = result["longest_chain"]
        sig_count = sum(1 for b in candidate_chain if "maxwell_signature" in b)
        if sig_count > 0:
            chain = candidate_chain
            print(f"Using your real reconstructed chain: {len(chain)} blocks, {sig_count} with signature data")
        else:
            print(f"Found your 500-block file, but it has ZERO maxwell_signature fields "
                  f"(it's the relay/packet_capture chain, a different one from the "
                  f"physics-signature genesis block seen earlier in the conversation) — "
                  f"falling back to that single real genesis block instead.")

    if not chain:
        # the one real signature-bearing block we've actually seen in this conversation
        chain = [{
            "block_number": 0, "hash": "0000de17", "previous_hash": "0" * 68,
            "maxwell_signature": {
                "E_field": [-0.7113509848713875, -0.9122692961245775, 0.9365450465120375],
                "H_field": [-0.11396875185891986, -0.5658458084799349, -0.7242543883621693],
                "B_field": [-0.44619431253522635, -0.6687429002486169, 0.12956363894045353],
                "D_field": [0.7614728379994631, -0.06811555568128824, 0.2608400313183665],
                "div_B": -0.9853735738433897,
            },
        }]

    output_dir = os.path.join(tempfile.gettempdir(), "maxwell_style_images")
    result = generate_style_images(chain, output_dir=output_dir, max_images=10)
    print(f"\nGenerated {result['generated']} real Maxwell-data-derived training images")
    print(f"Output dir: {result['output_dir']}")

    if result["generated"] > 0:
        with open(result["captions_path"]) as f:
            captions = json.load(f)
        print(f"\nSample captions (real, derived from real field data):")
        for fname, cap in list(captions.items())[:3]:
            print(f"  {fname}: {cap}")
        print(f"\nNOTE: only {result['generated']} real signature-bearing block(s) exist in what "
              f"we've seen so far — nowhere near the 10-20 images LoRA training actually needs. "
              f"See the message below this output for what to do about that.")
