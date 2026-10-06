"""Offline evaluation: does each part of the pipeline make search better?

  python -m eval.run_eval                     # all 4 variants, writes eval/results.md + .json
  python -m eval.run_eval --variants A,B,C    # skip the LLM variant (no API calls)
  python -m eval.run_eval --only jacket_hi    # one query, printed in detail

Variants (an "ablation": add one component at a time)
  A  dense      BGE-M3 dense vectors on the raw query
  B  hybrid     + sparse keyword vectors, merged with RRF
  C  +rerank    + cross-encoder reranker on the top 40
  D  full       + LLM query parsing (slots, filters), rules, boosts  = the real system

Grading (see eval/queries.json): each result gets 0, 1 or 2 points.
  1 = right category, 2 = right category AND the key attribute (terms / season / occasion / formality).
Metrics
  P@5            share of the top 5 with >= 1 point
  nDCG@10        ranking quality vs. an ideal list of ten 2-point results
  constraints    share of results obeying the query's price / gender limit (unknown values skipped)
  parity         non-English P@5 divided by the same query's English P@5
  parse rate     (D only) share of queries the LLM turned into a plan instead of falling back
  latency        median and 90th-percentile time per query
Stop the indexer worker while this runs: it shares the GPU and would slow the timings.
"""
from __future__ import annotations

import os

os.environ.setdefault("TQDM_DISABLE", "1")

import argparse
import json
import logging
import math
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

from common import vectorstore as vs
from common.embedder import QUERY_MAX_TOKENS, get_embedder

HERE = Path(__file__).parent
K = 10
RERANK_CANDIDATES = 40
KIDS = {"kids", "girls", "boys", "baby"}
VARIANT_NAMES = {"A": "Dense only", "B": "Hybrid (dense + sparse)", "C": "Hybrid + rerank", "D": "Full system (LLM + rules)"}

logging.basicConfig(level=logging.WARNING)


# ------------------------------------------------------------------ grading

def grade(p: dict, rules: dict) -> int:
    cats = rules.get("categories")
    if cats and p.get("category") not in cats:
        return 0
    text = f"{p.get('title') or ''} {(p.get('doc') or '')[:600]}"
    attr = any(re.search(t, text, re.I) for t in rules.get("terms", []))
    attr = attr or bool(set(rules.get("seasons", [])) & set(p.get("seasons") or []))
    attr = attr or bool(set(rules.get("occasions", [])) & set(p.get("occasions") or []))
    if rules.get("formality") and p.get("formality") == rules["formality"]:
        attr = True
    return 2 if attr else 1


def constraint_checks(p: dict, rules: dict) -> list[bool]:
    """One bool per checkable constraint; unknown values (no price / no gender) are skipped."""
    out = []
    if rules.get("max_price") is not None and p.get("price") is not None:
        out.append(float(p["price"]) <= rules["max_price"])
    g, want = p.get("gender"), rules.get("gender")
    if want and g:
        allowed = KIDS if want == "kids" else {want, "unisex"}
        out.append(g in allowed)
    return out


def ndcg(grades: list[int], k: int = K) -> float:
    dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades[:k]))
    ideal = sum((2 ** 2 - 1) / math.log2(i + 2) for i in range(k))
    return dcg / ideal


# ------------------------------------------------------------------ variants

def run_basic(variant: str, q: str, client, reranker) -> tuple[list[dict], float]:
    t0 = time.time()
    emb = get_embedder().encode([q], batch_size=1, max_length=QUERY_MAX_TOKENS)[0]
    flt = vs.build_filter(adults_only=False)
    if variant == "A":
        hits = vs.search(client, emb, mode="dense", flt=flt, limit=K)
    elif variant == "B":
        hits = vs.search(client, emb, mode="hybrid", flt=flt, limit=K)
    else:  # C
        hits = vs.search(client, emb, mode="hybrid", flt=flt, limit=RERANK_CANDIDATES)
        scores = reranker.score([(q, (h.payload or {}).get("doc") or "") for h in hits])
        hits = [h for _, h in sorted(zip(scores, hits), key=lambda x: -x[0])][:K]
    return [h.payload or {} for h in hits], time.time() - t0


def run_full(q: str) -> tuple[list[dict], float, dict]:
    from services.search import pipeline
    t0 = time.time()
    res = pipeline.search(q, k=K, use_llm=True, use_rerank=True)
    elapsed = time.time() - t0
    # An outfit returns several slots: interleave them (top of each slot first), like a shopper scanning.
    slots = [s["results"] for s in res["slots"]]
    merged = []
    for i in range(K):
        for s in slots:
            if i < len(s):
                merged.append(s[i])
    return merged[:K], elapsed, res


