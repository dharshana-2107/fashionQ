"""LLM query parser: any-language query -> structured search plan.

"கோடை கடற்கரைக்கு ஏற்ற ஆடை" ->
  {"english": "clothes for the summer beach", "seasons": ["summer"], "occasions": ["beach"],
   "slots": [{"name": "outfit", "query": "light summer beach dress or top", "category": null}]}

- Slots: an outfit request becomes several searches (top, bottom, footwear...).
- Filters: gender / price become hard Qdrant filters; occasion/season/color are
  only soft boosts (enrichment labels are noisy, hard filters would hide good items).
- Every result is cached in Redis, so the same query never costs a second LLM call.
- If the LLM fails or returns junk, parse_query returns None and search falls back
  to plain hybrid search on the raw query. The demo never crashes because of the LLM.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from pydantic import BaseModel, field_validator

from common.config import settings

try:  # reuse your fence-stripping JSON parser from Phase 2
    from common.llm import parse_json as _parse_json
except Exception:  # pragma: no cover
    _parse_json = None

log = logging.getLogger(__name__)

PARSER_VERSION = "p1"  # bump to invalidate cached parses after prompt changes
CACHE_TTL_S = 7 * 24 * 3600
MAX_SLOTS = 4
MAX_TOKENS = 2000       # generous: "thinking" models (Nemotron) reason before answering
TIMEOUT_S = 45

# OpenRouter only: if a model is busy (429) OpenRouter automatically tries the next.
# Your LLM_MODEL from .env always goes first. Edit this list if free models change
# (list them with: curl -s https://openrouter.ai/api/v1/models | grep ':free').
OPENROUTER_FALLBACKS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

try:
    from common.schemas import ALLOWED
    CATEGORIES = tuple(ALLOWED["category"])
except Exception:  # keep working even if schemas changes
    ALLOWED = {}
    CATEGORIES = ("tops", "bottoms", "dresses", "outerwear", "activewear", "swimwear",
                  "sleepwear_loungewear", "underwear_socks", "suits_formalwear", "footwear",
                  "bags", "jewelry", "watches", "eyewear", "headwear", "accessories",
                  "costumes", "other")
GENDERS = ("women", "men", "unisex", "kids", "girls", "boys")


def _vocab(key: str) -> str:
    vals = ALLOWED.get(key) if isinstance(ALLOWED, dict) else None
    return f" (use these words if possible: {', '.join(vals)})" if vals else ""


SYSTEM_PROMPT = f"""You turn fashion shopping queries (in ANY language, e.g. English, Tamil, Hindi) into a search plan.
Reply with ONLY one JSON object, no prose:
{{
  "english": "<the query translated to natural English>",
  "language": "<language of the query, e.g. en, ta, hi>",
  "gender": "<one of {', '.join(GENDERS)}, or null if not stated or clearly implied>",
  "min_price": <number or null>, "max_price": <number or null>,
  "occasions": ["<occasion words>"]{_vocab('occasions')},
  "seasons": ["<season words>"]{_vocab('seasons')},
  "colors": ["<colors mentioned>"],
  "slots": [{{"name": "<short label>", "query": "<English search text for this item>", "category": "<one of: {', '.join(CATEGORIES)}, or null>"}}]
}}
Rules:
- A single item request ("red sneakers") -> exactly 1 slot.
- An outfit / look / "what to wear" request -> 2 to 4 slots (e.g. top + bottom OR dress, footwear, one accessory).
- Slot queries are short, concrete, in English, and include useful context (material, style, occasion).
- Never invent a price or gender the user did not give or clearly imply.

Example: "red running shoes for women under 50 dollars" ->
{{"english": "red running shoes for women under $50", "language": "en", "gender": "women", "min_price": null, "max_price": 50, "occasions": ["sports"], "seasons": [], "colors": ["red"], "slots": [{{"name": "shoes", "query": "red women's running shoes", "category": "footwear"}}]}}

