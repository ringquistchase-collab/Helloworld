"""
project_identifier.py
================
A single, real, verifiable identifier for the ENTIRE codebase built
in this project — every file's actual bytes, hashed, combined into
one fingerprint. Same technique digital_dna.py uses for chain blocks
(hash each real thing, fold them together), applied one level up: to
the code itself rather than to events the code produces.

WHAT THIS ACTUALLY PROVES
------------------------------
If you run this against your files and get identifier X, and later
run it again and still get X, you've verified NOTHING changed — not
one byte, in any tracked file. If a single file changes, X changes
completely (that's what a hash is supposed to do). This is real
integrity checking, not a version number someone incremented by hand.

WHAT THIS IS NOT
--------------------
Not a cryptographic signature (no private key involved — anyone can
recompute this from the files, which is the point: it's for YOU to
verify your own files haven't drifted, not to prove authorship to
someone else). Not a replacement for git — git already tracks
per-file history; this gives you one number for "the whole project,
right now" that's fast to compare.

Usage
-----
    python3 project_identifier.py
    # -> prints the current project identifier and a per-file manifest

    from project_identifier import compute_project_identifier
    result = compute_project_identifier()
    print(result["project_id"])
"""

from __future__ import annotations
import hashlib
import json
import os


# Every file this project actually produced, across the whole conversation.
# Deliberately an explicit list, not a glob — a glob would silently absorb
# unrelated files dropped in the same folder later and misrepresent them
# as part of this project's verified identity.
PROJECT_FILES = [
    "digital_dna.py", "network_os.py", "crypto_layer.py",
    "growing_research_agent.py", "run_agent.py", "run_all.py", "run_local_network.py",
    "live_helix_network.py", "self_and_research_helix_live.py", "live_identity_network.py",
    "live_identity_network_with_tokens.py",
    "lora_style_training.py", "maxwell_style_images.py", "maxwell_research.py",
    "maxwell_chain_agent.py", "integrated_maxwell_agent.py",
    "audit_trail.py", "token_ledger.py", "ai_gossip.py",
    "integrated_research_agent.py", "crispr_guide_design.py", "crispr_research_suite.py",
    "ptsd_research.py", "research_matcher.py", "multi_source_research.py", "extended_research_sources.py",
    "connection_behavior_stats.py", "research_art_generator.py", "peer_history_store.py",
    "signal_stats_bridge.py", "dna_binary_codec.py",
    "interop_client.go", "interop_client.rb", "interop_client.cpp",
    "interop_client.js", "interop_client.py", "JavaInteropClient.java",
    "dna_codec.js",
    "project_identifier.py",
    "PROTOCOL.md", "TRUST.md", "KNOWN_GAPS.md",
    "maxwell blockchain.py",
    "requirements.txt", "pytest.ini",
    "tests/conftest.py", "tests/test_crypto_layer.py", "tests/test_digital_dna.py",
    "tests/test_maxwell_chain_agent.py", "tests/test_audit_trail.py",
    "tests/test_token_ledger.py", "tests/test_crispr_guide_design.py", "tests/test_network_os.py",
    "tests/test_connection_behavior_stats.py", "tests/test_research_art_generator.py",
    "tests/test_peer_history_store.py", "tests/test_signal_stats_bridge.py",
    "tests/test_dna_binary_codec.py", "tests/test_self_and_research_helix.py",
    "tests/test_dna_codec_js.py",
    ".github/workflows/verify.yml",
    "scripts/local_verify_node.py", "scripts/verify.ps1",
    "scripts/verify.sh", "scripts/verify.bat",
]


def compute_project_identifier(base_dir: str = ".") -> dict:
    file_hashes = {}
    missing = []

    for filename in sorted(PROJECT_FILES):
        path = os.path.join(base_dir, filename)
        if not os.path.exists(path):
            missing.append(filename)
            continue
        with open(path, "rb") as f:
            file_hashes[filename] = hashlib.sha256(f.read()).hexdigest()

    # fold every file's hash together, in sorted-filename order, so the
    # result is deterministic regardless of filesystem listing order
    combined = hashlib.sha256()
    for filename in sorted(file_hashes.keys()):
        combined.update(filename.encode())
        combined.update(file_hashes[filename].encode())

    return {
        "project_id": combined.hexdigest(),
        "file_count": len(file_hashes),
        "missing_files": missing,
        "file_hashes": file_hashes,
    }


def save_manifest(result: dict, path: str = "project_manifest.json"):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(result, f, indent=2)
    os.replace(tmp_path, path)


def verify_against_manifest(path: str = "project_manifest.json", base_dir: str = ".") -> dict:
    """Re-hashes the current files and compares against a previously saved
    manifest — tells you exactly which files changed, if any."""
    if not os.path.exists(path):
        return {"error": f"no manifest found at {path}"}
    with open(path) as f:
        saved = json.load(f)

    current = compute_project_identifier(base_dir)
    changed = [
        f for f in saved["file_hashes"]
        if current["file_hashes"].get(f) != saved["file_hashes"][f]
    ]
    new_files = [f for f in current["file_hashes"] if f not in saved["file_hashes"]]

    return {
        "identical": current["project_id"] == saved["project_id"],
        "old_project_id": saved["project_id"],
        "new_project_id": current["project_id"],
        "changed_files": changed,
        "new_files_since_manifest": new_files,
    }


if __name__ == "__main__":
    result = compute_project_identifier()

    print("=== Project identifier ===")
    print(f"project_id: {result['project_id']}")
    print(f"files hashed: {result['file_count']} / {len(PROJECT_FILES)}")
    if result["missing_files"]:
        print(f"missing (not found in this folder): {result['missing_files']}")

    save_manifest(result)
    print(f"\nSaved manifest to project_manifest.json")

    print("\n=== Verifying determinism: computing it again ===")
    result2 = compute_project_identifier()
    print(f"Same identifier on second computation: {result['project_id'] == result2['project_id']}")

    print("\n=== Proving it detects real changes ===")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for f in ["digital_dna.py"]:
            src = f
            if os.path.exists(src):
                with open(src) as fh:
                    content = fh.read()
                dest = os.path.join(tmp, f)
                os.makedirs(os.path.dirname(dest) or tmp, exist_ok=True)
                with open(dest, "w") as fh:
                    fh.write(content + "\n# tampered")
        # copy the rest unchanged for a fair comparison
        for f in PROJECT_FILES:
            if f != "digital_dna.py" and os.path.exists(f):
                with open(f, "rb") as fh:
                    content = fh.read()
                dest = os.path.join(tmp, f)
                os.makedirs(os.path.dirname(dest) or tmp, exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(content)

        tampered_result = compute_project_identifier(base_dir=tmp)
        print(f"Identifier changes after modifying ONE file: {tampered_result['project_id'] != result['project_id']}")