def _payload_by_asin(client, asins: list[str]) -> dict[str, dict]:
    """Full payloads (doc text, tags) for the cards the pipeline returned."""
    if not asins:
        return {}
    pts = client.retrieve(vs.ALIAS, [vs.point_id(a) for a in asins], with_payload=True)
    return {p.payload["parent_asin"]: p.payload for p in pts if p.payload}


# ------------------------------------------------------------------ main

def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", default="A,B,C,D")
    ap.add_argument("--only", help="run a single query id and print details")
    ap.add_argument("--rpm", type=float, default=15, help="max LLM calls per minute for variant D (cache hits are free)")
    args = ap.parse_args()
    variants = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    queries = json.loads((HERE / "queries.json").read_text())["queries"]
    if args.only:
        queries = [q for q in queries if q["id"] == args.only]

    client = vs.get_client()
    print("Loading models...")
    get_embedder().encode(["warmup"], batch_size=1, max_length=16)
    reranker = None
    if "C" in variants or "D" in variants:
        from services.search.reranker import get_reranker
        reranker = get_reranker()
        reranker.score([("warmup", "warmup")])

    results: dict[str, list[dict]] = {v: [] for v in variants}
    last_llm_call = 0.0
    for qi, qr in enumerate(queries, 1):
        print(f"[{qi}/{len(queries)}] {qr['id']}: {qr['q']}")
        for v in variants:
            if v == "D":
                wait = 60.0 / args.rpm - (time.time() - last_llm_call)
                items, secs, res = run_full(qr["q"])
                if res["parse"].get("source") == "llm":
                    last_llm_call = time.time()
                    if wait > 0:
                        time.sleep(wait)  # stay under the free-tier rate limit
                full = _payload_by_asin(client, [c.get("parent_asin") for c in items])
                payloads = [full.get(c.get("parent_asin"), c) for c in items]
                extra = {"parse_source": res["parse"].get("source"), "parse_model": res["parse"].get("model"),
                         "timings": res["timings"], "english": (res.get("parsed") or {}).get("english"),
                         "slots": [s["query"] for s in res["slots"]]}
            else:
                payloads, secs = run_basic(v, qr["q"], client, reranker)
                extra = {}
            grades = [grade(p, qr) for p in payloads]
            checks = [c for p in payloads for c in constraint_checks(p, qr)]
            results[v].append({
                "id": qr["id"], "lang": qr["lang"], "group": qr.get("group"), "q": qr["q"],
                "p5": sum(g >= 1 for g in grades[:5]) / 5, "ndcg10": ndcg(grades),
                "constraint_ok": sum(checks), "constraint_n": len(checks), "seconds": secs,
                "top": [{"title": (p.get("title") or "")[:90], "category": p.get("category"),
                         "price": p.get("price"), "gender": p.get("gender"), "grade": g}
                        for p, g in zip(payloads[:5], grades[:5])], **extra})
        if args.only:
            for v in variants:
                r = results[v][-1]
                print(f"\n  {v} {VARIANT_NAMES[v]}: P@5 {r['p5']:.2f}  nDCG@10 {r['ndcg10']:.2f}  {r['seconds']:.2f}s")
                if r.get("slots"):
                    print(f"     parsed: {r.get('english')!r} slots={r['slots']} ({r.get('parse_source')})")
                for t in r["top"]:
                    print(f"     [{t['grade']}] {t['title'][:70]}  ({t['category']}, {t['gender']}, {t['price']})")

    summary = summarize(results, queries)
    out = {"generated": datetime.now().isoformat(timespec="seconds"), "summary": summary, "per_query": results}
    (HERE / "results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (HERE / "results.md").write_text(render_md(summary, results))
    print("\n" + render_summary_table(summary))
    print(f"\nWrote {HERE / 'results.md'} and {HERE / 'results.json'}")


def summarize(results: dict[str, list[dict]], queries: list[dict]) -> dict:
    summary = {}
    for v, rows in results.items():
        if not rows:
            continue
        ok = sum(r["constraint_ok"] for r in rows)
        n = sum(r["constraint_n"] for r in rows)
        en = {r["group"]: r["p5"] for r in rows if r["group"] and r["lang"] == "en"}
        ratios = [min(r["p5"] / en[r["group"]], 1.0) for r in rows
                  if r["group"] and r["lang"] != "en" and en.get(r["group"])]
        by_lang = {}
        for lang in sorted({r["lang"] for r in rows}):
            lr = [r["p5"] for r in rows if r["lang"] == lang]
            by_lang[lang] = round(statistics.mean(lr), 3)
        secs = [r["seconds"] for r in rows]
        s = {"name": VARIANT_NAMES[v], "queries": len(rows),
             "p5": round(statistics.mean(r["p5"] for r in rows), 3),
             "ndcg10": round(statistics.mean(r["ndcg10"] for r in rows), 3),
             "constraint_acc": round(ok / n, 3) if n else None, "constraint_n": n,
             "parity": round(statistics.mean(ratios), 3) if ratios else None,
             "p5_by_lang": by_lang,
             "latency_p50_s": round(statistics.median(secs), 3), "latency_p90_s": round(pct(secs, 0.9), 3)}
        if v == "D":
            parsed = [r for r in rows if r.get("parse_source") in ("llm", "cache")]
            s["parse_rate"] = round(len(parsed) / len(rows), 3)
            stage = {}
            for key in ("parse_ms", "embed_ms", "search_ms", "rerank_ms"):
                vals = [r["timings"].get(key, 0) for r in rows if r.get("timings")]
                stage[key] = round(statistics.median(vals)) if vals else None
            s["stage_median_ms"] = stage
        summary[v] = s
    return summary


def _f(x, fmt="{:.2f}"):
    return "n/a" if x is None else fmt.format(x)


def render_summary_table(summary: dict) -> str:
    lines = ["| Variant | P@5 | nDCG@10 | Constraints | Multilingual parity | Latency p50 / p90 |",
             "|---|---|---|---|---|---|"]
    for v, s in summary.items():
        lines.append(f"| {v}. {s['name']} | {_f(s['p5'])} | {_f(s['ndcg10'])} | {_f(s['constraint_acc'], '{:.0%}')} "
                     f"| {_f(s['parity'], '{:.0%}')} | {s['latency_p50_s']:.2f}s / {s['latency_p90_s']:.2f}s |")
    return "\n".join(lines)


def render_md(summary: dict, results: dict) -> str:
    out = ["# Search evaluation", "",
           f"_Generated {datetime.now():%Y-%m-%d %H:%M} by `python -m eval.run_eval`. "
           f"{next(iter(summary.values()))['queries']} queries in English, Tamil and Hindi "
           "(see `eval/queries.json`)._", "",
           "## Summary", "", render_summary_table(summary), "",
           "- **P@5**: share of the top 5 results that are relevant (right category).",
           "- **nDCG@10**: ranking quality; 1.0 = ten perfect results (right category and attribute) in order.",
           "- **Constraints**: results obeying an explicit price or gender limit (items with unknown price/gender skipped).",
           "- **Multilingual parity**: Tamil/Hindi P@5 as a share of the same query's English P@5.", ""]
    langs = sorted({l for s in summary.values() for l in s["p5_by_lang"]})
    out += ["### P@5 by query language", "", "| Variant | " + " | ".join(langs) + " |",
            "|---|" + "---|" * len(langs)]
    for v, s in summary.items():
        out.append(f"| {v}. {s['name']} | " + " | ".join(_f(s["p5_by_lang"].get(l)) for l in langs) + " |")
    if "D" in summary:
        d = summary["D"]
        st = d.get("stage_median_ms", {})
        out += ["", "### Full system details", "",
                f"- LLM parse success rate: **{_f(d.get('parse_rate'), '{:.0%}')}** (the rest fell back to plain hybrid search)",
                f"- Median time per stage: LLM parse {st.get('parse_ms')} ms (0 when cached), embedding {st.get('embed_ms')} ms, "
                f"search {st.get('search_ms')} ms, rerank {st.get('rerank_ms')} ms"]
    out += ["", "## Per query (P@5 for each variant)", "",
            "| Query | Lang | " + " | ".join(results) + " |", "|---|---|" + "---|" * len(results)]
    by_id = {v: {r["id"]: r for r in rows} for v, rows in results.items()}
    first = next(iter(results.values()))
    for r in first:
        out.append(f"| {r['q']} | {r['lang']} | " + " | ".join(_f(by_id[v][r['id']]['p5']) for v in results) + " |")
    if "D" in results:
        out += ["", "## Full system: top 3 results per query", ""]
        for r in results["D"]:
            out.append(f"**{r['q']}** (P@5 {r['p5']:.2f})" + (f": understood as *{r['english']}*" if r.get("english") else ""))
            for t in r["top"][:3]:
                out.append(f"- [{t['grade']}] {t['title']} ({t['category']})")
            out.append("")
    out += ["## Limitations", "",
            "- Relevance is judged by rules (category + keywords/tags), not by people; style and taste aren't measured.",
            "- 26 queries is a small sample: good for comparing variants, not a precise benchmark.",
            "- Product categories come from title rules and a small LLM, so a few 'wrong category' results may be label errors."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
