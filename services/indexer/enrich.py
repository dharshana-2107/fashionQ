"""Enrichment logic, shared by the bulk script (Phase 2) and the indexer worker (Phase 5).

    inputs  = build_inputs(session, asins)     # products + reviews from Postgres
    results, failed = enrich_batch(inputs)     # one LLM call for the whole batch
    save_enrichments(session, results, inputs)
"""
import hashlib
import json
from collections import defaultdict

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from common.config import settings
from common.db import Product, ProductEnrichment, ReviewSample
from common.llm import complete_json
from common.schemas import ProductAttributes
from services.indexer.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_message

# Trim inputs so a batch of 10 products stays a reasonable prompt size.
MAX_FEATURES, FEATURE_LEN = 8, 200
DESCRIPTION_LEN = 600
REVIEWS_PER_PRODUCT, REVIEW_LEN = 5, 300


def build_inputs(session: Session, asins: list[str]) -> list[dict]:
    """Compact LLM input per product: listing text plus its most helpful reviews."""
    products = session.scalars(select(Product).where(Product.parent_asin.in_(asins))).all()
    samples = session.scalars(
        select(ReviewSample).where(ReviewSample.parent_asin.in_(asins))
        .order_by(ReviewSample.parent_asin, ReviewSample.helpful_vote.desc())
    ).all()
    reviews = defaultdict(list)
    for r in samples:
        if len(reviews[r.parent_asin]) < REVIEWS_PER_PRODUCT:
            reviews[r.parent_asin].append({"rating": int(r.rating), "text": r.text[:REVIEW_LEN]})

    inputs = []
    for p in products:
        inputs.append({
            "id": p.parent_asin,
            "title": p.title,
            "store": p.store,
            "department": (p.details or {}).get("Department"),
            "price": p.price,
            "features": [f[:FEATURE_LEN] for f in (p.features or [])[:MAX_FEATURES]],
            "description": " ".join(p.description or [])[:DESCRIPTION_LEN],
            "reviews": reviews.get(p.parent_asin, []),
        })
    return inputs


def input_hash(inp: dict) -> str:
    """Fingerprint of what the LLM saw; if it changes later, the product needs re-enriching."""
    return hashlib.sha256(json.dumps(inp, sort_keys=True, default=str).encode()).hexdigest()


def enrich_batch(inputs: list[dict], max_tokens: int = 12000):
    """Returns ({asin: ProductAttributes}, [asins that came back missing or invalid])."""
    data = complete_json(SYSTEM_PROMPT, build_user_message(inputs), max_tokens=max_tokens)
    items = data.get("products", []) if isinstance(data, dict) else data
    expected = {i["id"] for i in inputs}

    results: dict[str, ProductAttributes] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        asin = str(item.get("id", "")).strip()
        if asin in expected and asin not in results:
            try:
                results[asin] = ProductAttributes.model_validate(item)
            except ValidationError:
                pass
    failed = [a for a in expected if a not in results]
    return results, failed


def save_enrichments(session: Session, results: dict[str, ProductAttributes], inputs: list[dict]):
    """Upsert results; commit is left to the caller."""
    if not results:
        return
    by_id = {i["id"]: i for i in inputs}
    rows = [{
        "parent_asin": asin,
        "attributes": attrs.model_dump(),
        "category": attrs.category,
        "gender": attrs.gender,
        "review_summary": attrs.review_summary or None,
        "input_hash": input_hash(by_id[asin]),
        "prompt_version": PROMPT_VERSION,
        "model": settings.llm_model,
    } for asin, attrs in results.items()]
    stmt = insert(ProductEnrichment.__table__).values(rows)
    update = {c: stmt.excluded[c] for c in rows[0] if c != "parent_asin"}
    update["enriched_at"] = func.now()
    session.execute(stmt.on_conflict_do_update(index_elements=["parent_asin"], set_=update))
