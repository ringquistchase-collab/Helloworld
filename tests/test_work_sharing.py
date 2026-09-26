"""Work sharing between nodes: deterministic assignment, exactly-once work
when everyone is up, takeover when the first-in-line node is down, and
chain audits that catch tampering."""
import asyncio
import hashlib
import time

from digital_dna import DigitalDNA
from dna_binary_codec import encode_to_dna
from network_node import NetworkNode
from token_ledger import TokenLedger
from work_sharing import WorkManager, WorkSchedule, audit_chain, order_for

IDENTITY = encode_to_dna(hashlib.sha256(b"work-sharing-test").digest())
BASE = 19700


class FakeClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_order_is_deterministic_and_spreads_first_place():
    ids = [0, 1, 2]
    assert order_for("research:5", ids) == order_for("research:5", [2, 0, 1])
    firsts = {order_for(f"research:{r}", ids)[0] for r in range(30)}
    assert firsts == {0, 1, 2}


def test_schedule_and_audit_never_targets_the_auditor():
    sched = WorkSchedule(round_seconds=60, research_every=3, external_every=2)
    kinds = {t.kind for t in sched.tasks_for_round(6, [0, 1, 2])}
    assert kinds == {"research", "external_info", "audit"}
    assert {t.kind for t in sched.tasks_for_round(7, [0, 1, 2])} == {"audit"}
    for r in range(9):
        audit = [t for t in sched.tasks_for_round(r, [0, 1, 2]) if t.kind == "audit"][0]
        assert audit.target not in sched.candidates(audit, [0, 1, 2])


def _network(tmp_path, n, clock, ledger, runners=None, sched=None, base=BASE):
    sched = sched or WorkSchedule(round_seconds=100, research_every=1, external_every=1, audits=False)
    ports = [base + i for i in range(n)]
    nodes = []
    for i, p in enumerate(ports):
        d = tmp_path / f"n{i}"
        d.mkdir()
        dna = DigitalDNA(seed_label=f"ws-{i}", dna_path=str(d / "dna.json"))
        nodes.append(NetworkNode(i, p, [q for q in ports if q != p], dna, IDENTITY, ledger, str(d),
                                 require_known_peers=True))
    for a in nodes:
        for b in nodes:
            if a is not b:
                a.trust_peer(b.node_id, b.signing_pub_hex)
    managers = [WorkManager(nd, sched, takeover_seconds=10, clock=clock, runners=runners).attach()
                for nd in nodes]
    return nodes, managers


async def _step(managers, alive):
    """Every alive node runs whatever is due for it right now."""
    jobs = [m.run_task(task, pos) for m in managers if m.node.node_id in alive
            for task, pos in m.due(m.clock())]
    await asyncio.gather(*jobs)
    await asyncio.sleep(0.3)   # let gossip land


def _runners(calls):
    def make(kind):
        def run():
            calls.append(kind)
            return {"fake": kind}
        return run
    return {"research": make("research"), "external_info": make("external_info")}


async def _close(nodes):
    for nd in nodes:
        if nd.server:
            nd.server.close()
            await nd.server.wait_closed()


async def test_each_job_done_exactly_once_when_all_nodes_are_up(tmp_path):
    clock, calls = FakeClock(1000.0), []
    ledger = TokenLedger(store_path=str(tmp_path / "ledger.json"))
    nodes, managers = _network(tmp_path, 3, clock, ledger, _runners(calls))
    for nd in nodes:
        await nd.start_server()
    try:
        for offset in (0, 10, 20, 30):          # round start, then each takeover slot
            clock.t = 1000.0 + offset
            await _step(managers, alive={0, 1, 2})
        assert sorted(calls) == ["external_info", "research"]
        for m in managers:                      # every node knows both jobs are done
            assert {"research:10", "external_info:10"} <= set(m.done)
        assert sum(m.stats["takeovers"] for m in managers) == 0
    finally:
        await _close(nodes)


