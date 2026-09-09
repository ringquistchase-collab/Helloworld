"""
growing_research_agent.py
===========================
A background agent that starts from a (condition, biomarker) topic,
pulls real results from public biomedical APIs, and grows its own
queue of related topics to explore over time.

Sources hit per topic:
    - ClinicalTrials.gov API v2   (studies matching condition/biomarker)
    - PubMed (NCBI E-utilities)   (papers matching condition/biomarker)
    - ClinVar (NCBI E-utilities)  (variant records; also yields candidate
                                    genes -> queued as new topics)
    - HGNC REST                   (one call per candidate gene, to
                                    normalize/validate the symbol before
                                    queueing it)
    - St. Jude Cloud              (best-effort stub -- see _fetch_stjude)

State (topics + queue) is persisted to store_path as JSON after every
explore step, so killing and restarting the process picks up where it
left off.

Rate limiting: NCBI asks for no more than ~3 requests/second without a
free API key. All NCBI calls (PubMed + ClinVar) share one rate limiter
enforcing a minimum spacing between requests; ClinicalTrials.gov and
HGNC get their own, gentler limiters.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("growing_research_agent")


# ---------------------------------------------------------------------------
# small async HTTP helper (stdlib only -- no httpx/aiohttp installed)
# ---------------------------------------------------------------------------

_USER_AGENT = "GrowingResearchAgent/1.0 (research script; contact: local user)"


def _http_get_sync(url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 20) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


async def _http_get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 20) -> dict:
    raw = await asyncio.to_thread(_http_get_sync, url, params, headers, timeout)
    return json.loads(raw)


class _RateLimiter:
    """Serializes calls so consecutive requests are at least min_interval apart."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last = time.monotonic()


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------


