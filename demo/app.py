"""FashionQ demo page. Talks to the search service over HTTP.

  uvicorn services.search.api:app --port 8000     # terminal 1
  streamlit run demo/app.py                       # terminal 2
"""
from __future__ import annotations

import os

import httpx
import streamlit as st

API_URL = os.environ.get("FASHIONQ_API", "http://localhost:8000")

EXAMPLES = [
    ("Beach outfit", "outfit for the beach this summer"),
    ("Tamil", "கோடை கடற்கரைக்கு ஏற்ற ஆடை"),
    ("Hindi", "सर्दियों के लिए गर्म जैकेट"),
    ("Wedding, under $60", "outfit for a summer wedding for men under $60"),
    ("All-day comfort", "comfortable shoes for standing all day at work"),
]
LANG_NAMES = {"en": "English", "ta": "Tamil", "hi": "Hindi"}

st.set_page_config(page_title="FashionQ", page_icon="🧵", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;600;700&family=Noto+Sans+Tamil:wght@400;600&family=Noto+Sans+Devanagari:wght@400;600&display=swap');
:root { --ink:#1F2A44; --muted:#5B6478; --marigold:#E8A317; --marigold-soft:#FFF4DC; --line:#E3E6EE; }
html, body, [class*="css"], .stMarkdown, input, button {
  font-family: 'Noto Sans','Noto Sans Tamil','Noto Sans Devanagari',system-ui,sans-serif; }
h1.fq-title { color:var(--ink); font-weight:700; font-size:2.4rem; margin:0 0 .2rem 0; letter-spacing:-.01em; }
p.fq-sub { color:var(--muted); font-size:1.05rem; margin:0 0 1rem 0; }
.fq-understood { border-left:4px solid var(--marigold); background:var(--marigold-soft);
  padding:.9rem 1.1rem; border-radius:6px; margin:.6rem 0 1.2rem 0; color:var(--ink); }
.fq-understood .en { font-size:1.15rem; font-weight:600; margin-bottom:.4rem; }
.fq-chip { display:inline-block; border:1px solid var(--ink); border-radius:999px; padding:.05rem .6rem;
  margin:.15rem .3rem .15rem 0; font-size:.85rem; color:var(--ink); background:#fff; }
.fq-meta { color:var(--muted); font-size:.82rem; }
.fq-title-txt { font-weight:600; font-size:.92rem; color:var(--ink); line-height:1.3;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; min-height:2.4em; }
.fq-price { font-weight:700; color:var(--ink); }
.fq-new { background:var(--marigold); color:var(--ink); font-weight:700; font-size:.72rem;
  border-radius:4px; padding:.05rem .4rem; margin-left:.3rem; }
.fq-img { width:100%; aspect-ratio:1/1; object-fit:contain; background:#fff; border:1px solid var(--line); border-radius:6px; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=10, show_spinner=False)
def api_health() -> dict | None:
    try:
        return httpx.get(f"{API_URL}/health", timeout=3).json()
    except Exception:
        return None


def api_search(q: str, k: int, llm: bool, rerank: bool) -> dict:
    r = httpx.get(f"{API_URL}/search", params={"q": q, "k": k, "llm": llm, "rerank": rerank}, timeout=90)
    r.raise_for_status()
    return r.json()


def esc(s) -> str:
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Settings")
    use_llm = st.toggle("Understand the query with the LLM", value=True,
                        help="Off = plain hybrid search on the raw text")
    use_rerank = st.toggle("Rerank results", value=True, help="Cross-encoder re-scoring of candidates")
    k = st.slider("Items per group", 3, 12, 6)
    st.divider()
    health = api_health()
    if health:
        st.caption(f"{health['products']:,} products indexed")
        st.caption(f"LLM: {health.get('llm_model')}")
    else:
        st.error("Search service isn't running. Start it with:\n\n"
                 "`uvicorn services.search.api:app --port 8000`")

# ---------------------------------------------------------------- header + input
st.markdown('<h1 class="fq-title">FashionQ</h1>', unsafe_allow_html=True)
st.markdown('<p class="fq-sub">Describe what you need in English, தமிழ் or हिन्दी. '
            'Ask for one item or a whole outfit.</p>', unsafe_allow_html=True)

if "q" not in st.session_state:
    st.session_state.q = ""
cols = st.columns(len(EXAMPLES))
for col, (label, text) in zip(cols, EXAMPLES):
    if col.button(label, use_container_width=True):
        st.session_state.q = text

query = st.text_input("Search", key="q", placeholder="e.g. outfit for the beach this summer",
                      label_visibility="collapsed")

if not query:
    st.info("Type a query or pick an example above.")
    st.stop()

try:
    with st.spinner("Searching…"):
        res = api_search(query, k, use_llm, use_rerank)
except httpx.ConnectError:
    st.error("Can't reach the search service at " + API_URL +
             ". Start it with `uvicorn services.search.api:app --port 8000`.")
    st.stop()
except Exception as e:
    st.error(f"Search failed: {e}")
    st.stop()

# ---------------------------------------------------------------- what the search understood
parsed, info = res.get("parsed"), res.get("parse", {})
if parsed:
    chips = []
    lang = parsed.get("language")
    if lang:
        chips.append(f"language: {LANG_NAMES.get(lang, lang)}")
    if parsed.get("gender"):
        chips.append(f"for {parsed['gender']}")
    if parsed.get("max_price"):
        chips.append(f"under ${parsed['max_price']:g}")
    if parsed.get("min_price"):
        chips.append(f"over ${parsed['min_price']:g}")
    for key in ("occasions", "seasons", "colors"):
        chips += parsed.get(key) or []
    chips += [f"look for: {s['name']}" for s in parsed.get("slots", [])]
    src = {"cache": "from cache", "llm": f"parsed by {info.get('model', 'LLM')}"}.get(info.get("source"), "")
    st.markdown(
        '<div class="fq-understood"><div class="en">“' + esc(parsed.get("english")) + '”</div>'
        + "".join(f'<span class="fq-chip">{esc(c)}</span>' for c in chips)
        + f'<div class="fq-meta" style="margin-top:.4rem">{esc(src)} in {info.get("ms", 0)} ms</div></div>',
        unsafe_allow_html=True)
elif info.get("source") == "fallback":
    st.warning("The LLM was unavailable, so these are plain search results for your exact words.")

# ---------------------------------------------------------------- results
for slot in res.get("slots", []):
    if len(res["slots"]) > 1 or parsed:
        st.markdown(f"#### {esc(slot['name']).capitalize()}")
        note = f"searched for “{slot['query']}”"
        if slot.get("relaxed"):
            note += "; loosened " + ", ".join(slot["relaxed"]) + " to find enough items"
        st.markdown(f'<div class="fq-meta">{esc(note)}</div>', unsafe_allow_html=True)
    items = slot.get("results", [])
    if not items:
        st.write("Nothing matched this part of the request.")
        continue
    per_row = 6
    for row_start in range(0, len(items), per_row):
        row = st.columns(per_row)
        for col, it in zip(row, items[row_start:row_start + per_row]):
            with col:
                if it.get("image_url"):
                    st.markdown(f'<img class="fq-img" src="{esc(it["image_url"])}" alt="">',
                                unsafe_allow_html=True)
                new = '<span class="fq-new">NEW</span>' if it.get("is_new") else ""
                st.markdown(f'<div class="fq-title-txt">{esc(it.get("title"))}{new}</div>',
                            unsafe_allow_html=True)
                price = f'<span class="fq-price">${it["price"]:.2f}</span>  ' if it.get("price") else ""
                rating = (f'★ {it["avg_rating"]:.1f} ({it.get("review_count", 0):,})'
                          if it.get("avg_rating") else "")
                st.markdown(f'<div class="fq-meta">{price}{rating}<br>{esc(it.get("category", ""))}</div>',
                            unsafe_allow_html=True)
                if it.get("review_summary"):
                    with st.popover("Reviews", use_container_width=True):
                        st.write(it["review_summary"])

t = res.get("timings", {})
st.markdown(f'<div class="fq-meta" style="margin-top:1.5rem">Total {t.get("total_ms", 0)} ms '
            f'(understanding {t.get("parse_ms", 0)}, embedding {t.get("embed_ms", 0)}, '
            f'search {t.get("search_ms", 0)}, rerank {t.get("rerank_ms", 0)})</div>',
            unsafe_allow_html=True)
