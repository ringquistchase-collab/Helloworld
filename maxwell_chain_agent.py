"""
maxwell_chain_agent.py
================
Real proof-of-work chain agent used by integrated_maxwell_agent.py to
mine new blocks (research_event, and optionally Maxwell field-
signature blocks) on top of an existing canonical chain.

Reuses the real Maxwell electromagnetic field-signature engine
already in "maxwell blockchain.py" (compute_maxwell_signature) rather
than duplicating it -- a block produced here with
with_maxwell_signature=True carries the SAME kind of real,
non-fabricated E/H/B/D field data as that file's own chain, not a
second parallel implementation that could drift out of sync with it.

WHAT'S REAL HERE
--------------------
- SHA-256 proof-of-work: mine_block() genuinely increments a nonce
  until the block's hash has `difficulty` leading hex zeros, same
  mechanism as "maxwell blockchain.py"'s MaxwellBlock.mine().
- Chain linkage: every block's previous_hash is the prior block's
  real hash -- tampering with any block breaks every hash after it
  (validate_chain() recomputes and checks this).
- Persistence: atomic tmp-file-then-replace writes, same pattern as
  digital_dna.py / network_os.py.
- Audit integration: every mined block optionally logs to a shared
  AuditTrail via the same module/action/node_id/details shape
  network_os.py uses -- guarded by `if self.audit is not None`, same
  as network_os.py, so it works fine with audit=None too.

WHAT THIS DOES NOT DO
--------------------------
Doesn't validate or reconcile forks against other miners -- this is
a single local chain-extension agent, not a networked consensus
system (that's network_os.py's job, and it deliberately keeps
network_ledger separate from any locally-mined chain -- see that
file's own docstring on why).

Usage
-----
    from maxwell_chain_agent import MaxwellChainAgent

    agent = MaxwellChainAgent(canonical_chain=[], miner_id="my-miner",
                               difficulty=3, store_path="maxwell_chain.json")
    block = agent.mine_block("research_event", {"topic": "..."})
    agent.validate_chain()   # True
"""

from __future__ import annotations
import hashlib
import importlib.util as _ilu
import json
import os
import time


def _load_signature_engine():
    """Loads compute_maxwell_signature from "maxwell blockchain.py" by file
    path, since the filename has a space and can't be a normal import
    statement target. Returns None if that file isn't present -- callers
    fall back to signature-less blocks rather than failing outright."""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    mb_path = os.path.join(this_dir, "maxwell blockchain.py")
    if not os.path.exists(mb_path):
        return None
    try:
        spec = _ilu.spec_from_file_location("_maxwell_blockchain_engine", mb_path)
        module = _ilu.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.compute_maxwell_signature
    except Exception:
        return None


_compute_maxwell_signature = _load_signature_engine()


