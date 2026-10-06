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
CANDIDATES = 24   # per slot, before reranking
MIN_RESULTS = 4   # relax filters below this

# Formal queries: casual items sink, formal items rise (formality comes from title rules).
FORMAL_BONUS, CASUAL_PENALTY = 0.10, 0.25
OWN_CATEGORY_BONUS = 0.06  # shirts first in a "top" slot, then blazers/suits
NEW_BOOST = 0.03  # new arrivals win ties against look-alike older listings
NO_PRICE_PENALTY = 0.05  # price was asked for, but this item has no listed price
_FORMAL_QUERY = re.compile(r"\b(formal|office|business|meeting|interview|wedding|suit|blazer|tuxedo|gala|"
                           r"conference|ceremony|professional|corporate)\b", re.I)
_KIDS_QUERY = re.compile(r"\b(kids?|boys?|girls?|baby|babies|toddlers?|children|child|infants?|youth)\b", re.I)
_FORMAL_OCCASIONS = {"formal", "work_office", "office", "work", "wedding", "business"}

# A slot's category also searches closely related categories. E.g. the LLM files
# a blazer under "tops", but our rules put blazers in "suits_formalwear".
RELATED_CATEGORIES = {
    "tops": ["tops", "outerwear", "suits_formalwear"],
    "bottoms": ["bottoms"],  # suit sets crowded out real trousers
    "outerwear": ["outerwear", "suits_formalwear", "tops"],
    "suits_formalwear": ["suits_formalwear", "outerwear", "tops", "bottoms"],
    "activewear": ["activewear", "tops", "bottoms"],
    "dresses": ["dresses"],
}


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


def is_formal_query(pq: ParsedQuery | None, raw_query: str) -> bool:
    if pq is not None:
        if _FORMAL_OCCASIONS & set(pq.occasions):
            return True
        text = " ".join([pq.english] + [s.query for s in pq.slots])
    else:
        text = raw_query
    return bool(_FORMAL_QUERY.search(text or ""))


def _style_adjust(p: dict, formal_query: bool) -> float:
    if not formal_query:
        return 0.0
    f = p.get("formality")
    return FORMAL_BONUS if f == "formal" else (-CASUAL_PENALTY if f == "casual" else 0.0)


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


def _retrieve(client, emb, slot: Slot, pq: ParsedQuery | None, raw_query: str) -> tuple[list, list[str]]:
    """Hybrid search with hard filters, relaxing category then gender if needed."""
    text = raw_query + " " + (pq.english if pq else "") + " " + slot.query
    adults_only = not _KIDS_QUERY.search(text)  # kids' items only when the query asks for them
    price = dict(min_price=pq.min_price, max_price=pq.max_price) if pq else {}
    category = RELATED_CATEGORIES.get(slot.category, [slot.category]) if slot.category else None
    gender = pq.gender if pq else None
    relaxed: list[str] = []
    found: list = []          # strict results first, looser ones appended
    seen: set = set()
    while True:
        flt = vs.build_filter(category=category, gender=gender, adults_only=adults_only, **price)
        for h in vs.search(client, emb, mode="hybrid", flt=flt, limit=CANDIDATES):
            if h.id not in seen:
                seen.add(h.id)
                found.append(h)
        if len(found) >= MIN_RESULTS:
            return found[:CANDIDATES], relaxed
        if category:
            relaxed.append(f"category '{slot.category}'")
            category = None
        elif gender:
            relaxed.append(f"gender '{gender}'")
            gender = None
        else:
            return found, relaxed


def _card(p: dict, score: float, rerank: float | None, slot_name: str) -> dict:
    keep = ("parent_asin", "title", "store", "image_url", "price", "category", "gender", "formality",
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
    per_slot = [_retrieve(client, e, s, pq, query) for s, e in zip(slots, embs)]
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
    formal_query = is_formal_query(pq, query)
    price_asked = bool(pq and (pq.min_price or pq.max_price))
    seen_asins: set[str] = set()
    out_slots = []
    for slot, (hits, relaxed), rr in zip(slots, per_slot, rerank_scores):
        max_hybrid = max((h.score for h in hits), default=1.0) or 1.0
        scored = []
        for h, r in zip(hits, rr):
            p = h.payload or {}
            relevance = r if r is not None else h.score / max_hybrid
            final = (W_RELEVANCE * relevance + W_RATING * _rating_norm(p) + _tag_boost(p, pq)
                     + _style_adjust(p, formal_query)
                     + (OWN_CATEGORY_BONUS if slot.category and p.get("category") == slot.category else 0.0)
                     + (NEW_BOOST if p.get("is_new") else 0.0)
                     - (NO_PRICE_PENALTY if price_asked and p.get("price") is None else 0.0))
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
        "formal_query": formal_query,
        "slots": out_slots,
        "timings": timings,
    }
