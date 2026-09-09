"""
audit_trail.py
================
A shared, tamper-evident, chronologically-ordered log that multiple
modules in this project write to (network_os.py, maxwell_chain_agent.py,
and now token_ledger.py / ai_gossip.py / integrated_research_agent.py /
crispr_research_suite.py all accept an optional `audit=` and guard every
call with `if self.audit is not None`, so this file being absent never
broke anything -- it just meant no shared record existed yet).

WHY IT'S TAMPER-EVIDENT
----------------------------
Same hash-chain pattern used everywhere else in this project
(digital_dna.py's live-signal strand, maxwell_chain_agent.py's blocks):
each entry's hash covers its own content AND the previous entry's
hash. Change or delete any entry, or reorder them, and every hash from
that point forward stops matching -- verify_chain() catches it.

Persisted as JSON Lines (one entry per line) so multiple modules can
append to the same file over one run without re-writing the whole
file each time.

Usage
-----
    from audit_trail import AuditTrail

    audit = AuditTrail("system_audit.jsonl")
    audit.log(module="network_os", action="handshake_accepted",
              node_id="abc123", details={"peer_node_id": "def456"})

    entries = audit.read_all()
    ok, bad_entries = audit.verify_chain()
"""

from __future__ import annotations
import hashlib
import json
import os
import time


class AuditTrail:
    def __init__(self, path: str):
        self.path = path
        self._last_hash = self._find_last_hash()

    def _find_last_hash(self) -> str:
        if not os.path.exists(self.path):
            return "0" * 64
        last = "0" * 64
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    last = entry["hash"]
                except (json.JSONDecodeError, KeyError):
                    continue
        return last

    @staticmethod
    def _compute_hash(timestamp, module, action, node_id, details, prev_hash) -> str:
        content = {
            "timestamp": timestamp, "module": module, "action": action,
            "node_id": node_id, "details": details, "prev_hash": prev_hash,
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()

    def log(self, module: str, action: str, node_id: str, details: dict | None = None) -> dict:
        """Appends one entry, chained to whatever the last entry's hash was
        (across the whole file, not just this process's lifetime -- a
        fresh AuditTrail() on the same path picks up the chain where the
        last one left off)."""
        timestamp = time.time()
        details = details or {}
        entry_hash = self._compute_hash(timestamp, module, action, node_id, details, self._last_hash)
        entry = {
            "timestamp": timestamp, "module": module, "action": action,
            "node_id": node_id, "details": details,
            "prev_hash": self._last_hash, "hash": entry_hash,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        self._last_hash = entry_hash
        return entry

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def verify_chain(self) -> tuple[bool, list[dict]]:
        """Recomputes every entry's hash from its own content + the
        previous entry's hash, and checks the chain links. Returns
        (all_valid, [entries that failed])."""
        entries = self.read_all()
        bad: list[dict] = []
        prev_hash = "0" * 64
        for entry in entries:
            recalculated = self._compute_hash(
                entry["timestamp"], entry["module"], entry["action"],
                entry["node_id"], entry["details"], prev_hash,
            )
            if entry.get("prev_hash") != prev_hash or entry.get("hash") != recalculated:
                bad.append(entry)
            prev_hash = entry.get("hash", recalculated)
        return (len(bad) == 0, bad)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "audit.jsonl")
        audit = AuditTrail(path)

        audit.log(module="network_os", action="handshake_accepted", node_id="node-a",
                   details={"peer_node_id": "node-b"})
        audit.log(module="maxwell_chain_agent", action="mine_block", node_id="node-a",
                   details={"block_number": 0})

        entries = audit.read_all()
        print(f"Entries: {len(entries)}")
        for e in entries:
            print(f"  [{e['module']}] {e['action']}")

        ok, bad = audit.verify_chain()
        print(f"Chain verifies intact: {ok}")

        # reopen against the same file -- chain should continue, not reset
        audit2 = AuditTrail(path)
        audit2.log(module="ai_gossip", action="analysis", node_id="node-a", details={})
        ok2, bad2 = AuditTrail(path).verify_chain()
        print(f"Chain still verifies after reopening + appending: {ok2}")

        print("\n=== Proving tamper detection ===")
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tampered = json.loads(lines[0])
        tampered["details"] = {"peer_node_id": "TAMPERED"}
        lines[0] = json.dumps(tampered) + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        ok3, bad3 = AuditTrail(path).verify_chain()
        print(f"Chain verifies after tampering entry 0: {ok3} (expected False, {len(bad3)} bad entries found)")