Example: "शादी के लिए पुरुषों का आउटफिट" ->
{{"english": "outfit for a wedding for men", "language": "hi", "gender": "men", "min_price": null, "max_price": null, "occasions": ["wedding"], "seasons": [], "colors": [], "slots": [{{"name": "top", "query": "men's formal dress shirt for a wedding", "category": "tops"}}, {{"name": "bottom", "query": "men's formal dress pants", "category": "bottoms"}}, {{"name": "shoes", "query": "men's leather dress shoes", "category": "footwear"}}]}}
"""


class Slot(BaseModel):
    name: str = "results"
    query: str
    category: str | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _cat(cls, v):
        v = str(v).strip().lower().replace(" ", "_") if v else None
        return v if v in CATEGORIES else None


class ParsedQuery(BaseModel):
    english: str
    language: str | None = None
    gender: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    occasions: list[str] = []
    seasons: list[str] = []
    colors: list[str] = []
    slots: list[Slot] = []

    @field_validator("gender", mode="before")
    @classmethod
    def _gender(cls, v):
        v = str(v).strip().lower() if v else None
        aliases = {"woman": "women", "female": "women", "ladies": "women", "man": "men",
                   "male": "men", "child": "kids", "children": "kids", "girl": "girls", "boy": "boys"}
        v = aliases.get(v, v)
        return v if v in GENDERS else None

    @field_validator("min_price", "max_price", mode="before")
    @classmethod
    def _price(cls, v):
        try:
            v = float(str(v).replace("$", "").replace(",", "")) if v not in (None, "", "null") else None
        except ValueError:
            return None
        return v if v and v > 0 else None

    @field_validator("occasions", "seasons", "colors", mode="before")
    @classmethod
    def _lists(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip().lower() for x in v if str(x).strip()][:6]


def _validate(data: Any, raw_query: str) -> ParsedQuery:
    if isinstance(data, str):
        data = json.loads(data)
    pq = ParsedQuery.model_validate(data)
    pq.slots = [s for s in pq.slots if s.query.strip()][:MAX_SLOTS]
    if not pq.slots:  # LLM forgot slots: search the translation as one slot
        pq.slots = [Slot(name="results", query=pq.english or raw_query)]
    if pq.min_price and pq.max_price and pq.min_price > pq.max_price:
        pq.min_price, pq.max_price = pq.max_price, pq.min_price
    return pq


# ------------------------------------------------------------------ cache

_redis = None
_redis_checked = False


def _cache():
    global _redis, _redis_checked
    if not _redis_checked:
        _redis_checked = True
        try:
            import redis
            r = redis.Redis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1)
            r.ping()
            _redis = r
        except Exception as e:
            log.warning("Redis cache disabled: %s", e)
    return _redis


def _cache_key(query: str) -> str:
    model = getattr(settings, "llm_model", "llm")
    h = hashlib.sha256(" ".join(query.lower().split()).encode()).hexdigest()[:24]
    return f"qparse:{PARSER_VERSION}:{model}:{h}"


# ------------------------------------------------------------------ LLM call

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "none",
                         timeout=TIMEOUT_S, max_retries=0)
    return _client


def _model_chain() -> list[str]:
    chain = [settings.llm_model] + [m for m in OPENROUTER_FALLBACKS if m != settings.llm_model]
    return chain[:3]  # OpenRouter accepts a short fallback list


def _call_llm(query: str) -> tuple[str, str]:
    """One chat call. Returns (reply text, model that answered). Retries once on 429."""
    is_openrouter = "openrouter.ai" in str(settings.llm_base_url)
    kwargs: dict = dict(
        model=settings.llm_model, temperature=0, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}],
    )
    if getattr(settings, "llm_json_mode", True):
        kwargs["response_format"] = {"type": "json_object"}
    if is_openrouter:
        kwargs["extra_body"] = {"models": _model_chain(), "reasoning": {"effort": "low"}}
    elif getattr(settings, "llm_reasoning_effort", None):
        kwargs["reasoning_effort"] = settings.llm_reasoning_effort

    from openai import BadRequestError, RateLimitError
    rate_limited = 0
    while True:
        try:
            resp = _get_client().chat.completions.create(**kwargs)
            break
        except RateLimitError:
            rate_limited += 1
            if rate_limited > 1:
                raise
            time.sleep(2)
        except BadRequestError:
            if "response_format" not in kwargs:
                raise
            kwargs.pop("response_format")  # model without JSON mode: rely on the prompt
    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    if not content:
        raise ValueError(f"empty reply (finish_reason={choice.finish_reason})")
    return content, getattr(resp, "model", None) or settings.llm_model


def _to_json(content: str) -> Any:
    if _parse_json is not None:
        try:
            return _parse_json(content)
        except Exception:
            pass
    text = content.strip()
    if "```" in text:
        text = text.split("```")[1].removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# ------------------------------------------------------------------ main

def parse_query(query: str, use_cache: bool = True) -> tuple[ParsedQuery | None, dict]:
    """Returns (parsed or None, info). info = {source, ms, error?}."""
    t0 = time.time()
    r = _cache() if use_cache else None
    key = _cache_key(query)
    if r is not None:
        try:
            hit = r.get(key)
            if hit:
                return _validate(json.loads(hit), query), {"source": "cache", "ms": round((time.time() - t0) * 1000)}
        except Exception as e:
            log.warning("cache read failed: %s", e)
    try:
        content, model_used = _call_llm(query)
        pq = _validate(_to_json(content), query)
    except Exception as e:  # rate limits, timeouts, bad JSON, validation errors...
        log.warning("query parse failed, falling back to plain search: %s", e)
        return None, {"source": "fallback", "ms": round((time.time() - t0) * 1000), "error": str(e)[:300]}
    if r is not None:
        try:
            r.set(key, pq.model_dump_json(), ex=CACHE_TTL_S)
        except Exception as e:
            log.warning("cache write failed: %s", e)
    return pq, {"source": "llm", "model": model_used, "ms": round((time.time() - t0) * 1000)}
