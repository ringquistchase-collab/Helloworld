# TRUST.md — what stays open, and why

This project is MIT-licensed. This file makes explicit which parts
that openness is actually protecting, so it doesn't quietly erode as
the project grows or gets built on by others.

## The core claim this project makes

That a person's data — identity strand, research activity, any
consented biosignal — is only ever captured, stored, and shared the
way `ALLOWED_SOURCES` and the consent gates in `digital_dna.py`
describe. That claim is only worth anything if someone other than the
author can verify it's true by reading the code.

## Verifying this yourself

`project_identifier.py` hashes every tracked source file's actual
bytes (SHA-256) and folds them into one project-wide identifier — see
`PROJECT_FILES` in that file for exactly what's covered. Run it
yourself:

    python3 project_identifier.py

Last verified: `project_id 97c46dfc10bc75eb57ae668ac244915d2511e10b338db3a43adcb1d50e60f960`
(21/21 files), against commit `fa0a5af` on `master` (2026-09-09) — the
state right after `maxwell_chain_agent.py` and
`integrated_maxwell_agent.py` were added. If your own run against
that same commit produces a different identifier, something changed
that shouldn't have — that's the entire point of running it.

Note the inherent limit here: since this file is itself one of the
hashed files, editing this note to record a new identifier changes
`project_id` again, immediately. This note can only ever state the
identifier as of the commit *before* the edit that added it — it can
never contain its own post-edit identifier. That's not a bug, it's
what makes the claim checkable: pin the note to a specific commit,
and anyone can reproduce it exactly.

## What must stay open source

- `digital_dna.py` — the consent gate (`add_live_signal`,
  `ALLOWED_SOURCES`) that every personal-data source goes through
- `network_os.py` / `crypto_layer.py` — the authentication and
  encryption logic, so a reviewer can confirm handshakes are really
  verified and traffic is really encrypted, not just claimed to be
- Any file that decides what counts as "consented," what gets hashed
  vs. stored raw, or what leaves a device vs. stays local
  (`signal_stats_bridge.py`, `research_art_generator.py`'s
  abstraction boundary, etc.)

Keeping these open is what lets a security researcher, a grant
reviewer, or a journalist verify the privacy story directly, rather
than take a claim on faith.

## What's fine to be private/closed

Per the interoperability work already done (`PROTOCOL.md` and interop
clients in Go, Ruby, C++, Java, and JavaScript, plus a standalone
client in Python, all exercising it against the Python reference
implementation): any CLIENT that speaks the documented
protocol can be closed-source — a company's proprietary app, a
private research tool — the same way closed software can speak
HTTPS without publishing its source. The protocol is public; what
consumes it doesn't have to be.

The distinction that matters: **capture and consent logic open,
client implementations optional to be open.** A private client is
only as trustworthy as its own disclosed practices — this project
can't verify those on its behalf, and doesn't claim to.

## If this project is ever forked or commercialized

The parts listed under "must stay open source" should stay open in
any fork or derivative that still claims to follow this project's
consent model. A fork that closes those files and keeps using
language like "consent-verified" or "DAS-scored" without the
verifiable mechanism behind it would be making a claim it can't back
up — which is exactly the failure mode this file exists to name in
advance.