async def test_next_node_takes_over_when_first_in_line_is_down(tmp_path):
    clock, calls = FakeClock(1000.0), []
    ledger = TokenLedger(store_path=str(tmp_path / "ledger.json"))
    nodes, managers = _network(tmp_path, 3, clock, ledger, _runners(calls), base=BASE + 10)
    line = order_for("research:10", [0, 1, 2])
    first, second = line[0], line[1]
    alive = {0, 1, 2} - {first}
    for nd in nodes:
        if nd.node_id in alive:
            await nd.start_server()
    try:
        clock.t = 1000.0
        await _step(managers, alive)
        assert "research:10" not in managers[second].done    # nobody covered it yet
        clock.t = 1000.0 + 10                   # second in line's turn
        await _step(managers, alive)
        assert managers[second].stats["takeovers"] >= 1
        assert calls.count("research") == 1
        for i in alive:
            assert managers[i].done["research:10"]["by"] == second
    finally:
        await _close(nodes)


async def test_audit_passes_then_catches_tampering(tmp_path):
    clock = FakeClock(1000.0)
    ledger = TokenLedger(store_path=str(tmp_path / "ledger.json"))
    nodes, _ = _network(tmp_path, 2, clock, ledger, base=BASE + 20)
    for nd in nodes:
        await nd.start_server()
    try:
        for _ in range(3):
            await nodes[1].mine_and_gossip()
        await nodes[0].mine_and_gossip()        # handshakes node-0 -> node-1
        addr = nodes[0].address_of(1)
        key = nodes[0].peer_signing_keys[1]
        blocks = await nodes[0].fetch_peer_chain(addr)
        assert len(blocks) == 3
        result = audit_chain(blocks, 1, key, IDENTITY)
        assert result["ok"], result

        # naive tampering: payload edited, hashes left alone
        nodes[1].chain.blocks[1].payload["index"] = 999
        result = audit_chain(await nodes[0].fetch_peer_chain(addr), 1, key, IDENTITY)
        assert not result["ok"] and any("hash" in p for p in result["problems"])

        # careful tampering: hashes and links recomputed, so only the
        # signature can give it away
        chain = nodes[1].chain.blocks
        prev = chain[0].block_hash
        for b in chain[1:]:
            b.previous_hash = prev
            b.block_hash = b.compute_hash()
            prev = b.block_hash
        result = audit_chain(await nodes[0].fetch_peer_chain(addr), 1, key, IDENTITY)
        assert not result["ok"] and any("signature" in p for p in result["problems"])
    finally:
        await _close(nodes)


async def test_stranger_cannot_fetch_a_chain(tmp_path):
    clock = FakeClock(1000.0)
    ledger = TokenLedger(store_path=str(tmp_path / "ledger.json"))
    nodes, _ = _network(tmp_path, 2, clock, ledger, base=BASE + 30)
    (tmp_path / "s").mkdir()
    stranger = NetworkNode(7, BASE + 39, [nodes[1].port],
                           DigitalDNA(seed_label="s", dna_path=str(tmp_path / "s" / "dna.json")),
                           IDENTITY, ledger, str(tmp_path / "s"))
    await nodes[1].start_server()
    try:
        await nodes[1].mine_and_gossip()
        assert await stranger.fetch_peer_chain(("127.0.0.1", nodes[1].port)) is None
        assert nodes[1].stats["chain_requests_served"] == 0
    finally:
        await _close(nodes)


async def test_audit_work_runs_through_the_real_loop(tmp_path):
    """End to end with the real 1-second loop and a short round."""
    sched = WorkSchedule(round_seconds=3, research_every=0, external_every=0, audits=True)
    ledger = TokenLedger(store_path=str(tmp_path / "ledger.json"))
    nodes, managers = _network(tmp_path, 3, time.time, ledger, sched=sched, base=BASE + 40)
    for m in managers:
        m.takeover_seconds = 1
    for nd in nodes:
        nd.mine_interval = 0.5
    stop = asyncio.Event()
    tasks = [asyncio.create_task(nd.run(stop)) for nd in nodes]
    await asyncio.sleep(7)
    stop.set()
    await asyncio.gather(*tasks)
    audits = [a for m in managers for a in m.recent_audits]
    assert audits and all(a["ok"] for a in audits), audits
    done = sum(m.stats["work_done:audit"] for m in managers)
    audit_ids = {t for m in managers for t in m.done if t.startswith("audit:")}
    assert done >= 1 and done == len(audit_ids)   # each audit job done exactly once
