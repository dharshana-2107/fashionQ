"""Phase 1, step 2: clean the raw data, pick a subset, load it into Postgres.

    python -m scripts.load_catalog                    # top 5,000 products by review count
    python -m scripts.load_catalog --n-products 2000  # smaller subset
    python -m scripts.load_catalog --dry-run          # process and print summary, no DB writes
    python -m scripts.load_catalog --reset            # drop and recreate tables first

Streams the files (never loads everything into memory) in three passes:
  1. reviews -> count reviews per product
  2. meta    -> clean products, keep the most-reviewed ones
  3. reviews -> stats + most helpful review samples for the kept products
"""
import argparse
import heapq
import html
import itertools
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from scripts.download_data import local_paths

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
PRICE_RE = re.compile(r"\d+(?:\.\d+)?")


# ---------------------------------------------------------------- cleaning helpers
def clean_text(value) -> str:
    """Unescape HTML entities, strip tags, collapse whitespace."""
    if value is None:
        return ""
    s = html.unescape(str(value))
    s = TAG_RE.sub(" ", s)
    return WS_RE.sub(" ", s).strip()


def clean_list(items, max_items=20, max_len=500) -> list[str]:
    """Clean a list of strings, dropping empties and case-insensitive duplicates."""
    out, seen = [], set()
    for item in items or []:
        t = clean_text(item)[:max_len]
        if len(t) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= max_items:
            break
    return out


def clean_details(details) -> dict:
    if not isinstance(details, dict):
        return {}
    out = {}
    for k, v in details.items():
        k, v = clean_text(k)[:100], clean_text(v)[:300]
        if k and v:
            out[k] = v
    return out


