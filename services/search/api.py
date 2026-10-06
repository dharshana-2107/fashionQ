"""Search microservice.

Run from the project root:
  uvicorn services.search.api:app --port 8000

Then try:
  http://localhost:8000/health
  http://localhost:8000/search?q=outfit%20for%20the%20beach%20this%20summer
  http://localhost:8000/docs        (interactive API docs, free with FastAPI)
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from common import vectorstore as vs
from common.config import settings
from services.search import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("search-api")
logging.getLogger("httpx").setLevel(logging.WARNING)  # hide one log line per Qdrant call

RERANK_ON_STARTUP = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    t0 = time.time()
    log.info("Loading models (first start can take ~30 s)...")
    pipeline.warmup(rerank=RERANK_ON_STARTUP)
    log.info("Models ready in %.1fs", time.time() - t0)
    yield


app = FastAPI(title="FashionQ search", version="0.4", lifespan=lifespan)


@app.get("/health")
def health():
    client = vs.get_client()
    target = vs.alias_target(client) or vs.ALIAS
    try:
        count = client.count(target, exact=True).count
    except Exception as e:
        raise HTTPException(503, f"Qdrant not reachable: {e}")
    return {"status": "ok", "collection": target, "products": count,
            "llm_model": getattr(settings, "llm_model", None)}


@app.get("/search")
def search(q: str = Query(..., min_length=1, max_length=300, description="query in any language"),
           k: int = Query(6, ge=1, le=20, description="results per outfit slot"),
           llm: bool = Query(True, description="use the LLM query parser"),
           rerank: bool = Query(True, description="use the cross-encoder reranker")):
    # plain `def`: FastAPI runs it in a worker thread, so slow model calls don't block the server
    return pipeline.search(q, k=k, use_llm=llm, use_rerank=rerank)
