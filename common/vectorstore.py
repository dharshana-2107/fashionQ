"""Qdrant helpers: collection setup, alias swap, upsert state, filters, search.

Naming scheme
-------------
Real collection : products_bge_m3_v1   (versioned: model + version)
Alias           : products             (what search code always uses)

When you change the embedding model or document format later, build
products_bge_m3_v2 next to v1, test it, then point the alias at v2. Search
never notices, and you can roll back by pointing the alias back at v1.
"""
from __future__ import annotations

import json
import hashlib
import uuid
from functools import lru_cache
from typing import Any, Iterable

from qdrant_client import QdrantClient, models

from common.config import settings
from common.embedder import DENSE_DIM, Embedding

ALIAS = "products"
DENSE = "dense"    # name of the dense vector inside each point
SPARSE = "sparse"  # name of the sparse vector inside each point

# Payload fields we filter/sort on get an index (like a DB index in Postgres).
KEYWORD_FIELDS = ["parent_asin", "category", "product_type", "gender", "occasions",
                  "seasons", "colors", "materials", "styles", "store"]
FLOAT_FIELDS = ["price", "bayes_rating"]
INTEGER_FIELDS = ["review_count"]
BOOL_FIELDS = ["is_active", "enriched"]

_ASIN_NAMESPACE = uuid.UUID("6f1c2a52-6a8e-4c1e-9b7a-2f0d5c1e8a11")


def model_slug() -> str:
    """'BAAI/bge-m3' -> 'bge_m3'"""
    return settings.embed_model.split("/")[-1].lower().replace("-", "_").replace(".", "_")


def versioned_name(version: str = "v1") -> str:
    return f"products_{model_slug()}_{version}"


def point_id(parent_asin: str) -> str:
    """Qdrant ids must be ints or UUIDs, so turn the ASIN into a stable UUID."""
    return str(uuid.uuid5(_ASIN_NAMESPACE, parent_asin))


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=60)


# ---------------------------------------------------------------- collection

def ensure_collection(client: QdrantClient, name: str, recreate: bool = False) -> bool:
    """Create the collection if missing. Returns True if it was (re)created."""
    exists = client.collection_exists(name)
    if exists and not recreate:
        return False
    if exists:
        client.delete_collection(name)
    client.create_collection(
        collection_name=name,
        vectors_config={DENSE: models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)},
        # No IDF modifier: BGE-M3 already learned how important each token is.
        sparse_vectors_config={SPARSE: models.SparseVectorParams()},
    )
    ensure_payload_indexes(client, name)
    return True


def ensure_payload_indexes(client: QdrantClient, name: str) -> None:
    schema = {
        **{f: models.PayloadSchemaType.KEYWORD for f in KEYWORD_FIELDS},
        **{f: models.PayloadSchemaType.FLOAT for f in FLOAT_FIELDS},
        **{f: models.PayloadSchemaType.INTEGER for f in INTEGER_FIELDS},
        **{f: models.PayloadSchemaType.BOOL for f in BOOL_FIELDS},
    }
    for field, ftype in schema.items():
        client.create_payload_index(name, field_name=field, field_schema=ftype, wait=True)


def point_alias(client: QdrantClient, collection: str, alias: str = ALIAS) -> None:
    """Atomically make `alias` point at `collection` (removing any old target)."""
    ops: list = []
    if any(a.alias_name == alias for a in client.get_aliases().aliases):
        ops.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias)))
    ops.append(models.CreateAliasOperation(
        create_alias=models.CreateAlias(collection_name=collection, alias_name=alias)))
    client.update_collection_aliases(change_aliases_operations=ops)


def alias_target(client: QdrantClient, alias: str = ALIAS) -> str | None:
    for a in client.get_aliases().aliases:
        if a.alias_name == alias:
            return a.collection_name
    return None


# ---------------------------------------------------------------- writing

def to_sparse_vector(emb: Embedding) -> models.SparseVector:
    return models.SparseVector(indices=emb.sparse.indices, values=emb.sparse.values)


def make_point(parent_asin: str, emb: Embedding, payload: dict) -> models.PointStruct:
    return models.PointStruct(
        id=point_id(parent_asin),
        vector={DENSE: emb.dense, SPARSE: to_sparse_vector(emb)},
        payload=payload,
    )


