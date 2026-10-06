"""Fix product categories using obvious words in the title.

Why: the small LLMs (and possibly a fuzzy-matching validator) mislabeled many
products, e.g. cardigans, gloves and beanies as "footwear", and dumped >1,000
products into "accessories". Product titles usually name the item plainly
("... Knitted Cardigan Sweaters"), so simple rules beat a 3B model here.

How it works:
  - Every rule is a regex for product NOUNS (shoes, cardigan, earrings...).
    Adjectives like "running" or "winter" are deliberately not rules.
  - All rules are tried; the match that appears EARLIEST in the title wins
    (Amazon titles name the product before the marketing words).
    If two matches start at the same place, the longer one wins, so
    "sports bra" beats "bra" and "shirt stays" beats "shirt".
  - Lookaheads stop known traps: "dress shoes" is not a dress,
    "sneaker socks" are not sneakers, "boot cut jeans" are not boots.
  - If no rule matches, we keep the LLM's label.
"""
from __future__ import annotations

import re

# (category, regex). Only categories from ALLOWED["category"] in common/schemas.py.
_RULES: list[tuple[str, str]] = [
    ("costumes", r"\bcostumes?\b(?!\s*jewel)|\bcosplay\b"),
    ("watches", r"\bsmart\s?watch(es)?\b|\bwatch(es)?\b(?!\s*(box|case|winder|cabinet))"),
    ("eyewear", r"\bsunglasses\b(?!\s*(case|holder|strap))|\beyeglasses\b|\bglasses\b(?!\s*(case|chain|holder|strap))"
                r"|\bgoggles\b|\beyewear\b|\bspectacles\b"),
    ("jewelry", r"\bnecklaces?\b|\bearrings?\b|\bbracelets?\b|\brings?\b(?!\s*(light|binder))|\bpendants?\b"
                r"|\banklets?\b|\bbrooch(es)?\b|\bcharms?\b|\bchokers?\b|\bbangles?\b|\bjewel(le)?ry\b"),
    ("bags", r"\bbags?\b|\bbackpacks?\b|\bhandbags?\b|\bpurses?\b|\bwallets?\b|\btotes?\b|\bclutch(es)?\b"
             r"|\bcrossbody\b|\bsatchels?\b|\bfanny packs?\b|\bbelt bags?\b|\bwaist packs?\b|\bduffel\b"
             r"|\bluggage\b|\bbriefcases?\b"),
    ("swimwear", r"\bswim(suits?|wear)\b|\bswim\s+(trunks|shorts|briefs|dress|skirt|tops?|bottoms?|shirt|jammers?)\b"
                 r"|\bbikinis?\b|\btankinis?\b|\bboard\s?shorts\b|\brash guards?\b|\bbathing suits?\b"
                 r"|\bcover[\s-]?ups?\b"),
    ("sleepwear_loungewear", r"\bpaj?jamas?\b|\bpyjamas?\b|\bsleepwear\b|\bnight\s?gowns?\b|\bnightshirts?\b"
                             r"|\bnight\s?dress(es)?\b|\bnighties?\b|\b(bath\s?)?robes?\b|\bkimono robes?\b"
                             r"|\bloungewear\b|\blounge (set|pants|shorts)\b|\bonesies?\b|\bsleep (shirt|shorts|pants|set)s?\b"),
    ("underwear_socks", r"\bsocks?\b|\bunderwear\b|\bboxers?\b|\bbriefs\b|\bbras?\b|\bbralettes?\b|\bpant(y|ies)\b"
                        r"|\bthongs?\b|\blingerie\b|\bshapewear\b|\bundershirts?\b|\btights\b|\bpantyhose\b"
                        r"|\bstockings\b|\bleg warmers?\b|\b(calf|leg|compression)\s+(tube\s+)?sleeves?\b"
                        r"|\blong johns\b|\bgarters?\b"),
    ("activewear", r"\bsports?\s+bras?\b|\byoga (pants|shorts|leggings)\b|\btrack\s?suits?\b"
                   r"|\btrack (pants|jackets?)\b|\bathletic (shorts|pants)\b|\bbike shorts\b"
                   r"|\bcompression (shirts?|shorts|tights|leggings|pants)\b|\bcycling (jerseys?|shorts)\b"),
    ("suits_formalwear", r"\bsuits?\b(?!\s*case)|\bpants?\s?suits?\b|\bsuit sets?\b"
                         r"|\btuxedos?\b(?!\s*(shirts?|cufflinks?|studs?|bow|ties?|shoes?|pants|trousers))"
                         r"|\bblazers?\b|\bsport\s?coats?\b|\bwaistcoats?\b"),
    # "suits" that aren't clothing suits: these longer phrases win over plain "suit"
    ("other", r"\bbee(keep\w*)?\s+suits?\b|\bbeekeep\w*\b|\bsauna suits?\b|\bhazmat\b|\bcoveralls?\b"
              r"|\bspace suits?\b"),
    ("outerwear", r"\b(snow|rain|ski)\s?suits?\b"),
    ("swimwear", r"\b(wet|dry)\s?suits?\b"),
    ("outerwear", r"\bjackets?\b|\bcoats?\b|\bparkas?\b|\bwindbreakers?\b|\braincoats?\b|\bpuffer\b|\banoraks?\b"
                  r"|\bponchos?\b|\bpea\s?coats?\b|\btrench\b|\bvests?\b"),
    ("headwear", r"\bhats?\b|\bbeanies?\b|\bcaps?\b(?!\s*(sleeves?|toe))|\bvisors?\b|\berets?\b|\bfedoras?\b"
                 r"|\bhead\s?wraps?\b|\bturbans?\b|\bheadbands?\b|\bbandanas?\b|\bbalaclavas?\b"
                 r"|\bear\s?muffs?\b|\bear\s?warmers?\b|\bdurags?\b|\bdo-?rags?\b"),
    ("accessories", r"\bgloves?\b|\bmittens?\b|\bscar(f|ves)\b|\bbelts?\b|\bsuspenders\b|\bneck\s?ties?\b"
                    r"|\bbow\s?ties?\b|\btie (clips?|bars?)\b|\bcufflinks?\b|\bumbrellas?\b|\bkey\s?chains?\b"
                    r"|\bkey\s?rings?\b|\bhair (clips?|ties|bands?|pins?|accessories)\b|\bscrunchies?\b"
                    r"|\bshirt stays?\b|\bshoe\s?laces?\b|\bshoe horns?\b|\bneck gaiters?\b|\bface masks?\b"
                    r"|\bhandkerchiefs?\b|\bpocket squares?\b|\blanyards?\b|\bwristbands?\b|\barm sleeves?\b"),
    ("dresses", r"\bdress(es)?\b(?![\s-]*(shoes?|shirts?|socks?|pants|slacks|boots?|belts?|watch|sandals?|up\b|tuxedos?|suits?|vests?|coats?|jackets?|blazers?))"
                r"|\bgowns?\b|\bsundress(es)?\b|\bjumpsuits?\b|\brompers?\b"),
    ("bottoms", r"\bjeans\b|\bpants\b|\btrousers\b|\bshorts\b|\bleggings\b|\bskirts?\b|\bjoggers\b|\bchinos\b"
                r"|\bcapris?\b|\bculottes\b|\bsweatpants\b|\bboot\s?cut\b|\boveralls\b|\bslacks\b"),
    ("tops", r"(\bt-?shirts?\b|\btees?\b|\bshirts?\b|\bblouses?\b|\b(tank|crop|tube|halter)\s?tops?\b"
             r"|\btops?\s+for\b|\btunics?\b|\bsweaters?\b|\bsweatshirts?\b|\bhoodies?\b|\bcardigans?\b"
             r"|\bpullovers?\b|\bpolos?\b|\bcamisoles?\b|\bcamis?\b|\bhenleys?\b|\bbodysuits?\b"
             r"|\bturtlenecks?\b)(?![\s-]+(dress|stays?))"),
    ("footwear", r"(\bshoes?\b|\bsneakers?\b|\bboots?\b|\bbooties\b|\bsandals?\b|\bslippers?\b|\bclogs?\b"
                 r"|\bloafers?\b|\bheels\b|\bpumps\b|\bflip[\s-]?flops?\b|\bslides\b|\bmules\b|\bmoccasins?\b"
                 r"|\bespadrilles\b|\boxfords\b|\bflats\b|\bwedges\b|\bcleats\b|\binsoles?\b)"
                 r"(?![\s-]*(socks?|horns?|laces?|bags?|racks?|trees?|stretchers?|covers?|cut\b|organizers?|cleaners?))"),
]

_COMPILED = [(cat, re.compile(rx, re.IGNORECASE)) for cat, rx in _RULES]


def rule_category(title: str | None) -> str | None:
    """Category implied by the title, or None if no rule matches."""
    if not title:
        return None
    best: tuple[int, int, str] | None = None  # (start, -length, category)
    for cat, rx in _COMPILED:
        m = rx.search(title)
        if m:
            key = (m.start(), -(m.end() - m.start()), cat)
            if best is None or key < best:
                best = key
    return best[2] if best else None


def fix_category(title: str | None, llm_category: str | None) -> str | None:
    """Title rule wins when it matches; otherwise keep the LLM's label."""
    return rule_category(title) or (llm_category.lower() if llm_category else None)
