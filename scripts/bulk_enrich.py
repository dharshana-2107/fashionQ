"""Phase 2: enrich every product with LLM-extracted attributes + a review summary.

    python -m scripts.bulk_enrich --dry-run       # show the prompt for one batch, no API call
    python -m scripts.bulk_enrich --limit 30      # try a few products first
    python -m scripts.bulk_enrich --inspect 5     # look at 5 enriched products
    python -m scripts.bulk_enrich                 # all remaining products

Resumable: already-enriched products are skipped, so just re-run after any stop.
Paced with --rpm to stay under free-tier rate limits (check yours in Google AI Studio).
"""
import argparse
import json
import logging
import random
import time
from collections import Counter

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from sqlalchemy import func, or_, select
from tqdm import tqdm

from common.config import settings
from common.db import Product, ProductEnrichment, ReviewStats, SessionLocal, init_db
from common.llm import LLMError
from services.indexer.enrich import build_inputs, enrich_batch, save_enrichments
from services.indexer.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_message

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def pending_asins(session, limit: int | None, reenrich: bool) -> list[str]:
    """Products with no enrichment (or an outdated prompt version), most-reviewed first."""
    cond = ProductEnrichment.parent_asin.is_(None)
    if reenrich:
        cond = or_(cond, ProductEnrichment.prompt_version != PROMPT_VERSION)
    q = (select(Product.parent_asin)
         .outerjoin(ProductEnrichment, ProductEnrichment.parent_asin == Product.parent_asin)
         .outerjoin(ReviewStats, ReviewStats.parent_asin == Product.parent_asin)
         .where(Product.is_active, cond)
         .order_by(ReviewStats.review_count.desc().nulls_last()))
    if limit:
        q = q.limit(limit)
    return list(session.scalars(q))


def dry_run(session, batch_size: int):
    asins = pending_asins(session, batch_size, reenrich=False)
    if not asins:
        print("Nothing pending.")
        return
    user = build_user_message(build_inputs(session, asins))
    print("=" * 30, "SYSTEM PROMPT", "=" * 30)
    print(SYSTEM_PROMPT)
    print("=" * 30, "USER MESSAGE", "=" * 31)
    print(user[:4000] + ("\n... (truncated for display)" if len(user) > 4000 else ""))
    est = (len(SYSTEM_PROMPT) + len(user)) // 4
    print(f"\nRoughly {est:,} input tokens for this batch of {len(asins)}. No API call made.")


def inspect(session, n: int):
    rows = session.execute(
        select(Product.title, ProductEnrichment.attributes)
        .join(ProductEnrichment, ProductEnrichment.parent_asin == Product.parent_asin)
    ).all()
    if not rows:
        print("No enriched products yet.")
        return
    for title, attrs in random.sample(rows, min(n, len(rows))):
        print("\n" + title[:100])
        print(json.dumps(attrs, indent=2, ensure_ascii=False))


def print_stats(session):
    total = session.scalar(select(func.count()).select_from(Product))
    rows = session.scalars(select(ProductEnrichment.attributes)).all()
    print("\n" + "=" * 70)
    print(f"Enriched: {len(rows):,} of {total:,} products")
    if not rows:
        return
    for field in ("category", "gender", "sizing"):
        c = Counter(r[field] for r in rows)
        print(f"{field:<10} " + ", ".join(f"{k} {v}" for k, v in c.most_common(8)))
    for field in ("occasions", "seasons", "colors"):
        c = Counter(v for r in rows for v in r[field])
        print(f"{field:<10} " + ", ".join(f"{k} {v}" for k, v in c.most_common(8)))
    other = sum(1 for r in rows if r["category"] == "other")
    print(f"category=other: {100 * other / len(rows):.0f}% (high = check the prompt/categories)")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="max products this run")
    parser.add_argument("--batch-size", type=int, default=10, help="products per LLM request")
    parser.add_argument("--rpm", type=float, default=10, help="max requests per minute")
    parser.add_argument("--max-tokens", type=int, default=12000,
                        help="output budget per request, incl. the model's thinking")
    parser.add_argument("--reenrich", action="store_true",
                        help=f"also redo products enriched with a prompt version other than {PROMPT_VERSION}")
    parser.add_argument("--dry-run", action="store_true", help="print one batch's prompt, no API call")
    parser.add_argument("--inspect", type=int, metavar="N", help="show N enriched products and exit")
    args = parser.parse_args()

    init_db()  # creates the product_enrichment table on first run
    with SessionLocal() as session:
        if args.dry_run:
            return dry_run(session, args.batch_size)
        if args.inspect:
            return inspect(session, args.inspect)

        if not settings.llm_api_key or not settings.llm_model:
            raise SystemExit("Set LLM_API_KEY and LLM_MODEL in .env first.")
        asins = pending_asins(session, args.limit, args.reenrich)
        if not asins:
            print("Nothing to enrich: every product is done.")
            return print_stats(session)

        batches = [asins[i:i + args.batch_size] for i in range(0, len(asins), args.batch_size)]
        print(f"Enriching {len(asins):,} products in {len(batches)} requests "
              f"with {settings.llm_model} (max {args.rpm:g}/min)")

        interval = 60.0 / args.rpm
        done = failed = consecutive_errors = 0
        bar = tqdm(batches, unit=" req")
        for batch in bar:
            started = time.monotonic()
            try:
                inputs = build_inputs(session, batch)
                results, missing = enrich_batch(inputs, max_tokens=args.max_tokens)
                save_enrichments(session, results, inputs)
                session.commit()  # save progress after every batch
                done += len(results)
                failed += len(missing)
                consecutive_errors = 0
            except (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError) as e:
                session.rollback()
                print(f"\nGemini still unavailable after retries: {e}\n"
                        "Progress is saved. Wait a while (or until tomorrow for a daily quota) and re-run.")
                break
            except LLMError as e:
                session.rollback()
                failed += len(batch)
                consecutive_errors += 1
                tqdm.write(f"Batch skipped, will retry next run: {e}")
                if consecutive_errors >= 5:
                    print("\n5 failures in a row; stopping. Read the errors above.")
                    break
            bar.set_postfix(ok=done, failed=failed)
            time.sleep(max(0.0, interval - (time.monotonic() - started)))

        print(f"\nThis run: {done:,} enriched, {failed:,} failed or skipped "
              f"(re-run to retry those).")
        print_stats(session)


if __name__ == "__main__":
    main()
