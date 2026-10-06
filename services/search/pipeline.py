"""The full search pipeline.

  query (any language)
    1. parse      LLM -> slots + filters               (fallback: raw query, no filters)
    2. retrieve   per slot: BGE-M3 dense+sparse -> Qdrant hybrid (RRF), hard filters
                  (price, gender, category); filters are relaxed if too few results
    3. rerank     bge-reranker-v2-m3 on the candidates (optional)
    4. blend      final = 0.80*relevance + 0.15*rating + small boosts for matching
                  occasion/season/color tags; near-duplicate titles removed
"""
from __future__ import annotations

import os

os.environ.setdefault("TQDM_DISABLE", "1")  # silence FlagEmbedding progress bars

import logging
import re
import time
from typing import Any

from common import vectorstore as vs
from common.embedder import QUERY_MAX_TOKENS, get_embedder
from services.search.parser import ParsedQuery, Slot, parse_query

log = logging.getLogger(__name__)

W_RELEVANCE, W_RATING = 0.80, 0.15
TAG_BOOST, MAX_TAG_BOOST = 0.04, 0.12
CANDIDATES = 30   # per slot, before reranking
MIN_RESULTS = 4   # relax filters below this


def warmup(rerank: bool = True) -> None:
    """Load models once (~20-40 s) so the first real query is fast."""
    get_embedder().encode(["warmup"], batch_size=1, max_length=16)
    if rerank:
        from services.search.reranker import get_reranker
        get_reranker().score([("warmup", "warmup")])


def _rating_norm(p: dict) -> float:
    r = p.get("bayes_rating") or p.get("avg_rating") or 3.5
    return max(0.0, min(1.0, (float(r) - 1.0) / 4.0))


def _tag_boost(p: dict, pq: ParsedQuery | None) -> float:
    if pq is None:
        return 0.0
    b = 0.0
    for want, field in ((pq.occasions, "occasions"), (pq.seasons, "seasons"), (pq.colors, "colors")):
        if want and set(want) & set(p.get(field) or []):
            b += TAG_BOOST
    return min(b, MAX_TAG_BOOST)


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:50]


def _rerank_query(slot: Slot, pq: ParsedQuery | None) -> str:
    parts = [slot.query]
    if pq is not None:
        if pq.gender:
            parts.append(f"for {pq.gender}")
        if pq.occasions:
            parts.append("occasion: " + ", ".join(pq.occasions))
        if pq.seasons:
            parts.append("season: " + ", ".join(pq.seasons))
    return "; ".join(parts)


def _retrieve(client, emb, slot: Slot, pq: ParsedQuery | None) -> tuple[list, list[str]]:
    """Hybrid search with hard filters, relaxing category then gender if needed."""
    price = dict(min_price=pq.min_price, max_price=pq.max_price) if pq else {}
    category = slot.category
    gender = pq.gender if pq else None
    relaxed: list[str] = []
    while True:
        flt = vs.build_filter(category=category, gender=gender, **price)
        hits = vs.search(client, emb, mode="hybrid", flt=flt, limit=CANDIDATES)
        if len(hits) >= MIN_RESULTS:
            return hits, relaxed
        if category:
            relaxed.append(f"category '{category}'")
            category = None
        elif gender:
            relaxed.append(f"gender '{gender}'")
            gender = None
        else:
            return hits, relaxed


def _card(p: dict, score: float, rerank: float | None, slot_name: str) -> dict:
    keep = ("parent_asin", "title", "store", "image_url", "price", "category", "gender",
            "avg_rating", "review_count", "occasions", "seasons", "colors", "review_summary",
            "is_new", "indexed_at")
    card = {k: p.get(k) for k in keep if p.get(k) is not None}
    card["score"] = round(score, 4)
    if rerank is not None:
        card["rerank"] = round(rerank, 4)
    card["slot"] = slot_name
    return card


def search(query: str, k: int = 6, use_llm: bool = True, use_rerank: bool = True) -> dict[str, Any]:
    t_start = time.time()
    timings: dict[str, int] = {}
    query = (query or "").strip()
    if not query:
        return {"query": query, "parsed": None, "parse": {"source": "empty"}, "slots": [], "timings": {}}

    # 1. parse
    t0 = time.time()
    if use_llm:
        pq, parse_info = parse_query(query)
    else:
        pq, parse_info = None, {"source": "disabled"}
    slots = pq.slots if pq else [Slot(name="results", query=query)]
    timings["parse_ms"] = round((time.time() - t0) * 1000)

    # 2. embed all slot queries in one batch, then retrieve per slot
    t0 = time.time()
    embs = get_embedder().encode([s.query for s in slots], batch_size=len(slots),
                                 max_length=QUERY_MAX_TOKENS)
    timings["embed_ms"] = round((time.time() - t0) * 1000)

    t0 = time.time()
    client = vs.get_client()
    per_slot = [_retrieve(client, e, s, pq) for s, e in zip(slots, embs)]
    timings["search_ms"] = round((time.time() - t0) * 1000)

    # 3. rerank everything in one batch
    rerank_scores: list[list[float | None]] = [[None] * len(h) for h, _ in per_slot]
    if use_rerank:
        t0 = time.time()
        pairs, index = [], []
        for si, ((hits, _), slot) in enumerate(zip(per_slot, slots)):
            rq = _rerank_query(slot, pq)
            for hi, h in enumerate(hits):
                pairs.append((rq, (h.payload or {}).get("doc") or (h.payload or {}).get("title", "")))
                index.append((si, hi))
        try:
            from services.search.reranker import get_reranker
            scores = get_reranker().score(pairs)
            for (si, hi), sc in zip(index, scores):
                rerank_scores[si][hi] = sc
        except Exception as e:  # reranker trouble should never kill a search
            log.warning("rerank failed, using hybrid order: %s", e)
            use_rerank = False
            rerank_scores = [[None] * len(h) for h, _ in per_slot]
        timings["rerank_ms"] = round((time.time() - t0) * 1000)

    # 4. blend, dedupe, cut to k
    seen_asins: set[str] = set()
    out_slots = []
    for slot, (hits, relaxed), rr in zip(slots, per_slot, rerank_scores):
        max_hybrid = max((h.score for h in hits), default=1.0) or 1.0
        scored = []
        for h, r in zip(hits, rr):
            p = h.payload or {}
            relevance = r if r is not None else h.score / max_hybrid
            final = W_RELEVANCE * relevance + W_RATING * _rating_norm(p) + _tag_boost(p, pq)
            scored.append((final, r, p))
        scored.sort(key=lambda x: -x[0])
        results, seen_titles = [], set()
        for final, r, p in scored:
            asin, tkey = p.get("parent_asin"), _title_key(p.get("title", ""))
            if asin in seen_asins or tkey in seen_titles:
                continue
            seen_asins.add(asin)
            seen_titles.add(tkey)
            results.append(_card(p, final, r, slot.name))
            if len(results) >= k:
                break
        out_slots.append({"name": slot.name, "query": slot.query, "category": slot.category,
                          "relaxed": relaxed, "results": results})

    timings["total_ms"] = round((time.time() - t_start) * 1000)
    return {
        "query": query,
        "parsed": pq.model_dump() if pq else None,
        "parse": parse_info,
        "reranked": use_rerank,
        "slots": out_slots,
        "timings": timings,
    }
