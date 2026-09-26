#!/usr/bin/env python3
"""
work_sharing.py — nodes split the network's recurring work between them,
and take over from each other when one is down or slow.

Rounds: time is cut into rounds of `round_seconds`. Each round has a few
jobs (see WorkSchedule.tasks_for_round): a research lookup every
`research_every` rounds, a Bitcoin/Ethereum chain-tip read every
`external_every` rounds, and one chain audit per round whose target
rotates through the nodes.

Assignment: every node computes the same order for a job from the job id
and the set of known node ids (itself plus every peer whose key it has
pinned), so no coordination messages are needed. The node first in line
does the job at the start of the round; the node k-th in line waits
k * takeover_seconds and only acts if no result has been seen by then. A
node that is down (or slow) is therefore covered by the next one, without
anyone having to detect that it's down.

Results: a finished job is published as an ordinary mined block carrying
{"work": {...}}, so it's Ed25519-signed, replay-protected and verified
exactly like every other block. When a node receives a verified work
block it records the job as done and won't run it itself.

Audits: the auditor fetches the target's recent chain over their
encrypted session (NetworkNode.fetch_peer_chain) and re-checks it
independently: every block hash recomputed, every previous_hash link,
consecutive indices, and every block's origin and Ed25519 signature
against the target's pinned key. Problems are reported in the audit's
work block, so every node (and the daily report) sees them.

Clocks: rounds come from each node's wall clock. On one PC that's exact;
across machines, keep clocks NTP-synced (Windows does this by default).
A skewed clock only causes an occasional duplicated or late job.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from chain_store import ChainBlock, GENESIS_PREV_HASH
from dna_binary_codec import decode_from_dna
from external_chain_bridge import build_external_info_snapshot


@dataclass(frozen=True)
class WorkTask:
    task_id: str
    kind: str          # "research" | "external_info" | "audit"
    round_no: int
    start: float       # wall-clock time the first-in-line node should act
    target: Optional[int] = None   # audit target node id


def fetch_research() -> dict:
    """ClinicalTrials.gov lookup (same query the consolidated runner uses)."""
    try:
        resp = requests.get(
            "https://clinicaltrials.gov/api/v2/studies",
            params={"query.cond": "leukemia", "pageSize": 1, "format": "json"},
            timeout=8,
        )
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
        ident = studies[0]["protocolSection"]["identificationModule"] if studies else {}
        return {"source": "clinicaltrials.gov", "id": ident.get("nctId", "?")}
    except Exception as e:
        return {"source": "none", "error": str(e)}


def order_for(task_id: str, node_ids: list[int]) -> list[int]:
    """Deterministic, per-task rotation of the candidate nodes, so the
    first-in-line role is spread evenly and every node computes the same
    order."""
    ids = sorted(set(node_ids))
    if not ids:
        return []
    shift = int.from_bytes(hashlib.sha256(task_id.encode()).digest()[:4], "big") % len(ids)
    return ids[shift:] + ids[:shift]


class WorkSchedule:
    def __init__(self, round_seconds: float = 300.0, research_every: int = 3,
                 external_every: int = 2, audits: bool = True):
        self.round_seconds = round_seconds
        self.research_every = research_every
        self.external_every = external_every
        self.audits = audits

    def round_of(self, t: float) -> int:
        return int(t // self.round_seconds)

    def tasks_for_round(self, round_no: int, node_ids: list[int]) -> list[WorkTask]:
        start = round_no * self.round_seconds
        tasks = []
        if self.research_every and round_no % self.research_every == 0:
            tasks.append(WorkTask(f"research:{round_no}", "research", round_no, start))
        if self.external_every and round_no % self.external_every == 0:
            tasks.append(WorkTask(f"external_info:{round_no}", "external_info", round_no, start))
        ids = sorted(set(node_ids))
        if self.audits and len(ids) >= 2:
            target = ids[round_no % len(ids)]
            tasks.append(WorkTask(f"audit:{round_no}:node-{target}", "audit", round_no, start, target))
        return tasks

    def candidates(self, task: WorkTask, node_ids: list[int]) -> list[int]:
        # a node never audits itself
        pool = [n for n in node_ids if n != task.target]
        return order_for(task.task_id, pool)


def audit_chain(blocks: list[dict], target_id: int, target_key_hex: Optional[str],
                identity_strand: str) -> dict:
    """Independent re-verification of (a window of) another node's chain.
    `blocks` are ChainBlock dicts, oldest first."""
    from crypto_layer import signing_pub_from_hex, verify
    from network_node import block_signing_bytes

    problems: list[str] = []
    pub = signing_pub_from_hex(target_key_hex) if target_key_hex else None
    if pub is None:
        problems.append("no pinned signing key for target")

    prev: Optional[dict] = None
    for raw in blocks:
        try:
            b = ChainBlock(**raw)
        except TypeError:
            problems.append("malformed chain block")
            continue
        label = f"block #{b.index}"
        if b.compute_hash() != b.block_hash:
            problems.append(f"{label}: stored hash does not match recomputed hash")
        if prev is None:
            if b.index == 0 and b.previous_hash != GENESIS_PREV_HASH:
                problems.append(f"{label}: first block doesn't link to genesis")
        else:
            if b.index != prev["index"] + 1:
                problems.append(f"{label}: index gap after #{prev['index']}")
            if b.previous_hash != prev["block_hash"]:
                problems.append(f"{label}: previous_hash doesn't link to #{prev['index']}")
        payload = b.payload if isinstance(b.payload, dict) else {}
        if payload.get("origin") != target_id:
            problems.append(f"{label}: origin is {payload.get('origin')!r}, not node-{target_id}")
        if payload.get("identity_strand") != identity_strand:
            problems.append(f"{label}: identity strand differs from this network's")
        try:
            if decode_from_dna(payload["strand"]) != bytes.fromhex(payload["hash_hex"]):
                problems.append(f"{label}: strand doesn't encode its hash")
        except Exception:
            problems.append(f"{label}: missing/invalid strand or hash")
        if pub is not None:
            try:
                sig_ok = verify(pub, block_signing_bytes(payload), bytes.fromhex(payload.get("sig", "")))
            except ValueError:
                sig_ok = False
            if not sig_ok:
                problems.append(f"{label}: signature invalid for node-{target_id}'s pinned key")
        prev = raw

    return {
        "target": target_id,
        "ok": not problems and bool(blocks),
        "blocks_checked": len(blocks),
        "problems": problems[:10],
        "problem_count": len(problems),
    }


class WorkManager:
    """Attach to a NetworkNode with WorkManager(node, schedule).attach()."""

    def __init__(self, node, schedule: WorkSchedule, takeover_seconds: float = 20.0,
                 clock: Callable[[], float] = time.time, runners: Optional[dict] = None):
        self.node = node
        self.schedule = schedule
        self.takeover_seconds = takeover_seconds
        self.clock = clock
        # kind -> zero-arg blocking function returning the result dict
        self.runners = {"research": fetch_research, "external_info": build_external_info_snapshot}
        self.runners.update(runners or {})
        self.done: dict[str, dict] = {}          # task_id -> {"by": id, "at": t}
        self.in_progress: set[str] = set()
        self.first_seen: dict[str, float] = {}   # task_id -> when this node first saw it
        self.gave_up: set[str] = set()           # jobs this node left for the next in line
        self.stats: Counter = Counter()
        self.recent_audits: list[dict] = []

    def attach(self) -> "WorkManager":
        self.node.on_verified_block.append(self._on_block)
        self.node.background.append(self.loop)
        self.node.work = self
        return self

    def node_ids(self) -> list[int]:
        return sorted({self.node.node_id, *self.node.peer_signing_keys})

    # -- receiving results --

    def _on_block(self, block: dict) -> None:
        work = block.get("work")
        if not isinstance(work, dict) or "task" not in work:
            return
        task_id = work["task"]
        if task_id in self.done:
            self.stats["duplicate_results_seen"] += 1
            return
        self.done[task_id] = {"by": block.get("origin"), "at": self.clock()}
        self.stats[f"results_received:{work.get('kind')}"] += 1
        if work.get("kind") == "audit":
            self._note_audit(work, block.get("origin"))

    def _note_audit(self, work: dict, by) -> None:
        result = work.get("result") or {}
        entry = {"round": work.get("round"), "by": by, **result}
        self.recent_audits = (self.recent_audits + [entry])[-20:]
        if not result.get("ok"):
            self.node.log(f"!! audit by node-{by} found problems in node-{result.get('target')}'s chain: "
                          f"{result.get('problems')}")

    # -- doing work --

    def due(self, now: float) -> list[tuple[WorkTask, int]]:
        """(task, my position in line) for tasks this node should run now."""
        ids = self.node_ids()
        current = self.schedule.round_of(now)
        out = []
        for r in (current - 1, current):   # takeovers can cross a round boundary
            for task in self.schedule.tasks_for_round(r, ids):
                if task.task_id in self.done or task.task_id in self.in_progress                         or task.task_id in self.gave_up:
                    continue
                seen = self.first_seen.setdefault(task.task_id, now)
                if r < current and seen >= task.start + self.schedule.round_seconds:
                    # only noticed after its round ended (e.g. this node
                    # just started): not ours to catch up on
                    continue
                line = self.schedule.candidates(task, ids)
                if self.node.node_id not in line:
                    continue
                pos = line.index(self.node.node_id)
                # The wait for position k counts from when the job became
                # visible to this node, not only from the round start, so
                # nodes that start late keep their order instead of all
                # acting at once.
                if now >= max(task.start, seen) + pos * self.takeover_seconds:
                    out.append((task, pos))
        return out

    async def run_task(self, task: WorkTask, position: int) -> None:
        self.in_progress.add(task.task_id)
        try:
            if task.kind == "audit":
                result = await self._run_audit(task)
                if result is None:   # target unreachable: leave it for the next in line
                    self.stats["audit_target_unreachable"] += 1
                    self.gave_up.add(task.task_id)
                    return
            else:
                result = await asyncio.to_thread(self.runners[task.kind])
            if task.task_id in self.done:   # someone else finished first while we worked
                self.stats["work_finished_late"] += 1
                return
            work = {"task": task.task_id, "kind": task.kind, "round": task.round_no,
                    "by": self.node.node_id, "takeover": position > 0, "result": result}
            self.done[task.task_id] = {"by": self.node.node_id, "at": self.clock()}
            self.stats[f"work_done:{task.kind}"] += 1
            if position > 0:
                self.stats["takeovers"] += 1
                self.node.log(f"took over {task.task_id} (position {position} in line)")
            if task.kind == "audit":
                self._note_audit(work, self.node.node_id)
            await self.node.mine_and_gossip(extra={"work": work}, run_enrichers=False)
        except Exception as e:
            self.stats["work_errors"] += 1
            self.node.log(f"work {task.task_id} failed: {e}")
        finally:
            self.in_progress.discard(task.task_id)

    async def _run_audit(self, task: WorkTask) -> Optional[dict]:
        addr = self.node.address_of(task.target)
        if addr is None:
            return None
        blocks = await self.node.fetch_peer_chain(addr)
        if blocks is None:
            return None
        return audit_chain(blocks, task.target, self.node.peer_signing_keys.get(task.target),
                           self.node.identity_strand)

    def prune(self, now: float) -> None:
        oldest = self.schedule.round_of(now) - 3
        for table in (self.done, self.first_seen):
            for tid in [t for t in table if int(t.split(":")[1]) < oldest]:
                del table[tid]
        self.gave_up = {t for t in self.gave_up if int(t.split(":")[1]) >= oldest}

    async def loop(self, stop_event: asyncio.Event) -> None:
        running: set[asyncio.Task] = set()
        while not stop_event.is_set():
            now = self.clock()
            for task, pos in self.due(now):
                t = asyncio.create_task(self.run_task(task, pos))
                running.add(t)
                t.add_done_callback(running.discard)
            self.prune(now)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        for t in list(running):
            t.cancel()

    def status(self) -> dict:
        return {"stats": dict(self.stats), "recent_audits": self.recent_audits[-10:]}