class GrowingResearchAgent:
    def __init__(self, node, dna, store_path: str = "research_store.json", interval_s: int = 3600, max_topics: int = 50):
        self.node = node
        self.dna = dna
        self.store_path = store_path
        self.interval_s = interval_s
        self.max_topics = max_topics

        self.topics: dict[str, dict] = {}
        self.queue: list[tuple[str, str | None]] = []

        self._task: asyncio.Task | None = None
        self._running = False

        # NCBI (PubMed + ClinVar share one budget): <=3 req/s without an API key.
        self._ncbi_limiter = _RateLimiter(0.35)
        self._ct_limiter = _RateLimiter(0.4)
        self._hgnc_limiter = _RateLimiter(0.4)
        self._stjude_limiter = _RateLimiter(0.4)

        self._load_store()

        if hasattr(node, "on_topic"):
            node.on_topic(self._on_peer_topic)

    # -- lifecycle ----------------------------------------------------------

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._save_store()

    # -- public API -----------------------------------------------------------

    async def seed_topic(self, condition: str, biomarker: str | None = None) -> str:
        """Explore a topic right now (blocking until the HTTP calls finish) and store it."""
        key = self._topic_key(condition, biomarker)
        await self._explore(condition, biomarker)
        return key

    # -- background loop ------------------------------------------------------

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise

            try:
                if self.queue:
                    condition, biomarker = self.queue.pop(0)
                    await self._explore(condition, biomarker)
                elif self.topics:
                    oldest_key = min(self.topics, key=lambda k: self.topics[k]["last_checked"])
                    t = self.topics[oldest_key]
                    await self._explore(t["condition"], t["biomarker"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Explore step failed; will retry next interval")

    # -- exploring a topic ------------------------------------------------------

    async def _explore(self, condition: str, biomarker: str | None):
        key = self._topic_key(condition, biomarker)
        existing = self.topics.get(key)
        prev_ids = {src: set(ids) for src, ids in (existing or {}).get("all_ids", {}).items()}

        ct_ids, related_conditions = await self._fetch_clinicaltrials(condition, biomarker)
        pubmed_ids = await self._fetch_pubmed(condition, biomarker)
        clinvar_ids, candidate_genes = await self._fetch_clinvar(condition, biomarker)
        stjude_ids = await self._fetch_stjude(condition, biomarker)

        all_ids = {
            "clinicaltrials": ct_ids,
            "pubmed": pubmed_ids,
            "clinvar": clinvar_ids,
            "stjude": stjude_ids,
        }

        new_ids: list[str] = []
        for src, ids in all_ids.items():
            new_ids.extend(sorted(set(ids) - prev_ids.get(src, set())))

        now = time.time()
        self.topics[key] = {
            "condition": condition,
            "biomarker": biomarker,
            "created_at": (existing or {}).get("created_at", now),
            "last_checked": now,
            "all_ids": all_ids,
            "new_ids_last_run": new_ids,
        }

        queued = await self._queue_related_topics(condition, biomarker, related_conditions, candidate_genes)
        self.topics[key]["related_topics_found"] = queued

        self._save_store()

        if hasattr(self.node, "broadcast_topic"):
            try:
                await self.node.broadcast_topic(self.topics[key])
            except Exception:
                logger.warning("broadcast_topic failed (no peers connected?)", exc_info=True)

        logger.info(
            "Explored '%s' (biomarker=%s): %d new result(s), %d topic(s) queued",
            condition, biomarker, len(new_ids), len(queued),
        )

    async def _queue_related_topics(self, condition, biomarker, related_conditions, candidate_genes) -> list[str]:
        queued: list[str] = []
        known = {self._topic_key(c, b) for c, b in self.queue}
        known.update(self.topics.keys())

        def room_left() -> bool:
            return (len(self.topics) + len(self.queue)) < self.max_topics

        # same condition, newly-seen candidate genes as biomarkers -- each one
        # costs exactly one HGNC lookup to validate before it's queued.
        for gene in candidate_genes:
            if not room_left():
                break
            normalized = await self._fetch_hgnc(gene)
            if not normalized:
                continue
            k = self._topic_key(condition, normalized)
            if k in known:
                continue
            self.queue.append((condition, normalized))
            known.add(k)
            queued.append(k)

        # same biomarker, related conditions pulled from ClinicalTrials.gov
        for rc in related_conditions:
            if not room_left():
                break
            k = self._topic_key(rc, biomarker)
            if k in known:
                continue
            self.queue.append((rc, biomarker))
            known.add(k)
            queued.append(k)

        return queued

    def _on_peer_topic(self, topic: dict):
        """Merge a topic gossiped in by a peer node -- doesn't re-fetch, just records it."""
        condition = topic.get("condition")
        if not condition:
            return
        key = self._topic_key(condition, topic.get("biomarker"))
        if key not in self.topics:
            self.topics[key] = topic
            self._save_store()

    # -- source fetchers ------------------------------------------------------

    async def _fetch_clinicaltrials(self, condition: str, biomarker: str | None) -> tuple[list[str], list[str]]:
        await self._ct_limiter.wait()
        params = {"query.cond": condition, "pageSize": 20, "fields": "NCTId,Condition,BriefTitle"}
        if biomarker:
            params["query.term"] = biomarker
        try:
            data = await _http_get_json("https://clinicaltrials.gov/api/v2/studies", params=params)
        except Exception as e:
            logger.warning("ClinicalTrials.gov fetch failed: %s", e)
            return [], []

        ids: list[str] = []
        related_conditions: set[str] = set()
        for study in data.get("studies", []):
            proto = study.get("protocolSection", {})
            nct = proto.get("identificationModule", {}).get("nctId")
            if nct:
                ids.append(nct)
            for c in proto.get("conditionsModule", {}).get("conditions", []) or []:
                if c and c.strip().lower() != condition.strip().lower():
                    related_conditions.add(c.strip())
        return ids, list(related_conditions)[:5]

    async def _fetch_pubmed(self, condition: str, biomarker: str | None) -> list[str]:
        await self._ncbi_limiter.wait()
        term = f"{condition} AND {biomarker}" if biomarker else condition
        params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": 20}
        try:
            data = await _http_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
        except Exception as e:
            logger.warning("PubMed fetch failed: %s", e)
            return []
        ids = data.get("esearchresult", {}).get("idlist", [])
        return [f"PMID:{i}" for i in ids]

    async def _fetch_clinvar(self, condition: str, biomarker: str | None) -> tuple[list[str], list[str]]:
        await self._ncbi_limiter.wait()
        term = f"{biomarker}[gene] AND {condition}" if biomarker else condition
        params = {"db": "clinvar", "term": term, "retmode": "json", "retmax": 20}
        try:
            data = await _http_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
            uids = data.get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            logger.warning("ClinVar esearch failed: %s", e)
            return [], []

        if not uids:
            return [], []

        await self._ncbi_limiter.wait()
        try:
            params2 = {"db": "clinvar", "id": ",".join(uids), "retmode": "json"}
            summary = await _http_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", params=params2)
        except Exception as e:
            logger.warning("ClinVar esummary failed: %s", e)
            return [f"ClinVar:{u}" for u in uids], []

        genes: set[str] = set()
        result = summary.get("result", {})
        for uid in uids:
            rec = result.get(uid, {})
            for g in rec.get("genes", []) or []:
                sym = g.get("symbol")
                if sym and sym.upper() != (biomarker or "").upper():
                    genes.add(sym.upper())

        return [f"ClinVar:{u}" for u in uids], list(genes)[:5]

    async def _fetch_stjude(self, condition: str, biomarker: str | None) -> list[str]:
        """Best-effort St. Jude Cloud lookup.

        Unlike ClinicalTrials.gov/NCBI/HGNC, St. Jude Cloud doesn't publish a
        stable, documented, unauthenticated REST search endpoint -- so rather
        than guess a URL, this is left as a deliberate no-op that logs once
        and returns no results. If you have St. Jude Cloud API access, fill
        in the real endpoint/auth here; the rest of the agent (rate limiter,
        id tracking, persistence) already treats this source like the others.
        """
        await self._stjude_limiter.wait()
        logger.info("St. Jude Cloud source not configured (no public unauthenticated API) -- skipping")
        return []

    async def _fetch_hgnc(self, symbol: str) -> str | None:
        await self._hgnc_limiter.wait()
        try:
            data = await _http_get_json(
                f"https://rest.genenames.org/fetch/symbol/{urllib.parse.quote(symbol)}",
                headers={"Accept": "application/json"},
            )
        except Exception as e:
            logger.warning("HGNC fetch failed for %s: %s", symbol, e)
            return None
        docs = data.get("response", {}).get("docs", [])
        if not docs:
            return None
        return docs[0].get("symbol", symbol)

    # -- persistence ------------------------------------------------------------

    @staticmethod
    def _topic_key(condition: str, biomarker: str | None) -> str:
        return f"{condition.strip().lower()}|{(biomarker or '').strip().lower()}"

    def _load_store(self):
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not load store %s (%s) -- starting fresh", self.store_path, e)
            return
        self.topics = data.get("topics", {})
        self.queue = [tuple(item) for item in data.get("queue", [])]

    def _save_store(self):
        data = {
            "topics": self.topics,
            "queue": [list(item) for item in self.queue],
        }
        tmp_path = f"{self.store_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.store_path)
