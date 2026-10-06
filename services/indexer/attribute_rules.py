"""Rule-based enrichment: derive attributes from title, features and Amazon's
"Department" field. No LLM, runs on 25k products in seconds.

Used for products the LLM never enriched, and to fill gaps the LLM left empty.
These values only go into the PAYLOAD (filters, boosts, ranking), never into the
embedded text, so adding or changing rules never forces a re-embedding.

  gender     Department field first ("Mens", "Womens", "Unisex-adult", "Girls"...), then title
  colors     color words in the title
  materials  material words in title + features
  seasons    explicit ("winter") or implied ("fleece" -> winter, "linen" -> summer)
  occasions  explicit or implied ("tuxedo" -> formal/wedding, "yoga" -> sports)
  formality  "formal" / "casual" / None, from style words in the title
"""
from __future__ import annotations

import re
from typing import Any

try:
    from common.schemas import ALLOWED
except Exception:  # pragma: no cover
    ALLOWED = {}

KIDS_GENDERS = ("kids", "girls", "boys", "baby")


def _rx(*words: str) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.IGNORECASE)


# ------------------------------------------------------------------ gender

_DEPT_GENDER = [
    (re.compile(r"baby|infant|toddler|newborn", re.I), "kids"),
    (re.compile(r"\bgirl", re.I), "kids"),
    (re.compile(r"\bboy", re.I), "kids"),
    (re.compile(r"kid|child|youth", re.I), "kids"),
    (re.compile(r"unisex", re.I), "unisex"),
    (re.compile(r"women|womens|ladies|female", re.I), "women"),
    (re.compile(r"\bmen|\bmens|\bmale", re.I), "men"),
]
_T_KIDS = _rx(r"boys?'?", r"girls?'?", r"kids?'?", r"toddlers?", r"baby", r"babies", r"infants?",
              r"newborns?", r"youth", r"little (?:boys?|girls?)", r"big (?:boys?|girls?)", r"children'?s?")
_T_WOMEN = _rx(r"women'?s?", r"womens", r"ladies'?", r"woman'?s?", r"female", r"maternity")
_T_MEN = _rx(r"men'?s?", r"mens", r"male", r"man'?s")
_T_UNISEX = _rx(r"unisex", r"men and women", r"women and men", r"men & women", r"women & men")


def rule_gender(title: str, details: dict | None) -> str | None:
    dept = str((details or {}).get("Department") or (details or {}).get("department") or "")
    if dept:
        for rx, g in _DEPT_GENDER:
            if rx.search(dept):
                return g
    t = title or ""
    if _T_KIDS.search(t):
        return "kids"
    if _T_UNISEX.search(t):
        return "unisex"
    w, m = bool(_T_WOMEN.search(t)), bool(_T_MEN.search(t))
    if w and m:
        return "unisex"
    if w:
        return "women"
    if m:
        return "men"
    return None


# ------------------------------------------------------------------ colors / materials

_COLORS = {
    "black": r"black", "white": r"white", "navy": r"navy", "blue": r"blue", "red": r"red",
    "green": r"green", "grey": r"gr[ae]y", "pink": r"pink", "purple": r"purple|violet|lavender",
    "yellow": r"yellow|mustard", "orange": r"orange", "brown": r"brown|chocolate|coffee",
    "beige": r"beige|nude|camel", "khaki": r"khaki", "ivory": r"ivory|cream|off[- ]white",
    "gold": r"gold(?:en)?", "silver": r"silver", "burgundy": r"burgundy|maroon|wine red",
    "olive": r"olive", "teal": r"teal|turquoise", "tan": r"tan",
}
_COLOR_RX = {c: re.compile(r"\b(" + rx + r")\b", re.I) for c, rx in _COLORS.items()}

_MATERIALS = ["cotton", "linen", "wool", "merino", "cashmere", "silk", "satin", "chiffon", "polyester",
              "nylon", "spandex", "denim", "leather", "suede", "fleece", "velvet", "lace", "knit",
              "rayon", "bamboo", "canvas", "mesh", "corduroy", "flannel", "jersey", "modal"]
_MATERIAL_RX = _rx(*_MATERIALS)


# ------------------------------------------------------------------ seasons / occasions / formality