def parse_price(value) -> float | None:
    """Prices come as numbers, strings like '$19.99', ranges, or 'None'."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not value:
        return None
    m = PRICE_RE.search(str(value).replace(",", ""))
    if not m:
        return None
    price = float(m.group())
    return price if 0 < price < 100_000 else None


def first_image(images) -> str | None:
    """Prefer the MAIN variant image, else the first one available."""
    best = None
    for img in images or []:
        if not isinstance(img, dict):
            continue
        url = img.get("large") or img.get("hi_res") or img.get("thumb")
        if not url:
            continue
        if img.get("variant") == "MAIN":
            return url
        best = best or url
    return best


def to_datetime(ts) -> datetime | None:
    """Review timestamps are unix milliseconds."""
    if not ts:
        return None
    ts = float(ts)
    if ts > 1e12:
        ts /= 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def build_product(m: dict) -> dict | None:
    title = clean_text(m.get("title"))[:500]
    if len(title) < 5:
        return None
    return {
        "parent_asin": m["parent_asin"],
        "title": title,
        "store": clean_text(m.get("store"))[:200] or None,
        "main_category": clean_text(m.get("main_category"))[:100] or None,
        "features": clean_list(m.get("features")),
        "description": clean_list(m.get("description"), max_items=10, max_len=2000),
        "details": clean_details(m.get("details")),
        "price": parse_price(m.get("price")),
        "image_url": first_image(m.get("images")),
        "amazon_avg_rating": m.get("average_rating"),
        "amazon_rating_count": m.get("rating_number"),
    }


def read_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip the rare malformed line


# ---------------------------------------------------------------- the three passes
def count_reviews(reviews_path: Path) -> Counter:
    counts = Counter()
    for r in tqdm(read_jsonl(reviews_path), desc="Pass 1/3 counting reviews", unit=" rev"):
        if r.get("parent_asin"):
            counts[r["parent_asin"]] += 1
    return counts


def select_products(meta_path: Path, counts: Counter, n: int, min_reviews: int) -> list[dict]:
    """Keep the n most-reviewed valid products, with duplicate titles removed."""
    cap = int(n * 1.3)  # extra headroom because dedup removes some
    heap: list[tuple[int, str, dict]] = []  # min-heap on review count
    for m in tqdm(read_jsonl(meta_path), desc="Pass 2/3 cleaning products", unit=" prod"):
        asin = m.get("parent_asin")
        c = counts.get(asin, 0)
        if not asin or c < min_reviews:
            continue
        if len(heap) >= cap and c <= heap[0][0]:
            continue  # can't make the cut, skip the cleaning work
        product = build_product(m)
        if product is None:
            continue
        if len(heap) < cap:
            heapq.heappush(heap, (c, asin, product))
        else:
            heapq.heappushpop(heap, (c, asin, product))

    selected, seen_asins, seen_titles = [], set(), set()
    for c, asin, product in sorted(heap, reverse=True):
        key = (product["title"].lower()[:120], (product["store"] or "").lower())
        if asin in seen_asins or key in seen_titles:
            continue
        seen_asins.add(asin)
        seen_titles.add(key)
        selected.append(product)
        if len(selected) >= n:
            break
    return selected


def collect_reviews(reviews_path: Path, asins: set[str], k: int, prior_weight: float):
    """Per-product stats and the k most helpful reviews (verified first on ties)."""
    acc = {a: {"n": 0, "sum": 0.0, "verified": 0, "first": None, "last": None,
               "hist": Counter()} for a in asins}
    heaps: dict[str, list] = defaultdict(list)
    seq = itertools.count()  # tie-breaker so dicts are never compared

    for r in tqdm(read_jsonl(reviews_path), desc="Pass 3/3 review stats", unit=" rev"):
        s = acc.get(r.get("parent_asin"))
        if s is None:
            continue
        try:
            rating = float(r.get("rating"))
        except (TypeError, ValueError):
            continue
        if not 1 <= rating <= 5:
            continue
        ts = r.get("timestamp") or 0
        verified = bool(r.get("verified_purchase"))
        s["n"] += 1
        s["sum"] += rating
        s["verified"] += verified
        s["hist"][str(int(round(rating)))] += 1
        s["first"] = ts if s["first"] is None else min(s["first"], ts)
        s["last"] = ts if s["last"] is None else max(s["last"], ts)

        text = clean_text(r.get("text"))
        if len(text) < 30:
            continue  # too short to be useful for summaries
        helpful = int(r.get("helpful_vote") or 0)
        item = (helpful, verified, ts, next(seq), {
            "parent_asin": r["parent_asin"],
            "rating": rating,
            "title": clean_text(r.get("title"))[:200] or None,
            "text": text[:1000],
            "helpful_vote": helpful,
            "verified": verified,
            "review_at": to_datetime(ts),
        })
        h = heaps[r["parent_asin"]]
        if len(h) < k:
            heapq.heappush(h, item)
        else:
            heapq.heappushpop(h, item)

    # Bayesian average: pull products with few reviews toward the global mean.
    total_n = sum(s["n"] for s in acc.values()) or 1
    global_mean = sum(s["sum"] for s in acc.values()) / total_n
    stats = []
    for asin, s in acc.items():
        if s["n"] == 0:
            continue
        stats.append({
            "parent_asin": asin,
            "review_count": s["n"],
            "avg_rating": round(s["sum"] / s["n"], 3),
            "bayes_rating": round((prior_weight * global_mean + s["sum"]) / (prior_weight + s["n"]), 3),
            "verified_ratio": round(s["verified"] / s["n"], 3),
            "first_review_at": to_datetime(s["first"]),
            "last_review_at": to_datetime(s["last"]),
            "rating_hist": dict(s["hist"]),
        })
    samples = [item[-1] for h in heaps.values() for item in sorted(h, reverse=True)]
    return stats, samples, global_mean


# ---------------------------------------------------------------- database
def chunks(rows, size=1000):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def write_db(products, stats, samples, reset: bool):
    from sqlalchemy import delete, func, select
    from sqlalchemy.dialects.postgresql import insert

    from common.db import Product, ReviewSample, ReviewStats, drop_all, engine, init_db

    if reset:
        print("Dropping existing tables (--reset)")
        drop_all()
    init_db()

    def upsert(table, rows, key):
        for chunk in chunks(rows):
            stmt = insert(table).values(chunk)
            update = {c: stmt.excluded[c] for c in chunk[0] if c != key}
            if table is Product.__table__:
                update["updated_at"] = func.now()
            conn.execute(stmt.on_conflict_do_update(index_elements=[key], set_=update))

    with engine.begin() as conn:  # one transaction: all or nothing
        upsert(Product.__table__, products, "parent_asin")
        upsert(ReviewStats.__table__, stats, "parent_asin")
        # Samples have no natural key, so replace them for the products we loaded.
        asins = [p["parent_asin"] for p in products]
        for chunk in chunks(asins, 5000):
            conn.execute(delete(ReviewSample.__table__).where(ReviewSample.parent_asin.in_(chunk)))
        for chunk in chunks(samples):
            conn.execute(insert(ReviewSample.__table__).values(chunk))

        counts = {t.name: conn.execute(select(func.count()).select_from(t)).scalar()
                  for t in (Product.__table__, ReviewStats.__table__, ReviewSample.__table__)}
    print("\nRows now in Postgres:", ", ".join(f"{k}={v:,}" for k, v in counts.items()))


# ---------------------------------------------------------------- summary
def print_summary(products, stats, samples, global_mean):
    n = len(products) or 1
    pct = lambda cond: f"{100 * sum(1 for p in products if cond(p)) / n:.0f}%"  # noqa: E731
    by_asin = {s["parent_asin"]: s for s in stats}
    firsts = [s["first_review_at"] for s in stats if s["first_review_at"]]

    print("\n" + "=" * 70)
    print(f"Products selected:      {len(products):,}")
    print(f"Review samples kept:    {len(samples):,}")
    print(f"Global mean rating:     {global_mean:.2f}")
    print(f"Has price:              {pct(lambda p: p['price'])}")
    print(f"Has image:              {pct(lambda p: p['image_url'])}")
    print(f"Has features:           {pct(lambda p: p['features'])}")
    print(f"Has description:        {pct(lambda p: p['description'])}")
    if firsts:
        print(f"First-review dates:     {min(firsts):%Y-%m-%d} to {max(firsts):%Y-%m-%d}")
    depts = Counter(p["details"].get("Department", "(none)") for p in products)
    print("Top departments:        " + ", ".join(f"{d} ({c})" for d, c in depts.most_common(6)))

    print("\nSample products:")
    for p in products[:3] + products[len(products) // 2:len(products) // 2 + 2]:
        s = by_asin.get(p["parent_asin"], {})
        price = f"${p['price']:.2f}" if p["price"] else "no price"
        print(f"  [{p['parent_asin']}] {p['title'][:80]}")
        print(f"      {s.get('review_count', 0):,} reviews, bayes {s.get('bayes_rating', 0):.2f}, {price}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="Amazon_Fashion")
    parser.add_argument("--n-products", type=int, default=5000)
    parser.add_argument("--min-reviews", type=int, default=5)
    parser.add_argument("--samples-per-product", type=int, default=10)
    parser.add_argument("--prior-weight", type=float, default=20,
                        help="Bayesian prior strength, in 'virtual reviews' at the global mean")
    parser.add_argument("--dry-run", action="store_true", help="don't write to Postgres")
    parser.add_argument("--reset", action="store_true", help="drop and recreate tables first")
    args = parser.parse_args()

    paths = local_paths(args.category)
    for kind, p in paths.items():
        if not p.exists():
            raise SystemExit(f"Missing {kind} file at {p}. Run: python -m scripts.download_data")

    counts = count_reviews(paths["reviews"])
    print(f"  {len(counts):,} products have reviews")
    products = select_products(paths["meta"], counts, args.n_products, args.min_reviews)
    print(f"  selected {len(products):,} products")
    stats, samples, global_mean = collect_reviews(
        paths["reviews"], {p["parent_asin"] for p in products},
        args.samples_per_product, args.prior_weight)

    print_summary(products, stats, samples, global_mean)
    if args.dry_run:
        print("\nDry run: nothing written.")
        return
    write_db(products, stats, samples, args.reset)
    print("Phase 1 done. Next: Phase 2 (enrichment).")


if __name__ == "__main__":
    main()