def fetch_index_state(client: QdrantClient, collection: str) -> dict[str, tuple[str, str]]:
    """{parent_asin: (doc_hash, payload_hash)} for everything already indexed."""
    state: dict[str, tuple[str, str]] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection, limit=1000, offset=offset, with_vectors=False,
            with_payload=["parent_asin", "doc_hash", "payload_hash"],
        )
        for p in points:
            pl = p.payload or {}
            state[pl.get("parent_asin")] = (pl.get("doc_hash"), pl.get("payload_hash"))
        if offset is None:
            return state


def delete_products(client: QdrantClient, collection: str, asins: Iterable[str]) -> int:
    ids = [point_id(a) for a in asins]
    if ids:
        client.delete(collection, points_selector=models.PointIdsList(points=ids), wait=True)
    return len(ids)


# ---------------------------------------------------------------- filters

def _as_list(v) -> list:
    if v is None:
        return []
    return [str(x).lower() for x in (v if isinstance(v, (list, tuple, set)) else [v])]


KIDS_GENDERS = ["kids", "girls", "boys", "baby"]


def build_filter(category=None, gender=None, occasions=None, seasons=None, colors=None,
                 min_price: float | None = None, max_price: float | None = None,
                 only_active: bool = True, adults_only: bool = True) -> models.Filter | None:
    """Hard filters. List fields match if they share ANY value with the request.

    - gender "men"/"women" also matches unisex items.
    - asking for kids/girls/boys matches every kids' label; otherwise (adults_only)
      kids' items are excluded, so "formal outfit" never shows a toddler tee.
    - price: only 20% of products have a price, so items WITHOUT a price are kept
      (the pipeline ranks them slightly lower); items with a price must fit the range.
    """
    must: list = []
    must_not: list = []
    if only_active:
        must.append(models.FieldCondition(key="is_active", match=models.MatchValue(value=True)))
    if category:
        must.append(models.FieldCondition(key="category", match=models.MatchAny(any=_as_list(category))))
    genders = _as_list(gender)
    if genders:
        if any(g in KIDS_GENDERS for g in genders):
            genders = sorted(set(genders) | set(KIDS_GENDERS))
        elif "unisex" not in genders:
            genders.append("unisex")  # unisex items suit everyone
        must.append(models.FieldCondition(key="gender", match=models.MatchAny(any=genders)))
    elif adults_only:
        must_not.append(models.FieldCondition(key="gender", match=models.MatchAny(any=KIDS_GENDERS)))
    for key, val in (("occasions", occasions), ("seasons", seasons), ("colors", colors)):
        if val:
            must.append(models.FieldCondition(key=key, match=models.MatchAny(any=_as_list(val))))
    if min_price is not None or max_price is not None:
        must.append(models.Filter(should=[
            models.FieldCondition(key="price", range=models.Range(gte=min_price, lte=max_price)),
            models.IsEmptyCondition(is_empty=models.PayloadField(key="price")),
        ]))
    if not must and not must_not:
        return None
    return models.Filter(must=must or None, must_not=must_not or None)


# ---------------------------------------------------------------- search

def search(client: QdrantClient, emb: Embedding, collection: str = ALIAS, mode: str = "hybrid",
           flt: models.Filter | None = None, limit: int = 10, prefetch_limit: int = 100,
           with_payload: bool | list[str] = True) -> list[models.ScoredPoint]:
    """mode: 'dense' (meaning), 'sparse' (keywords) or 'hybrid' (both, merged with RRF).

    RRF = Reciprocal Rank Fusion: each list votes 1/(60 + rank) for each item.
    Items ranked high in BOTH lists win. It ignores raw scores, which is handy
    because dense and sparse scores are on completely different scales.
    """
    sparse = to_sparse_vector(emb)
    has_sparse = bool(sparse.indices)
    if mode == "dense" or (mode == "hybrid" and not has_sparse):
        res = client.query_points(collection, query=emb.dense, using=DENSE, query_filter=flt,
                                  limit=limit, with_payload=with_payload)
    elif mode == "sparse":
        if not has_sparse:
            return []
        res = client.query_points(collection, query=sparse, using=SPARSE, query_filter=flt,
                                  limit=limit, with_payload=with_payload)
    elif mode == "hybrid":
        res = client.query_points(
            collection,
            prefetch=[
                models.Prefetch(query=emb.dense, using=DENSE, filter=flt, limit=prefetch_limit),
                models.Prefetch(query=sparse, using=SPARSE, filter=flt, limit=prefetch_limit),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=flt, limit=limit, with_payload=with_payload,
        )
    else:
        raise ValueError(f"unknown mode {mode!r}")
    return res.points
