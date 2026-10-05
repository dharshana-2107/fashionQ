"""Try searches from the terminal. No LLM yet: raw query -> BGE-M3 -> Qdrant.

  python -m scripts.try_search                                  # demo queries (EN + Tamil + Hindi)
  python -m scripts.try_search "linen shirt for a summer wedding"
  python -m scripts.try_search "running shoes" --mode all       # compare dense / sparse / hybrid
  python -m scripts.try_search "dress" --gender women --max-price 40
  python -m scripts.try_search "warm jacket" --show-doc         # see the text that was embedded
"""
from __future__ import annotations

import argparse
import time

from common import vectorstore as vs

DEMO_QUERIES = [
    "outfit for the beach this summer",
    "comfortable shoes for standing all day at work",
    "கோடை கடற்கரைக்கு ஏற்ற ஆடை",     # Tamil: clothes suitable for the summer beach
    "सर्दियों के लिए गर्म जैकेट",        # Hindi: warm jacket for winter
]


def fmt_hit(rank: int, hit) -> str:
    p = hit.payload or {}
    bits = [p.get("category") or "?", p.get("gender") or "?"]
    if p.get("price") is not None:
        bits.append(f"${p['price']:.2f}")
    if p.get("avg_rating") is not None:
        bits.append(f"★{p['avg_rating']:.1f} ({p.get('review_count', 0)})")
    title = (p.get("title") or "")[:70]
    return f"  {rank:>2}. {hit.score:7.4f}  {title:<70}  [{' | '.join(bits)}]"


def run(embedder, client, query: str, modes: list[str], k: int, flt, collection: str,
        show_doc: bool) -> None:
    t0 = time.time()
    emb = embedder.encode_query(query)
    t_emb = (time.time() - t0) * 1000
    print("\n" + "=" * 100 + f"\nQuery: {query}\n(embedding {t_emb:.0f} ms, "
          f"{len(emb.sparse.indices)} sparse tokens)")
    for mode in modes:
        t0 = time.time()
        hits = vs.search(client, emb, collection=collection, mode=mode, flt=flt, limit=k)
        print(f"--- {mode} ({(time.time() - t0) * 1000:.0f} ms)")
        if not hits:
            print("  (no results)")
        for i, h in enumerate(hits, 1):
            print(fmt_hit(i, h))
            if show_doc:
                print("      " + (h.payload.get("doc") or "").replace("\n", "\n      ")[:600])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*", help="your query (leave empty for the demo set)")
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "dense", "sparse", "all"])
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--collection", default=vs.ALIAS)
    ap.add_argument("--gender")
    ap.add_argument("--category")
    ap.add_argument("--min-price", type=float)
    ap.add_argument("--max-price", type=float)
    ap.add_argument("--show-doc", action="store_true")
    args = ap.parse_args()

    client = vs.get_client()
    target = vs.alias_target(client, args.collection) or args.collection
    if not client.collection_exists(target):
        raise SystemExit(f"Collection '{args.collection}' not found. Run: python -m scripts.bulk_index --only-enriched")
    print(f"Searching '{args.collection}' -> {target} ({client.count(target).count} products)")

    from common.embedder import get_embedder
    t0 = time.time()
    embedder = get_embedder()
    print(f"Model loaded in {time.time() - t0:.1f}s")

    flt = vs.build_filter(category=args.category, gender=args.gender,
                          min_price=args.min_price, max_price=args.max_price)
    modes = ["dense", "sparse", "hybrid"] if args.mode == "all" else [args.mode]
    queries = [" ".join(args.query)] if args.query else DEMO_QUERIES
    for q in queries:
        run(embedder, client, q, modes, args.k, flt, args.collection, args.show_doc)


if __name__ == "__main__":
    main()
