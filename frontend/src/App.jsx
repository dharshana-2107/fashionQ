import { useCallback, useEffect, useRef, useState } from "react";
import { apiHealth, apiSearch } from "./api.js";
import { EXAMPLES } from "./format.js";
import Sidebar, { Code } from "./components/Sidebar.jsx";
import { Results, Understood } from "./components/Results.jsx";

const HEALTH_MS = 10000;

export default function App() {
  const [settings, setSettings] = useState({ llm: true, rerank: true, k: 6 });
  const [input, setInput] = useState("");   // what's in the box
  const [query, setQuery] = useState("");   // what was submitted
  const [highlight, setHighlight] = useState(null);
  const [health, setHealth] = useState(undefined);
  const [res, setRes] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [nonce, setNonce] = useState(0);    // bump to re-run the same search
  const abortRef = useRef(null);

  // Health check, refreshed like st.cache_data(ttl=10).
  useEffect(() => {
    let alive = true;
    const tick = async () => { const h = await apiHealth(); if (alive) setHealth(h); };
    tick();
    const id = setInterval(tick, HEALTH_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Run the search whenever the submitted query or a setting changes.
  useEffect(() => {
    if (!query.trim()) { setRes(null); setError(null); return; }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    apiSearch({ q: query, ...settings }, ctrl.signal)
      .then((data) => { if (!ctrl.signal.aborted) setRes(data); })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setRes(null);
        setError(e.name === "TimeoutError" ? "Search failed: timed out after 90 s" :
          e.unreachable ? e.message : `Search failed: ${e.message}`);
      })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [query, settings, nonce]);

  const submit = (text) => {
    setInput(text);
    setQuery(text);
    setNonce((n) => n + 1);
  };

  const onFind = useCallback((asin, title) => {
    const q = (title || "").split(/\s+/).filter(Boolean).slice(0, 8).join(" ");
    setHighlight(asin);
    submit(q);
    setMenuOpen(false);
  }, []);

  return (
    <div className={`fq-app ${menuOpen ? "menu-open" : ""}`}>
      <button type="button" className="fq-menu-btn" onClick={() => setMenuOpen((o) => !o)}
        aria-label="Toggle settings">☰ Settings</button>
      <Sidebar settings={settings} onSettings={setSettings} health={health} onFind={onFind}
        onCatalogChange={() => query && setNonce((n) => n + 1)} />
      {menuOpen && <div className="fq-scrim" onClick={() => setMenuOpen(false)} />}

      <main className="fq-main">
        <h1 className="fq-title">FashionQ</h1>
        <p className="fq-sub">
          Describe what you need in English, தமிழ் or हिन्दी. Ask for one item or a whole outfit.
        </p>

        <div className="fq-examples">
          {EXAMPLES.map(([label, text]) => (
            <button key={label} type="button" className="fq-btn" onClick={() => submit(text)}>{label}</button>
          ))}
        </div>

        <form className="fq-search" onSubmit={(e) => { e.preventDefault(); submit(input); }}>
          <input type="search" aria-label="Search" value={input} onChange={(e) => setInput(e.target.value)}
            placeholder="e.g. outfit for the beach this summer" />
          <button type="submit" className="fq-btn fq-btn-primary">Search</button>
        </form>

        {!query.trim() && <div className="fq-alert fq-alert-info">Type a query or pick an example above.</div>}
        {loading && <div className="fq-spinner" role="status"><span className="dot" /> Searching…</div>}
        {error && <div className="fq-alert fq-alert-error"><Code text={error} /></div>}

        {res && !error && (
          <div style={{ opacity: loading ? 0.5 : 1, transition: "opacity .15s" }}>
            <Understood parsed={res.parsed} info={res.parse || {}} />
            <Results res={res} highlight={highlight} />
          </div>
        )}
      </main>
    </div>
  );
}