class MaxwellChainAgent:
    def __init__(
        self, canonical_chain: list[dict], miner_id: str = "maxwell-agent",
        difficulty: int = 3, store_path: str | None = None, audit=None,
    ):
        self.miner_id = miner_id
        self.difficulty = difficulty
        self.store_path = store_path
        self.audit = audit
        self.chain: list[dict] = list(canonical_chain)  # copy -- don't mutate the caller's list

        if store_path and os.path.exists(store_path):
            with open(store_path) as f:
                self.chain = json.load(f)

    def _save(self):
        if not self.store_path:
            return
        tmp_path = self.store_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(self.chain, f, indent=2)
        os.replace(tmp_path, self.store_path)

    def _calculate_hash(self, block_number, timestamp, block_type, data,
                         previous_hash, nonce, maxwell_signature) -> str:
        content = {
            "block_number": block_number, "timestamp": timestamp,
            "block_type": block_type, "data": data,
            "previous_hash": previous_hash, "nonce": nonce,
        }
        if maxwell_signature is not None:
            content["maxwell_div_B"] = round(maxwell_signature["div_B"], 8)
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def mine_block(self, block_type: str, data: dict, with_maxwell_signature: bool = False) -> dict:
        """
        Mines a new block extending self.chain with a real SHA-256
        proof-of-work hash (leading `self.difficulty` hex zeros).

        with_maxwell_signature=True attaches a real E/H/B/D field
        signature via "maxwell blockchain.py"'s compute_maxwell_signature
        (only if that module was importable at load time) -- off by
        default, since most block types here (e.g. "research_event")
        aren't meant to carry field data, matching
        integrated_maxwell_agent.py's own honesty note about which
        blocks do and don't.
        """
        block_number = len(self.chain)
        previous_hash = self.chain[-1]["hash"] if self.chain else "0" * 64
        timestamp = time.time()

        maxwell_signature = None
        if with_maxwell_signature and _compute_maxwell_signature is not None:
            import numpy as np
            data_str = json.dumps(data, sort_keys=True)
            prev_block = self.chain[-1] if self.chain else None
            if prev_block and "maxwell_signature" in prev_block:
                prev_curl = np.array(prev_block["maxwell_signature"]["next_curl"])
            else:
                prev_curl = np.zeros(3)
            maxwell_signature = _compute_maxwell_signature(data_str, block_number, prev_curl)

        nonce = 0
        target = "0" * self.difficulty
        block_hash = self._calculate_hash(
            block_number, timestamp, block_type, data, previous_hash, nonce, maxwell_signature)
        while not block_hash.startswith(target):
            nonce += 1
            block_hash = self._calculate_hash(
                block_number, timestamp, block_type, data, previous_hash, nonce, maxwell_signature)

        block = {
            "block_number": block_number,
            "timestamp": timestamp,
            "block_type": block_type,
            "data": data,
            "previous_hash": previous_hash,
            "nonce": nonce,
            "hash": block_hash,
            "miner_id": self.miner_id,
        }
        if maxwell_signature is not None:
            block["maxwell_signature"] = maxwell_signature

        self.chain.append(block)
        self._save()

        if self.audit is not None:
            self.audit.log(
                module="maxwell_chain_agent", action="mine_block", node_id=self.miner_id,
                details={"block_number": block_number, "block_type": block_type, "hash": block_hash},
            )

        return block

    def validate_chain(self) -> bool:
        """Recomputes EVERY block's hash (including block 0 -- a block that
        skips its own self-check would let its data be tampered with
        undetected, since previous_hash linkage alone doesn't catch a
        mutation that doesn't touch the stored hash field) and checks
        chain linkage from block 1 onward."""
        for i, curr in enumerate(self.chain):
            recalculated = self._calculate_hash(
                curr["block_number"], curr["timestamp"], curr["block_type"], curr["data"],
                curr["previous_hash"], curr["nonce"], curr.get("maxwell_signature"),
            )
            if recalculated != curr["hash"]:
                return False
            if i > 0 and curr["previous_hash"] != self.chain[i - 1]["hash"]:
                return False
        return True


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        agent = MaxwellChainAgent(
            canonical_chain=[], miner_id="smoke-test-miner", difficulty=3,
            store_path=os.path.join(tmp, "chain.json"),
        )

        print(f"Signature engine loaded from 'maxwell blockchain.py': {_compute_maxwell_signature is not None}")

        b0 = agent.mine_block("research_event", {"topic": "smoke test"})
        print(f"Block 0 mined: hash={b0['hash'][:16]}... nonce={b0['nonce']} "
              f"(leading zeros match difficulty={agent.difficulty}: {b0['hash'].startswith('0' * agent.difficulty)})")

        b1 = agent.mine_block("field_sample", {"source": "smoke test"}, with_maxwell_signature=True)
        print(f"Block 1 mined WITH signature: has maxwell_signature key = {'maxwell_signature' in b1}")

        print(f"Chain length: {len(agent.chain)}")
        print(f"Chain validates intact: {agent.validate_chain()}")

        original_data = agent.chain[0]["data"]
        agent.chain[0]["data"] = {"tampered": True}
        print(f"Chain validates after tampering block 0: {agent.validate_chain()} (expected False)")
        agent.chain[0]["data"] = original_data