# concept -> (title/feature words, candidate names in your vocabulary, first match wins)
_SEASON_RULES = {
    "summer": (_rx("summer", "swim\\w*", "beach\\w*", "linen", "sandals?", "sleeveless", "tank tops?",
                   "upf ?\\d*", "uv protection", "cooling", "bikinis?", "sundress(?:es)?", "shorts"),
               ["summer"]),
    "winter": (_rx("winter", "thermal", "fleece", "puffer", "down jacket", "insulated", "snow", "ski",
                   "beanies?", "gloves?", "mittens?", "earmuffs?", "parkas?", "wool", "sherpa", "cold weather"),
               ["winter"]),
}
_OCCASION_RULES = {
    "formal": (_rx("formal", "tuxedos?", "blazers?", "suits?", "waistcoats?", "dress shirts?", "dress pants",
                   "slacks", "bow ?ties?", "neck ?ties?", "cufflinks?", "evening gowns?", "gowns?", "oxfords?"),
               ["formal"]),
    "office": (_rx("office", "business", "work pants", "career", "professional", "dress pants", "slacks",
                   "blazers?", "pencil skirts?", "button[- ]down", "chinos?"),
               ["work_office", "office", "work"]),
    "wedding": (_rx("wedding", "bridal", "bridesmaid", "tuxedos?", "groom\\w*"), ["wedding"]),
    "party": (_rx("party", "cocktail", "prom", "club\\w*", "night out", "sequin\\w*"),
              ["party", "party_night_out", "night_out"]),
    "beach": (_rx("beach\\w*", "swim\\w*", "bikinis?", "cover[- ]?ups?", "sarongs?", "flip[- ]?flops?"),
              ["beach", "beach_vacation", "vacation"]),
    "sports": (_rx("running", "gym", "workout", "yoga", "athletic", "training", "sports?", "fitness",
                   "cycling", "hiking", "jogging", "compression"),
               ["sports", "gym_workout", "athletic", "workout", "gym", "sport"]),
    "outdoor": (_rx("hiking", "camping", "outdoor", "trekking", "fishing", "hunting"),
                ["outdoor", "outdoor_hiking", "hiking"]),
    "sleep": (_rx("pajamas?", "pyjamas?", "sleepwear", "nightgowns?", "loungewear", "robes?"),
              ["lounge_sleep", "sleep", "lounge", "home"]),
}
_FORMAL_WORDS = _rx("formal", "blazers?", "suits?", "tuxedos?", "waistcoats?", "dress shirts?", "dress pants",
                    "slacks", "tailored", "business", "office", "oxfords?", "loafers?", "neck ?ties?", "bow ?ties?",
                    "cufflinks?", "pencil skirts?", "sheath", "chinos?", "gowns?", "work pants", "trouser suits?",
                    "derby", "brogues?", "pumps")
_CASUAL_WORDS = _rx("t-?shirts?", "tees?", "graphic", "hoodies?", "hooded", "sweatshirts?", "joggers?",
                    "sweatpants", "cargo", "punk", "hip ?hop", "distressed", "ripped", "tie[- ]?dye", "costumes?",
                    "cosplay", "pajamas?", "flip[- ]?flops?", "slides", "crocs", "tank tops?", "crop tops?",
                    "athletic", "workout", "gym", "running", "yoga", "shorts", "hawaiian", "funny", "novelty",
                    "halloween", "sweatsuits?", "tracksuits?", "camo\\w*", "skull", "goth\\w*", "streetwear",
                    "christmas", "xmas", "ugly", "light[- ]?up", "sequins?", "sequined", "disco", "glitter",
                    "beekeep\\w*", "sauna")


def _vocab_name(key: str, candidates: list[str]) -> str:
    """Map a rule concept to the name your ALLOWED vocabulary uses (e.g. 'work_office')."""
    vocab = ALLOWED.get(key) if isinstance(ALLOWED, dict) else None
    if vocab:
        for c in candidates:
            if c in vocab:
                return c
    return candidates[0]


_SEASON_NAMES = {k: _vocab_name("seasons", v[1]) for k, v in _SEASON_RULES.items()}
_OCCASION_NAMES = {k: _vocab_name("occasions", v[1]) for k, v in _OCCASION_RULES.items()}


def rule_formality(title: str) -> str | None:
    t = title or ""
    f, c = len(_FORMAL_WORDS.findall(t)), len(_CASUAL_WORDS.findall(t))
    if f > c:
        return "formal"
    if c > 0:
        return "casual"
    return None


def rule_attributes(title: str | None, features: list[str] | None = None,
                    details: dict | None = None) -> dict[str, Any]:
    title = title or ""
    text = title + " " + " ".join(features or [])[:1500]
    seasons = [_SEASON_NAMES[k] for k, (rx, _) in _SEASON_RULES.items() if rx.search(title)]
    occasions = [_OCCASION_NAMES[k] for k, (rx, _) in _OCCASION_RULES.items() if rx.search(title)]
    return {
        "gender": rule_gender(title, details),
        "colors": [c for c, rx in _COLOR_RX.items() if rx.search(title)][:4],
        "materials": sorted({m.lower() for m in _MATERIAL_RX.findall(text)})[:4],
        "seasons": seasons,
        "occasions": occasions,
        "formality": rule_formality(title),
    }
