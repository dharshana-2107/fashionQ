# FashionQ frontend (React)

React + Vite replacement for `demo/app.py`. It talks to the same two FastAPI
services; nothing on the backend needs to change for local development.

## Run it

```bash
uvicorn services.search.api:app --port 8000     # terminal 1
uvicorn services.catalog.api:app --port 8001    # terminal 2
cd frontend && npm install && npm run dev       # terminal 3 → http://localhost:5173
```

The Vite dev server proxies `/api/*` → `:8000` and `/catalog/*` → `:8001`,
so the browser never makes a cross-origin request. If your services run
elsewhere, set `FASHIONQ_API` / `FASHIONQ_CATALOG` before `npm run dev`
(same names the Streamlit app used).

## Production build

```bash
npm run build      # outputs frontend/dist/
```

Serve `dist/` behind a reverse proxy (nginx, Caddy…) that maps `/api` and
`/catalog` to the two services, exactly like the dev proxy does.

Or point the build straight at the services:

```bash
VITE_API_URL=https://search.example.com VITE_CATALOG_URL=https://catalog.example.com npm run build
```

In that case the browser calls the services directly, so both FastAPI apps
need CORS enabled for the frontend's origin:

```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["https://your-frontend.example.com"],
                   allow_methods=["*"], allow_headers=["*"])
```

## Layout

```
src/
  api.js                  HTTP calls (search, health, catalog) + error messages
  format.js               examples, labels, chip building, number formatting
  App.jsx                 header, examples, search box, result state
  components/Sidebar.jsx  settings, health, add-product form, live event feed
  components/Results.jsx  "what we understood" box, slot groups, product cards
  components/Popover.jsx  small popover used for Actions and Reviews
  styles.css              same palette and classes as the Streamlit CSS
```

## Differences from the Streamlit version

- Search runs on Enter / the Search button (or an example click), not on every keystroke.
- The live-catalog feed polls every 3 s, so you can watch new products index without clicking Refresh.
- After a catalog action (review, discount, removal), the current search re-runs so the change shows up.
- On narrow screens the sidebar becomes a slide-out panel.
