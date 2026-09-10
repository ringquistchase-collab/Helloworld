# KNOWN_GAPS.md — honestly disclosed, not silently assumed

Same spirit as every module docstring in this project: real code
should say plainly what it hasn't proven yet, rather than let a
confident-looking file imply more than it's actually verified.

## Structurally correct, not execution-verified

- **`integrated_research_agent.py`'s DALL-E visuals hook** — follows
  OpenAI's documented `images/generations` REST shape, but has never
  been run against a live `OPENAI_API_KEY` (none is configured in the
  environment this was built in). Treat it the same way
  `lora_style_training.py` already treats its own GPU dependency:
  real code, unconfirmed by an actual run.
- **`lora_style_training.py`** — same status as documented in its own
  header: structurally correct against `diffusers`/`peft`'s real API,
  never run to completion (needs a GPU this environment doesn't
  have). Its heavier dependencies (`diffusers`, `peft`, `accelerate`,
  `torch`, `torchvision`) are deliberately NOT in `requirements.txt` —
  installing a multi-GB CUDA toolchain isn't something CI or a normal
  `pip install -r requirements.txt` should do by default.

## Documented as not implemented

- **Video generation** (`runway_api_key` in `integrated_research_agent.py`)
  — accepted as a parameter, deliberately left unimplemented. Writing
  untested code against a less-standardized API seemed worse than an
  honest gap; see that file's own docstring.
- **`crispr_guide_design.py`'s scoring** — a real, published GC-content
  heuristic, explicitly NOT the machine-learned on/off-target model
  real tools like CRISPOR use (those need experimental training data
  this project doesn't have). Good for narrowing candidates, not a
  clinical-grade final answer — see that file's own docstring.
- **St. Jude Cloud source** (`growing_research_agent.py`'s
  `_fetch_stjude`) — a deliberate no-op; St. Jude Cloud doesn't
  publish a stable unauthenticated search endpoint. Logs once and
  returns no results rather than guessing at a URL.

## Test coverage gaps

- **CI only exercises the Python interop client.** `tests/` and the
  GitHub Actions workflow don't set up Go/Ruby/C++/Java/JS toolchains,
  so the other five `interop_client.*` files are verified by having
  been run manually (see this project's commit history / TRUST.md)
  but aren't covered by automated regression testing yet. Adding
  per-language CI jobs (`actions/setup-go`, `ruby/setup-ruby`,
  `actions/setup-java`, apt-get for g++/openssl, Node is on the
  default runner already) is a reasonable follow-up, not done here to
  keep this round of work scoped to Python.
- **`growing_research_agent.py` / `integrated_research_agent.py` /
  `crispr_research_suite.py`'s live network paths are not covered by
  the automated test suite.** They hit real external APIs
  (ClinicalTrials.gov, PubMed, ClinVar, HGNC) — running those on every
  CI push would be slow, rate-limit-sensitive, and non-deterministic
  (result counts change over time as real new studies/papers appear).
  They've been verified by manual runs during development (see commit
  messages), not by an automated, repeatable test.
- **Same applies to `maxwell_research.py`, `research_matcher.py`,
  `multi_source_research.py`, and `extended_research_sources.py`**
  (arXiv, ClinicalTrials.gov, PubMed, NIH RePORTER, Europe PMC) and
  the file that ties three of them together, `ptsd_research.py` — all
  verified by manual runs against the live APIs, none in the
  automated suite, same reasoning as above.

## Why certifi is a dependency

`research_matcher.py`, `multi_source_research.py`,
`extended_research_sources.py`, and `maxwell_research.py` all pass an
explicit `ssl.create_default_context(cafile=certifi.where())` to
their `urlopen()` calls instead of relying on the platform default.
Found during development: `api.reporter.nih.gov`'s certificate chain
verified fine via curl (which used Windows' native schannel/OS trust
store) but failed Python's default OpenSSL trust store with
`CERTIFICATE_VERIFY_FAILED` — the two stores don't necessarily agree,
and this specific government API's chain was the case that surfaced
it. certifi's bundle is a well-maintained, portable CA set that
doesn't depend on what a given machine happens to have installed;
this wasn't a security downgrade (verification stays on, just against
a more complete/portable bundle) and shouldn't be reverted to "just
use the default" without re-checking this specific failure mode.

## TRUST.md references files that don't exist yet

`TRUST.md`'s "What must stay open source" list names
`signal_stats_bridge.py` and `research_art_generator.py` as examples
of files that decide what's consented/hashed/local — neither exists
in this repo. Flagged at the very start of this project's work in
this repo and never resolved; noted here rather than silently edited
out, since removing the reference would understate what TRUST.md's
open-source commitment is meant to cover once those files (or
equivalents) exist.

## Design choices that look like gaps but aren't

- **`network_ledger` and the mined chain are deliberately separate**
  (`network_os.py`'s own docstring covers this at length) — not an
  oversight, a boundary.
- **`crispr_research_suite.py` never mines into the chain** — verified
  by construction (`node=None, dna=None` on its internal research
  agent), not a missing integration.
- **`token_ledger.py`'s balances are not a cryptocurrency** — no
  consensus, not tradable, purely a local, per-node score like DAS/MOS.
  Worth stating explicitly so "token" doesn't get over-read.
