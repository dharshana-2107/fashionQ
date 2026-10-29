import { buildChips, capitalize, fmtInt } from "../format.js";
import Popover from "./Popover.jsx";

export function Understood({ parsed, info }) {
  if (!parsed) {
    if (info?.source === "fallback") {
      return (
        <div className="fq-alert fq-alert-warn">
          The LLM was unavailable, so these are plain search results for your exact words.
        </div>
      );
    }
    return null;
  }
  const chips = buildChips(parsed);
  const src = { cache: "from cache", llm: `parsed by ${info?.model || "LLM"}` }[info?.source] || "";
  return (
    <div className="fq-understood">
      <div className="en">“{parsed.english}”</div>
      {chips.map((c, i) => <span key={i} className="fq-chip">{c}</span>)}
      <div className="fq-meta" style={{ marginTop: ".4rem" }}>{src} in {info?.ms || 0} ms</div>
    </div>
  );
}

function ProductCard({ it, highlight }) {
  const picked = it.parent_asin && it.parent_asin === highlight;
  return (
    <div className="fq-card">
      {it.image_url
        ? <img className="fq-img" src={it.image_url} alt="" loading="lazy" />
        : <div className="fq-img fq-noimg">no photo</div>}
      <div className="fq-title-txt">
        {it.title}
        {it.is_new && <span className="fq-new">NEW</span>}
        {picked && <span className="fq-new fq-picked">YOU PICKED</span>}
      </div>
      <div className="fq-meta">
        {it.price ? <span className="fq-price">${Number(it.price).toFixed(2)}</span> : "price not listed"}{"  "}
        {it.avg_rating ? `★ ${Number(it.avg_rating).toFixed(1)} (${fmtInt(it.review_count)})` : ""}
        <br />
        {it.category || ""}
      </div>
      {it.review_summary && (
        <Popover label="Reviews">
          <p className="fq-review">{it.review_summary}</p>
        </Popover>
      )}
    </div>
  );
}

export function Results({ res, highlight }) {
  const slots = res.slots || [];
  const showHeaders = slots.length > 1 || !!res.parsed;
  const t = res.timings || {};
  return (
    <>
      {slots.map((slot, si) => {
        const items = slot.results || [];
        let note = `searched for “${slot.query}”`;
        if (slot.relaxed?.length) note += `; loosened ${slot.relaxed.join(", ")} to find enough items`;
        return (
          <section key={si} className="fq-slot">
            {showHeaders && (
              <>
                <h4>{capitalize(slot.name)}</h4>
                <div className="fq-meta">{note}</div>
              </>
            )}
            {items.length === 0
              ? <p>Nothing matched this part of the request.</p>
              : (
                <div className="fq-grid">
                  {items.map((it, i) => <ProductCard key={it.parent_asin || i} it={it} highlight={highlight} />)}
                </div>
              )}
          </section>
        );
      })}
      <div className="fq-meta" style={{ marginTop: "1.5rem" }}>
        Total {t.total_ms || 0} ms (understanding {t.parse_ms || 0}, embedding {t.embed_ms || 0},{" "}
        search {t.search_ms || 0}, rerank {t.rerank_ms || 0})
      </div>
    </>
  );
}
