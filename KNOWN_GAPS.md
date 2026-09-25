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
- **`research_art_generator.py`'s `generate_network_art()`** — same
  GPU dependency and same `diffusers`/`torch` status as
  `lora_style_training.py`; loads that file's LoRA output if present.
  `_build_art_prompt()`, the deterministic prompt-building half of
  this file, IS fully tested (`tests/test_research_art_generator.py`)
  since it needs no GPU — only the actual image generation is
  unverified here.

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
- **The *live* network round trips are still not covered by the
  automated test suite.** `growing_research_agent.py` /
  `integrated_research_agent.py` / `crispr_research_suite.py` hit real
  external APIs (ClinicalTrials.gov, PubMed, ClinVar, HGNC) — actually
  calling those on every CI push would be slow, rate-limit-sensitive,
  and non-deterministic (result counts change as real new
  studies/papers appear), so the live calls remain verified by manual
  runs during development (see commit messages), not by CI.
- **The *response parsing* for the fetchers IS now covered offline.**
  `tests/test_growing_research_agent_fetchers.py` mocks the module's
  `_http_get_json` and exercises the real parsing/sequencing of
  `_fetch_pubmed`, `_fetch_clinicaltrials`, the two-step
  `_fetch_clinvar` (including candidate-gene extraction and the
  esummary-failure fallback), `_fetch_hgnc`, and the `_fetch_stjude`
  no-op — deterministically, without touching the network.
  `multi_source_research.py` (PubMed esearch+esummary) and
  `extended_research_sources.py` (NIH RePORTER, Europe PMC) are
  likewise covered by `tests/test_multi_source_research.py` and
  `tests/test_extended_research_sources.py`, mocking
  `urllib.request.urlopen`. What these guard is the field-mapping,
  term-building, `max_results` slicing, and failure-returns-`[]`
  behavior — i.e. regressions in *our* code, not the remote APIs.
- **`maxwell_research.py`, `research_matcher.py`, and `ptsd_research.py`
  parsing is now covered offline too.** `tests/test_maxwell_research.py`
  exercises the arXiv Atom-XML parsing, title-whitespace normalization,
  empty feed, and the distinctive `[{"error": ...}]` failure shape (this
  fetcher signals failure differently from the `[]`-returning ones).
  `tests/test_research_matcher.py` covers ClinicalTrials.gov field
  mapping, no-`nctId` skipping, `max_results` slicing, and the optional
  `biomarker`/`recruiting_only` query params. `tests/test_ptsd_research.py`
  stubs the four underlying fetchers to pin the aggregation shape, the
  exact arguments passed to each source, and `format_results` rendering.
  As with the others, these guard *our* parsing/orchestration, not the
  live remote APIs.

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

## TRUST.md's referenced boundary files now all exist (closed)

`TRUST.md`'s "What must stay open source" list names
`signal_stats_bridge.py` and `research_art_generator.py` as examples
of files that decide what's consented/hashed/local. Both now exist:
`research_art_generator.py` (see its own "ABSTRACTION BOUNDARY"
docstring section) and `signal_stats_bridge.py`, which enforces the
EEG/hormone-telemetry boundary in code — `fold_signal_into_strand()`
restricts the source to the `*_telemetry` channels in
`ALLOWED_SOURCES`, summarizes raw readings to abstract stats, and
folds ONLY a hash of those stats through `digital_dna.py`'s real
consent gate (raw readings never reach the gate). Both are hashed by
`project_identifier.py` and covered by the test suite
(`tests/test_signal_stats_bridge.py`,
`tests/test_research_art_generator.py`). This previously-open gap is
closed.

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
