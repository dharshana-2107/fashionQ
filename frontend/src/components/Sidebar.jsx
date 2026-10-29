import { useCallback, useEffect, useState } from "react";
import { catalog, SEARCH_DOWN_MSG } from "../api.js";
import { DEPARTMENTS, EVENT_LABELS, fmtInt, statusLabel } from "../format.js";
import Popover from "./Popover.jsx";

const EMPTY_FORM = { title: "", price: 29.99, dept: DEPARTMENTS[0], image: "", features: "" };
const POLL_MS = 3000;

export default function Sidebar({ settings, onSettings, health, onFind, onCatalogChange }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [msg, setMsg] = useState(null);
  const [recent, setRecent] = useState(null);
  const [stats, setStats] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const r = await catalog("GET", "/recent", { params: { n: 8 } });
    if (r.error) {
      setRecent(null);
      setStats(null);
      setMsg((m) => m || r.error);
      return;
    }
    setRecent(r.data);
    const s = await catalog("GET", "/stats");
    setStats(s.data || null);
  }, []);

  // Streamlit only refreshed on a rerun; here the feed polls so you can watch events index.
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const after = async (text) => {
    setMsg(text);
    await refresh();
    onCatalogChange?.();
  };

  async function addProduct(e) {
    e.preventDefault();
    if (form.title.trim().length < 3) {
      setMsg("Please enter a product title.");
      return;
    }
    setBusy(true);
    const body = {
      title: form.title.trim(),
      price: Number(form.price),
      details: { Department: form.dept },
      image_url: form.image.trim() || null,
      features: form.features.split("\n").map((f) => f.trim()).filter(Boolean),
    };
    const r = await catalog("POST", "/products", { body });
    setBusy(false);
    if (r.error) return setMsg(r.error);
    setForm(EMPTY_FORM);
    after(`Added ${r.data.parent_asin}. Watch it index below.`);
  }

  async function review(asin, stars) {
    const r = await catalog("POST", `/products/${asin}/reviews`, { body: { rating: stars } });
    if (r.error) return setMsg(r.error);
    after(`Review added: now ${r.data.review_count} reviews, avg ${Number(r.data.avg_rating).toFixed(2)}. ` +
      "Ratings update without re-embedding.");
  }

  async function discount(asin) {
    const p = await catalog("GET", `/products/${asin}`);
    if (p.error) return setMsg(p.error);
    if (!p.data?.price) return setMsg("This product has no price to discount.");
    const newPrice = Math.round(p.data.price * 0.9 * 100) / 100;
    const r = await catalog("PATCH", `/products/${asin}`, { body: { price: newPrice } });
    if (r.error) return setMsg(r.error);
    after(`Price changed to $${newPrice.toFixed(2)} (payload-only update).`);
  }

  async function remove(asin) {
    const r = await catalog("POST", `/products/${asin}/deactivate`);
    if (r.error) return setMsg(r.error);
    after("Removed. It disappears from search in a moment.");
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <aside className="fq-sidebar">
      <h3>Settings</h3>
      <label className="fq-toggle" title="Off = plain hybrid search on the raw text">
        <input type="checkbox" checked={settings.llm}
          onChange={(e) => onSettings({ ...settings, llm: e.target.checked })} />
        <span>Understand the query with the LLM</span>
      </label>
      <label className="fq-toggle" title="Cross-encoder re-scoring of candidates">
        <input type="checkbox" checked={settings.rerank}
          onChange={(e) => onSettings({ ...settings, rerank: e.target.checked })} />
        <span>Rerank results</span>
      </label>
      <label className="fq-field">
        <span>Items per group: <b>{settings.k}</b></span>
        <input type="range" min={3} max={12} value={settings.k}
          onChange={(e) => onSettings({ ...settings, k: Number(e.target.value) })} />
      </label>

      <hr />
      {health ? (
        <div className="fq-meta">
          <div>{fmtInt(health.products)} products indexed</div>
          <div>LLM: {String(health.llm_model ?? "")}</div>
        </div>
      ) : (
        <div className="fq-alert fq-alert-error"><Code text={SEARCH_DOWN_MSG} /></div>
      )}

      <hr />
      <h3>Live catalog</h3>
      <p className="fq-meta">Every change in Postgres is picked up by a trigger and reaches search within seconds.</p>

      <form className="fq-form" onSubmit={addProduct}>
        <label className="fq-field">
          <span>Product title</span>
          <input type="text" value={form.title} onChange={set("title")}
            placeholder="Women's Linen Wide Leg Pants Beige" />
        </label>
        <div className="fq-row2">
          <label className="fq-field">
            <span>Price ($)</span>
            <input type="number" min={0} step="any" value={form.price} onChange={set("price")} />
          </label>
          <label className="fq-field">
            <span>Department</span>
            <select value={form.dept} onChange={set("dept")}>
              {DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}
            </select>
          </label>
        </div>
        <label className="fq-field">
          <span>Image URL (optional)</span>
          <input type="url" value={form.image} onChange={set("image")} />
        </label>
        <label className="fq-field">
          <span>Features (one per line, optional)</span>
          <textarea rows={3} value={form.features} onChange={set("features")} />
        </label>
        <button type="submit" className="fq-btn fq-btn-block fq-btn-primary" disabled={busy}>
          {busy ? "Adding…" : "Add product"}
        </button>
      </form>

      <button type="button" className="fq-btn fq-btn-block" onClick={() => { setMsg(null); refresh(); }}>
        Refresh
      </button>

      {msg && (
        <div className="fq-alert fq-alert-info">
          <Code text={msg} />
          <button type="button" className="fq-x" aria-label="Dismiss" onClick={() => setMsg(null)}>×</button>
        </div>
      )}

      {recent && (
        <div className="fq-events">
          {recent.events.map((e) => {
            const title = e.title || e.asin;
            return (
              <div key={e.id} className="fq-event">
                <div className="fq-meta">
                  <b>{title.slice(0, 70)}</b><br />
                  {EVENT_LABELS[e.type] || e.type}: {statusLabel(e.status || {})}
                </div>
                {e.type !== "product.deactivated" && (
                  <Popover label="Actions">
                    {(close) => (
                      <div className="fq-actions">
                        <button type="button" className="fq-btn fq-btn-block"
                          onClick={() => { close(); onFind(e.asin, title); }}>Find in search</button>
                        <button type="button" className="fq-btn fq-btn-block"
                          onClick={() => { close(); review(e.asin, 5); }}>Add a 5★ review</button>
                        <button type="button" className="fq-btn fq-btn-block"
                          onClick={() => { close(); review(e.asin, 1); }}>Add a 1★ review</button>
                        <button type="button" className="fq-btn fq-btn-block"
                          onClick={() => { close(); discount(e.asin); }}>Price −10%</button>
                        <button type="button" className="fq-btn fq-btn-block"
                          onClick={() => { close(); remove(e.asin); }}>Remove from store</button>
                      </div>
                    )}
                  </Popover>
                )}
              </div>
            );
          })}

          {stats && (
            <>
              {!stats.triggers_installed && (
                <div className="fq-alert fq-alert-warn">
                  <Code text="Triggers not installed: run `python -m scripts.install_triggers`." />
                </div>
              )}
              <p className="fq-meta">
                {fmtInt(stats.active_products)} products in the store; outbox backlog {stats.outbox_backlog || 0};{" "}
                {stats.pending_events || 0} events in progress
              </p>
            </>
          )}
        </div>
      )}
    </aside>
  );
}

// Renders `backtick` spans as <code>, like Streamlit's markdown did.
export function Code({ text }) {
  const parts = String(text).split("`");
  return <span>{parts.map((p, i) => (i % 2 ? <code key={i}>{p}</code> : p))}</span>;
}
