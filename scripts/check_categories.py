"""Read-only report: how would the title rules change the LLM's categories?

  python -m scripts.check_categories                  # summary + 3 samples per change
  python -m scripts.check_categories --samples 10
  python -m scripts.check_categories --unmatched accessories   # titles no rule caught

Nothing is written anywhere. Re-index afterwards to apply:
  python -m scripts.bulk_index --only-enriched
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from sqlalchemy import text

from common.db import engine
from services.indexer.category_rules import rule_category
from services.indexer.document import _llm_category

SQL = """
SELECT p.parent_asin, p.title, e.attributes AS attrs, e.category AS enr_category, e.model AS enr_model
FROM products p JOIN product_enrichment e ON e.parent_asin = p.parent_asin
WHERE p.is_active
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=int, default=3, help="example titles per change type")
    ap.add_argument("--unmatched", metavar="CATEGORY", help="list titles in this LLM category that no rule matched")
    args = ap.parse_args()

    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(SQL)).mappings()]

    before, after = Counter(), Counter()
    changes: dict[tuple, list[str]] = defaultdict(list)
    unmatched: dict[str, list[str]] = defaultdict(list)
    by_model = Counter()
    for r in rows:
        old = _llm_category(r) or "none"
        rule = rule_category(r["title"])
        new = rule or old
        before[old] += 1
        after[new] += 1
        if rule is None:
            unmatched[old].append(r["title"] or "")
        elif rule != old:
            changes[(old, rule)].append(r["title"] or "")
            by_model[r.get("enr_model") or "?"] += 1

    n_changed = sum(len(v) for v in changes.values())
    n_matched = len(rows) - sum(len(v) for v in unmatched.values())
    print(f"{len(rows)} enriched products | a rule matched {n_matched} | category changes for {n_changed}")
    print("changes per enrichment model:", dict(by_model))

    print(f"\n{'category':<22}{'before':>8}{'after':>8}")
    for cat in sorted(set(before) | set(after), key=lambda c: -after[c]):
        print(f"{cat:<22}{before[cat]:>8}{after[cat]:>8}")

    print("\nBiggest changes (old -> new):")
    for (old, new), titles in sorted(changes.items(), key=lambda kv: -len(kv[1]))[:20]:
        print(f"\n  {old} -> {new}: {len(titles)}")
        for t in titles[: args.samples]:
            print(f"     - {t[:95]}")

    if args.unmatched:
        titles = unmatched.get(args.unmatched.lower(), [])
        print(f"\nNo rule matched, kept as '{args.unmatched}' ({len(titles)}):")
        for t in titles[:50]:
            print(f"     - {t[:95]}")


if __name__ == "__main__":
    main()
