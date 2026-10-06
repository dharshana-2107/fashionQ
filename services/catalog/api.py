"""Catalog microservice: writes products and reviews to Postgres.

Run from the project root:
  uvicorn services.catalog.api:app --port 8001

It never publishes events or touches Qdrant itself. Postgres triggers record every
change in the outbox (scripts/install_triggers.py), the relay publishes them to
Redis, and the indexer worker updates search. So products added by ANY route
(this API, the loader, Adminer, plain SQL) become searchable the same way.

  POST  /products                    add a new product (or replace an existing one)
  PATCH /products/{asin}             change some fields (price only -> no re-embedding)
  POST  /products/{asin}/reviews     add a review: updates rating + count (no re-embedding)
  POST  /products/{asin}/deactivate  take a product off the store
  GET   /products/{asin}             read one product
  GET   /recent                      latest events + what the worker did
  GET   /stats                       counts, outbox backlog, queue, trigger check
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from common import events as ev
from common.db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s catalog: %(message)s")

app = FastAPI(title="FashionQ catalog", version="0.6")

# Bayesian rating: the same idea as your loader. A product with few reviews is pulled
# toward the global mean. Set this to the --prior-weight your load_catalog uses.
BAYES_PRIOR_WEIGHT = 20.0
JSON_FIELDS = {"features", "description", "details"}
EDITABLE = {"title", "store", "price", "image_url", "features", "description", "details"}


class ProductIn(BaseModel):
    parent_asin: str | None = Field(None, min_length=3, max_length=20, description="omit to auto-generate")
    title: str = Field(..., min_length=3, max_length=500)
    store: str | None = None
    price: float | None = Field(None, ge=0)
    image_url: str | None = None
    features: list[str] = []
    description: list[str] = []
    details: dict[str, Any] = {}


class ProductPatch(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=500)
    store: str | None = None
    price: float | None = Field(None, ge=0)
    image_url: str | None = None
    features: list[str] | None = None
    description: list[str] | None = None
    details: dict[str, Any] | None = None


class ReviewIn(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    verified: bool = True


def _exists(conn, asin: str) -> bool:
    return conn.execute(text("SELECT 1 FROM products WHERE parent_asin = :a"), {"a": asin}).first() is not None


@app.get("/health")
def health():
    try:
        ev.get_redis().ping()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(503, f"dependency not reachable: {e}")
    return {"status": "ok"}


@app.post("/products")
def add_product(p: ProductIn):
    asin = p.parent_asin or "NEW" + secrets.token_hex(4).upper()
    with engine.begin() as conn:
        existed = _exists(conn, asin)
        conn.execute(text("""
            INSERT INTO products (parent_asin, title, store, features, description, details, price,
                                  image_url, is_active, created_at, updated_at)
            VALUES (:asin, :title, :store, CAST(:features AS JSONB), CAST(:description AS JSONB),
                    CAST(:details AS JSONB), :price, :image_url, TRUE, now(), now())
            ON CONFLICT (parent_asin) DO UPDATE SET
                title = excluded.title, store = excluded.store, features = excluded.features,
                description = excluded.description, details = excluded.details, price = excluded.price,
                image_url = excluded.image_url, is_active = TRUE, updated_at = now()
        """), {"asin": asin, "title": p.title, "store": p.store, "price": p.price, "image_url": p.image_url,
               "features": json.dumps(p.features), "description": json.dumps(p.description),
               "details": json.dumps(p.details)})
    return {"parent_asin": asin, "action": "replaced" if existed else "created"}


@app.patch("/products/{asin}")
def patch_product(asin: str, patch: ProductPatch):
    fields = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if k in EDITABLE}
    if not fields:
        raise HTTPException(400, "nothing to change")
    sets = ", ".join(f"{k} = CAST(:{k} AS JSONB)" if k in JSON_FIELDS else f"{k} = :{k}" for k in fields)
    params = {k: (json.dumps(v) if k in JSON_FIELDS else v) for k, v in fields.items()}
    with engine.begin() as conn:
        res = conn.execute(text(f"UPDATE products SET {sets}, updated_at = now() WHERE parent_asin = :a"),
                           {**params, "a": asin})
        if res.rowcount == 0:
            raise HTTPException(404, f"unknown product {asin}")
    return {"parent_asin": asin, "changed": sorted(fields)}


@app.post("/products/{asin}/reviews")
def add_review(asin: str, review: ReviewIn):
    """Incrementally updates review_stats: count, average, Bayesian rating, verified ratio."""
    with engine.begin() as conn:
        if not _exists(conn, asin):
            raise HTTPException(404, f"unknown product {asin}")
        m = conn.execute(text("SELECT COALESCE(sum(avg_rating * review_count) / NULLIF(sum(review_count), 0), 4.0) "
                              "FROM review_stats")).scalar()
        conn.execute(text("""
            INSERT INTO review_stats (parent_asin, review_count, avg_rating, bayes_rating, verified_ratio,
                                      first_review_at, last_review_at, rating_hist)
            VALUES (:a, 1, :r, (:c * :m + :r) / (:c + 1), :v, now(), now(), CAST(:hist AS JSONB))
            ON CONFLICT (parent_asin) DO UPDATE SET
                avg_rating     = (review_stats.avg_rating * review_stats.review_count + :r) / (review_stats.review_count + 1),
                bayes_rating   = (:c * :m + review_stats.avg_rating * review_stats.review_count + :r)
                                 / (:c + review_stats.review_count + 1),
                verified_ratio = (COALESCE(review_stats.verified_ratio, 0) * review_stats.review_count + :v)
                                 / (review_stats.review_count + 1),
                review_count   = review_stats.review_count + 1,
                last_review_at = now()
        """), {"a": asin, "r": float(review.rating), "v": 1.0 if review.verified else 0.0,
               "c": BAYES_PRIOR_WEIGHT, "m": float(m), "hist": json.dumps({str(review.rating): 1})})
        row = conn.execute(text("SELECT review_count, avg_rating, bayes_rating FROM review_stats "
                                "WHERE parent_asin = :a"), {"a": asin}).one()
    return {"parent_asin": asin, "review_count": row[0], "avg_rating": round(float(row[1]), 3),
            "bayes_rating": round(float(row[2]), 3)}


@app.post("/products/{asin}/deactivate")
def deactivate(asin: str):
    with engine.begin() as conn:
        res = conn.execute(text("UPDATE products SET is_active = FALSE, updated_at = now() "
                                "WHERE parent_asin = :a"), {"a": asin})
        if res.rowcount == 0:
            raise HTTPException(404, f"unknown product {asin}")
    return {"parent_asin": asin, "action": "deactivated"}


@app.get("/products/{asin}")
def get_product(asin: str):
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT p.parent_asin, p.title, p.price, p.image_url, p.is_active,
                   s.review_count, s.avg_rating, s.bayes_rating
            FROM products p LEFT JOIN review_stats s ON s.parent_asin = p.parent_asin
            WHERE p.parent_asin = :a"""), {"a": asin}).mappings().first()
    if row is None:
        raise HTTPException(404, f"unknown product {asin}")
    return {k: (float(v) if k in ("price", "avg_rating", "bayes_rating") and v is not None else v)
            for k, v in dict(row).items()}


@app.get("/recent")
def recent(n: int = Query(10, ge=1, le=50)):
    items = [json.loads(x) for x in ev.get_redis().lrange(ev.RECENT, 0, n - 1)]
    status = ev.get_status([i["id"] for i in items])
    for i in items:
        i["status"] = status.get(i["id"], {})
    return {"events": items}


@app.get("/stats")
def stats():
    r = ev.get_redis()
    try:
        pending = r.xpending(ev.STREAM, ev.GROUP)["pending"]
    except Exception:
        pending = None  # the worker hasn't created its group yet
    with engine.connect() as conn:
        active = conn.execute(text("SELECT count(*) FROM products WHERE is_active")).scalar()
        triggers = conn.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'fq_%_cdc'")).scalar()
        try:
            backlog = conn.execute(text("SELECT count(*) FROM catalog_outbox WHERE published_at IS NULL")).scalar()
        except Exception:
            backlog = None
    return {"active_products": active, "triggers_installed": triggers == 3, "outbox_backlog": backlog,
            "pending_events": pending,
            "dead_letters": r.xlen(ev.DLQ) if r.exists(ev.DLQ) else 0}
