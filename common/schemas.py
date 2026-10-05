"""The shape of LLM-extracted product attributes.

Allowed values live here once and are used in two places:
  1. the enrichment prompt lists them, so the LLM knows the vocabulary
  2. the validators clean the LLM's answer, so small slips ("Fall", "gray",
     "mens") are mapped to the right value instead of creating new ones
"""
import re

from pydantic import BaseModel, field_validator

CATEGORIES = (
    "tops", "bottoms", "dresses", "outerwear", "activewear", "swimwear",
    "sleepwear_loungewear", "underwear_socks", "suits_formalwear", "footwear",
    "bags", "jewelry", "watches", "eyewear", "headwear", "accessories", "costumes", "other",
)
GENDERS = ("men", "women", "unisex", "kids", "unknown")
OCCASIONS = (
    "casual", "work_office", "formal", "party", "wedding", "date_night", "beach", "pool",
    "vacation", "travel", "outdoor_hiking", "gym_workout", "running", "sports",
    "lounge_home", "sleep", "school", "festival",
)
SEASONS = ("spring", "summer", "autumn", "winter", "all_season")
COLORS = (
    "black", "white", "grey", "navy", "blue", "red", "pink", "purple", "green", "yellow",
    "orange", "brown", "beige", "gold", "silver", "multicolor",
)
STYLES = (
    "casual", "classic", "sporty", "streetwear", "bohemian", "elegant", "minimalist",
    "vintage", "trendy", "business", "outdoor", "cute",
)
FITS = ("slim", "regular", "relaxed", "oversized", "unknown")
SIZING = ("runs_small", "true_to_size", "runs_large", "mixed", "unknown")

ALLOWED = {
    "category": CATEGORIES, "gender": GENDERS, "occasions": OCCASIONS, "seasons": SEASONS,
    "colors": COLORS, "styles": STYLES, "fit": FITS, "sizing": SIZING,
}

# Common LLM variations -> our canonical value
SYNONYMS = {
    "fall": "autumn", "all season": "all_season", "year_round": "all_season",
    "gray": "grey", "multi": "multicolor", "multicolour": "multicolor", "multi_color": "multicolor",
    "khaki": "beige", "tan": "beige", "cream": "white", "ivory": "white", "maroon": "red",
    "burgundy": "red", "teal": "green", "olive": "green", "navy_blue": "navy", "dark_blue": "navy",
    "light_blue": "blue", "rose_gold": "gold",
    "male": "men", "mens": "men", "man": "men", "female": "women", "womens": "women",
    "woman": "women", "ladies": "women", "boys": "kids", "girls": "kids", "children": "kids",
    "office": "work_office", "work": "work_office", "gym": "gym_workout", "workout": "gym_workout",
    "hiking": "outdoor_hiking", "home": "lounge_home", "lounge": "lounge_home",
    "true_to_fit": "true_to_size", "fits_true_to_size": "true_to_size",
    "small": "runs_small", "large": "runs_large",
}


def _norm(value) -> str:
    s = str(value).strip().lower().replace("'", "")
    s = re.sub(r"[\s\-/]+", "_", s)
    return SYNONYMS.get(s, s)


def _one(value, allowed, default) -> str:
    s = _norm(value) if value else ""
    return s if s in allowed else default


def _many(value, allowed=None, max_items=6, max_len=40) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    out = []
    for v in value:
        s = _norm(v) if allowed else str(v).strip().lower()[:max_len]
        if s and (allowed is None or s in allowed) and s not in out:
            out.append(s)
    return out[:max_items]


class ProductAttributes(BaseModel):
    category: str = "other"
    product_type: str = "unknown"
    gender: str = "unknown"
    occasions: list[str] = []
    seasons: list[str] = []
    colors: list[str] = []
    materials: list[str] = []
    styles: list[str] = []
    fit: str = "unknown"
    sizing: str = "unknown"
    search_keywords: list[str] = []
    review_summary: str = ""

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return _one(v, CATEGORIES, "other")

    @field_validator("gender", mode="before")
    @classmethod
    def _gender(cls, v):
        return _one(v, GENDERS, "unknown")

    @field_validator("fit", mode="before")
    @classmethod
    def _fit(cls, v):
        return _one(v, FITS, "unknown")

    @field_validator("sizing", mode="before")
    @classmethod
    def _sizing(cls, v):
        return _one(v, SIZING, "unknown")

    @field_validator("occasions", mode="before")
    @classmethod
    def _occasions(cls, v):
        return _many(v, OCCASIONS, max_items=8)

    @field_validator("seasons", mode="before")
    @classmethod
    def _seasons(cls, v):
        s = _many(v, SEASONS, max_items=5)
        return ["all_season"] if "all_season" in s else s

    @field_validator("colors", mode="before")
    @classmethod
    def _colors(cls, v):
        return _many(v, COLORS, max_items=6)

    @field_validator("styles", mode="before")
    @classmethod
    def _styles(cls, v):
        return _many(v, STYLES, max_items=4)

    @field_validator("materials", mode="before")
    @classmethod
    def _materials(cls, v):
        return _many(v, None, max_items=5, max_len=30)

    @field_validator("search_keywords", mode="before")
    @classmethod
    def _keywords(cls, v):
        return _many(v, None, max_items=8, max_len=40)

    @field_validator("product_type", mode="before")
    @classmethod
    def _ptype(cls, v):
        s = re.sub(r"[^a-z0-9]+", "_", str(v or "").lower()).strip("_")[:40]
        return s or "unknown"

    @field_validator("review_summary", mode="before")
    @classmethod
    def _summary(cls, v):
        return str(v or "").strip()[:400]
