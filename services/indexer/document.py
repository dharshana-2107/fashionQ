"""Turn one product (DB row + LLM enrichment) into:
1. a text DOCUMENT  -> what gets embedded (what search "reads")
2. a PAYLOAD dict   -> stored next to the vectors, used for filters + display

Rule of thumb: words that describe the product go in the document; numbers and
exact facts you filter or sort on (price, rating, gender) go in the payload.
Embeddings are bad at numbers, filters are perfect at them.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from common.vectorstore import stable_hash
from services.indexer.attribute_rules import rule_attributes
from services.indexer.category_rules import fix_category

# Bump this whenever you change build_document(): every product re-embeds on the
# next bulk_index run, because its doc_hash changes.
DOC_VERSION = "d1"

MAX_FEATURES = 6
MAX_FEATURE_CHARS = 160
MAX_DESC_CHARS = 400  # only used when there is no enrichment

# Amazon "features" that add noise but no meaning
_JUNK_FEATURES = {"imported", "machine wash", "hand wash only", "hand wash", "pull on closure",
                  "zipper closure", "lace up closure", "dry clean only"}


def _as_obj(v: Any, default):
    """JSONB comes back as list/dict from Postgres; tolerate JSON strings too."""
    if v is None:
        return default
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v


def _str_list(v: Any) -> list[str]:
    v = _as_obj(v, [])
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out, seen = [], set()
    for x in v:
        s = str(x).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _attrs(row: dict) -> dict:
    return _as_obj(row.get("attrs"), {}) or {}


def _llm_category(row: dict) -> str | None:
    c = _attrs(row).get("category") or row.get("enr_category")
    return str(c).lower() if c else None


def final_category(row: dict) -> str | None:
    """LLM label, corrected by title rules (see category_rules.py)."""
    return fix_category(row.get("title"), _llm_category(row))


def build_document(row: dict) -> str:
    a = _attrs(row)
    lines: list[str] = []

    title = (row.get("title") or "").strip()
    if title:
        lines.append(_clip(title, 300))
    if row.get("store"):
        lines.append(f"Brand: {row['store']}")

    category = final_category(row)
    ptype = a.get("product_type")
    gender = a.get("gender") or row.get("enr_gender")
    kind = " / ".join(x for x in dict.fromkeys(str(v) for v in (category, ptype) if v))
    if kind:
        lines.append(f"Type: {kind}" + (f", for {gender}" if gender else ""))

    for label, key in (("Occasions", "occasions"), ("Seasons", "seasons"), ("Colors", "colors"),
                       ("Materials", "materials"), ("Style", "styles")):
        vals = _str_list(a.get(key))
        if vals:
            lines.append(f"{label}: {', '.join(vals)}")
    if a.get("fit"):
        lines.append(f"Fit: {a['fit']}")

    feats = [f for f in _str_list(row.get("features")) if f.lower().strip(" .") not in _JUNK_FEATURES]
    if feats:
        lines.append("Features: " + "; ".join(_clip(f, MAX_FEATURE_CHARS) for f in feats[:MAX_FEATURES]))

    kw = _str_list(a.get("search_keywords"))
    if kw:
        lines.append(f"Keywords: {', '.join(kw)}")

    summary = a.get("review_summary") or row.get("review_summary")
    if summary:
        lines.append(f"Customers say: {_clip(summary, 400)}")

    if not a:  # not enriched yet: fall back to Amazon's own description
        desc = " ".join(_str_list(row.get("description")))
        if desc:
            lines.append(f"Description: {_clip(desc, MAX_DESC_CHARS)}")

    return "\n".join(lines)


def doc_hash(document: str) -> str:
    return hashlib.sha256(f"{DOC_VERSION}\n{document}".encode("utf-8")).hexdigest()[:16]


def _lower_list(v: Any) -> list[str]:
    return [s.lower() for s in _str_list(v)]


def _num(v: Any) -> float | None:
    try:
        return None if v is None else round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _merge(llm_vals: Any, rule_vals: list[str]) -> list[str]:
    out = _lower_list(llm_vals)
    return out + [v for v in rule_vals if v not in out]


def _gender(a: dict, row: dict, rule_g: str | None) -> str | None:
    """Explicit evidence (Department field, "Men's" in the title) beats the LLM's guess."""
    if rule_g:
        return rule_g
    g = str(a.get("gender") or row.get("enr_gender") or "").lower()
    return g if g and g != "unknown" else None


def build_payload(row: dict, document: str) -> dict:
    a = _attrs(row)
    r = rule_attributes(row.get("title"), _str_list(row.get("features")), _as_obj(row.get("details"), {}))
    llm_styles = _lower_list(a.get("styles"))
    formality = r["formality"] or ("formal" if "formal" in llm_styles else None)
    payload = {
        "parent_asin": row["parent_asin"],
        "title": row.get("title"),
        "store": row.get("store"),
        "image_url": row.get("image_url"),
        "price": _num(row.get("price")),
        "category": final_category(row),
        "llm_category": _llm_category(row),  # kept for debugging/eval
        "product_type": (a.get("product_type") or "").lower() or None,
        "gender": _gender(a, row, r["gender"]),
        # LLM tags first, then rule-derived tags fill the gaps (payload only, not embedded)
        "occasions": _merge(a.get("occasions"), r["occasions"]),
        "seasons": _merge(a.get("seasons"), r["seasons"]),
        "colors": _merge(a.get("colors"), r["colors"]),
        "materials": _merge(a.get("materials"), r["materials"]),
        "styles": llm_styles,
        "formality": formality,
        "fit": a.get("fit"),
        "review_summary": a.get("review_summary") or row.get("review_summary"),
        "bayes_rating": _num(row.get("bayes_rating")),
        "avg_rating": _num(row.get("avg_rating") or row.get("amazon_avg_rating")),
        "review_count": int(row.get("review_count") or row.get("amazon_rating_count") or 0),
        "is_active": bool(row.get("is_active", True)),
        "enriched": bool(a),
        "enrich_model": row.get("enr_model"),
        "doc": document,  # Phase 4's reranker reads this text
        "doc_hash": doc_hash(document),
    }
    # Qdrant range filters simply skip points without the field, so drop Nones.
    payload = {k: v for k, v in payload.items() if v is not None}
    payload["payload_hash"] = stable_hash(payload)
    return payload
