"""shared_per_round: every node asking for the same round's enrichment
gets one shared fetch, not one outside request per node."""
import asyncio

from run_consolidated_network import shared_per_round


async def test_one_fetch_per_round_shared_by_all_nodes():
    calls = []

    @shared_per_round
    async def enricher(counter):
        calls.append(counter)
        await asyncio.sleep(0.05)
        return {"round": counter}

    results = await asyncio.gather(*(enricher(4) for _ in range(3)))
    assert results == [{"round": 4}] * 3
    assert calls == [4]

    assert await enricher(8) == {"round": 8}
    assert calls == [4, 8]
