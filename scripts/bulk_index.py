"""Embed products with BGE-M3 and store them in Qdrant. Safe to re-run.

Each run compares what's in Postgres with what's already in Qdrant:
  - new product, or its document text changed  -> embed + upsert (slow part)
  - only payload changed (price, rating, ...)   -> update payload, no embedding
  - nothing changed                             -> skip
  - product deactivated in Postgres             -> delete from Qdrant

Examples
  python -m scripts.bulk_index --only-enriched --dry-run     # peek, no writes
  python -m scripts.bulk_index --only-enriched --limit 50    # small test
  python -m scripts.bulk_index --only-enriched               # the real run
  python -m scripts.bulk_index --only-enriched --version v2 --recreate --no-alias
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from sqlalchemy import text

from common import vectorstore as vs
from common.db import engine
from services.indexer.document import build_document, build_payload

SQL = """
SELECT p.parent_asin, p.title, p.store, p.main_category, p.features, p.description, p.details,
       p.price, p.image_url, p.amazon_avg_rating, p.amazon_rating_count, p.is_active,
       e.attributes AS attrs, e.category AS enr_category, e.gender AS enr_gender,
       e.review_summary, e.model AS enr_model,
       s.review_count, s.avg_rating, s.bayes_rating
FROM products p
{join} product_enrichment e ON e.parent_asin = p.parent_asin
LEFT JOIN review_stats s ON s.parent_asin = p.parent_asin
WHERE p.is_active
ORDER BY s.review_count DESC NULLS LAST, p.parent_asin
"""


def load_rows(only_enriched: bool, limit: int | None) -> list[dict]:
    sql = SQL.format(join="JOIN" if only_enriched else "LEFT JOIN")
    if limit:
        sql += f" LIMIT {int(limit)}"
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql)).mappings()]


def load_inactive_asins() -> list[str]:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(text("SELECT parent_asin FROM products WHERE NOT is_active"))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only-enriched", action="store_true", help="index only products with LLM enrichment")
    ap.add_argument("--limit", type=int, help="only look at the first N products")
    ap.add_argument("--batch-size", type=int, default=16, help="texts per embedding batch (16 is good on M2)")
    ap.add_argument("--version", default="v1", help="collection version suffix")
    ap.add_argument("--recreate", action="store_true", help="delete and rebuild the collection")
    ap.add_argument("--force", action="store_true", help="re-embed everything even if unchanged")
    ap.add_argument("--no-alias", action="store_true", help=f"don't point the '{vs.ALIAS}' alias here")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between batches (keeps the laptop cool)")
    ap.add_argument("--dry-run", action="store_true", help="show documents + counts, write nothing")
    args = ap.parse_args()

    collection = vs.versioned_name(args.version)
    client = vs.get_client()

    rows = load_rows(args.only_enriched, args.limit)
    print(f"Postgres: {len(rows)} active products selected (only_enriched={args.only_enriched})")
    if not rows:
        sys.exit("Nothing to index. Did Phase 1/2 run?")

    if args.dry_run:
        for r in rows[:2]:
            doc = build_document(r)
            payload = build_payload(r, doc)
            print("\n" + "=" * 70 + f"\n{r['parent_asin']}  ({len(doc)} chars)\n" + "-" * 70)
            print(doc)
            print("-" * 70 + "\npayload:", json.dumps({k: v for k, v in payload.items() if k != "doc"},
                                                     indent=1, ensure_ascii=False)[:1500])
        lens = sorted(len(build_document(r)) for r in rows)
        print(f"\nDoc length (chars): min={lens[0]} median={lens[len(lens) // 2]} max={lens[-1]}")
        print("Dry run: nothing written.")
        return

    if vs.ensure_collection(client, collection, recreate=args.recreate):
        print(f"Created collection {collection}")
    existing = vs.fetch_index_state(client, collection)
    print(f"Qdrant:   {len(existing)} points already in {collection}")

    to_embed, to_repayload = [], []
    for r in rows:
        doc = build_document(r)
        payload = build_payload(r, doc)
        old = existing.get(r["parent_asin"])
        if args.force or old is None or old[0] != payload["doc_hash"]:
            to_embed.append((r["parent_asin"], doc, payload))
        elif old[1] != payload["payload_hash"]:
            to_repayload.append((r["parent_asin"], payload))
    skipped = len(rows) - len(to_embed) - len(to_repayload)
    print(f"Plan:     embed {len(to_embed)}, payload-only {len(to_repayload)}, unchanged {skipped}")

    if to_embed:
        from common.embedder import get_embedder  # import here so --dry-run stays fast
        t0 = time.time()
        embedder = get_embedder()
        print(f"Loaded {embedder.model_name} on {embedder.device} in {time.time() - t0:.1f}s")

        t0, done = time.time(), 0
        for i in range(0, len(to_embed), args.batch_size):
            batch = to_embed[i: i + args.batch_size]
            embs = embedder.encode_documents([doc for _, doc, _ in batch], batch_size=args.batch_size)
            points = [vs.make_point(asin, e, payload) for (asin, _, payload), e in zip(batch, embs)]
            client.upsert(collection, points=points, wait=True)
            done += len(batch)
            rate = done / max(time.time() - t0, 1e-6)
            eta = (len(to_embed) - done) / rate if rate else 0
            print(f"  {done}/{len(to_embed)}  {rate:.1f} docs/s  ETA {eta / 60:.1f} min", flush=True)
            if args.sleep:
                time.sleep(args.sleep)

    if to_repayload:
        t0 = time.time()
        for i in range(0, len(to_repayload), 256):
            ops = [vs.models.OverwritePayloadOperation(overwrite_payload=vs.models.SetPayload(
                       payload=payload, points=[vs.point_id(asin)]))
                   for asin, payload in to_repayload[i: i + 256]]
            client.batch_update_points(collection, update_operations=ops, wait=True)
        print(f"Updated payload of {len(to_repayload)} products in {time.time() - t0:.1f}s (no re-embedding)")

    gone = [a for a in load_inactive_asins() if a in existing]
    if gone:
        print(f"Deleted {vs.delete_products(client, collection, gone)} deactivated products")

    if not args.no_alias:
        vs.point_alias(client, collection)
        print(f"Alias '{vs.ALIAS}' -> {collection}")
    total = client.count(collection, exact=True).count
    print(f"Done. {collection} now holds {total} products.")


if __name__ == "__main__":
    main()
