// Base URLs. In dev these go through the Vite proxy (see vite.config.js).
// For a production build, set VITE_API_URL / VITE_CATALOG_URL at build time,
// or serve the build behind a reverse proxy that maps /api and /catalog.
export const API_URL = import.meta.env.VITE_API_URL || "/api";
export const CATALOG_URL = import.meta.env.VITE_CATALOG_URL || "/catalog";

export const CATALOG_DOWN_MSG =
  "Catalog service isn't running. Start it with `uvicorn services.catalog.api:app --port 8001`.";
export const SEARCH_DOWN_MSG =
  "Search service isn't running. Start it with `uvicorn services.search.api:app --port 8000`.";

function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null) p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

async function withTimeout(promiseFn, ms, outerSignal) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(new DOMException("Timed out", "TimeoutError")), ms);
  const onOuterAbort = () => ctrl.abort(outerSignal.reason);
  outerSignal?.addEventListener("abort", onOuterAbort);
  try {
    return await promiseFn(ctrl.signal);
  } finally {
    clearTimeout(timer);
    outerSignal?.removeEventListener("abort", onOuterAbort);
  }
}

// The Vite proxy answers 502/504 with a non-JSON body when the target is down.
function looksDown(res) {
  return res.status === 502 || res.status === 503 || res.status === 504;
}

export async function apiHealth() {
  try {
    return await withTimeout(async (signal) => {
      const r = await fetch(`${API_URL}/health`, { signal });
      if (!r.ok) return null;
      return await r.json();
    }, 3000);
  } catch {
    return null;
  }
}

export class SearchError extends Error {
  constructor(message, { unreachable = false } = {}) {
    super(message);
    this.unreachable = unreachable;
  }
}

export async function apiSearch({ q, k, llm, rerank }, outerSignal) {
  return withTimeout(async (signal) => {
    let r;
    try {
      r = await fetch(`${API_URL}/search${qs({ q, k, llm, rerank })}`, { signal });
    } catch (e) {
      if (e.name === "AbortError" || e.name === "TimeoutError") throw e;
      throw new SearchError(SEARCH_DOWN_MSG, { unreachable: true });
    }
    if (looksDown(r)) throw new SearchError(SEARCH_DOWN_MSG, { unreachable: true });
    if (!r.ok) {
      let detail = `${r.status} ${r.statusText}`;
      try {
        const body = await r.json();
        if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch { /* not JSON */ }
      throw new SearchError(detail);
    }
    return r.json();
  }, 90000, outerSignal);
}

/**
 * Calls the catalog service. Returns { data } on success or { error } with a
 * human-readable message, mirroring the Streamlit `catalog()` helper.
 */
export async function catalog(method, path, { body, params } = {}) {
  try {
    return await withTimeout(async (signal) => {
      const r = await fetch(`${CATALOG_URL}${path}${qs(params)}`, {
        method,
        signal,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (looksDown(r)) return { error: CATALOG_DOWN_MSG };
      if (!r.ok) {
        let msg = `${r.status} ${r.statusText}`;
        try {
          const j = await r.json();
          if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
        } catch { /* not JSON */ }
        return { error: msg };
      }
      return { data: await r.json() };
    }, 10000);
  } catch {
    return { error: CATALOG_DOWN_MSG };
  }
}
