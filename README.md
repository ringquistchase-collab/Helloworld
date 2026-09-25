# network-os-project

A local-first, peer-to-peer research node. It keeps a signed personal
data strand (`digital_dna.py`), gossips signed blocks to peers you
explicitly connect to (`network_os.py`), and runs an autonomous
literature-research agent over **real public biomedical APIs**. Every
module logs to one tamper-evident audit trail, and every run is tied to
a verifiable hash of the exact code state (`project_identifier.py`).

It is **MIT-licensed** and **yours**. It runs on plain Python. It does
not require Claude, an internet account, a subscription, or GitHub to
run — see [Owning your copy](#owning-your-copy).

---

## What it actually does

| Piece | File | What's real |
|---|---|---|
| Identity + chain | `digital_dna.py`, `crypto_layer.py` | Real cryptographic signing; a per-node DNA-encoded strand |
| P2P networking | `network_os.py` | Real sockets; only connects to peers you name explicitly |
| Research agent | `growing_research_agent.py`, `integrated_research_agent.py` | Live queries to ClinicalTrials.gov, PubMed, ClinVar, HGNC |
| Extra sources | `multi_source_research.py`, `extended_research_sources.py`, `maxwell_research.py` | arXiv, NIH RePORTER, Europe PMC, PubMed metadata |
| CRISPR suite | `crispr_research_suite.py`, `crispr_guide_design.py` | Literature tracking + a published GC-content guide heuristic |
| Ledger + audit | `token_ledger.py`, `audit_trail.py` | Local per-node score (not a cryptocurrency); append-only audit log |
| Provenance | `project_identifier.py` | Hash manifest tying each run to an exact code state |
| Entry points | `run_all.py`, `run_agent.py` | Launch everything, or just the research agent |

**Honest boundaries** (see [`KNOWN_GAPS.md`](KNOWN_GAPS.md) for the full list):
- `token_ledger.py` balances are a **local score**, not a tradable currency — no consensus, no wallet.
- The CRISPR guide scorer is a **GC-content heuristic** for narrowing candidates, *not* a clinical-grade on/off-target model. Candidates need real wet-lab validation.
- CRISPR results **never mine into the chain** — that boundary is enforced by construction (`node=None, dna=None`).
- Image/video generation is off unless you set your own API keys, and video is deliberately unimplemented.
- The node **connects to no peers on its own**. It reaches only the public research APIs listed above, and only when a topic is explored.

---

## Requirements

- **Python 3.14** (developed and tested on 3.14.6)
- The packages in [`requirements.txt`](requirements.txt): `cryptography`, `numpy`, `matplotlib`, `biopython`, `certifi`, plus `pytest` + `pytest-asyncio` for the tests.

```bash
python -m pip install -r requirements.txt
```

## Quickstart

Run everything (opens a local node on `0.0.0.0:8765`, seeds one research
topic, then idles until Ctrl+C — all state is saved to JSON files beside
the code and reloaded on the next run):

```bash
python run_all.py
```

Just the research agent, no networking node:

```bash
python run_agent.py
```

Edit the block near the top of `run_all.py` to change the seed label,
port, and the condition/biomarker it researches:

```python
SEED_LABEL = "my-research-node"
HOST, PORT = "0.0.0.0", 8765
CONDITION  = "breast cancer"
BIOMARKER  = "BRCA1"
```

## Running the tests

```bash
python -m pytest -q
```

As of this writing: **58 tests pass** offline in a few seconds. The
tests deliberately do **not** hit the live external APIs (those are
verified by manual runs — see [`KNOWN_GAPS.md`](KNOWN_GAPS.md)) so the
suite stays fast and deterministic.

---

## Owning your copy

This project belongs to you and does not depend on any paid service to
keep working:

- **It's just files.** Everything is plain `.py` on your disk. If any
  subscription lapsed tomorrow, nothing here stops working — you run it
  with the Python already on your machine.
- **Local version history (no account needed).** You can keep a full
  history entirely on your own disk, with no remote and no GitHub:

  ```bash
  git init
  git add .
  git commit -m "snapshot"
  ```

  That history lives in `.git/` in this folder. It never leaves your
  machine unless *you* choose to push it somewhere.

- **Back it up somewhere you hold.** Copy this whole folder to a second
  drive or a USB stick. Those copies are yours to keep, restore, or
  delete at any time.

You keep the off-switch. Nothing here runs on its own, reaches beyond
the APIs listed above, or persists anywhere you didn't put it.

---

## Project docs

- [`TRUST.md`](TRUST.md) — what stays open source, and why
- [`PROTOCOL.md`](PROTOCOL.md) — the wire/gossip protocol
- [`KNOWN_GAPS.md`](KNOWN_GAPS.md) — what's verified vs. structurally-correct-but-unrun, disclosed plainly
