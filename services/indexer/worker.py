"""Indexer worker: keeps Qdrant in sync with catalog events, in near real time.

  python -m services.indexer.worker

For every event on the "catalog.events" Redis Stream (published by the outbox relay):
  product.created / updated / enriched -> load from Postgres, apply rules, embed, upsert
  product.changed / reviewed           -> payload-only update (price, rating...): no embedding
  product.deactivated                  -> delete from Qdrant

Reliability:
  - consumer group: each event is handled once; it's ACKed only after success
  - on restart the worker first re-processes its own un-ACKed (pending) events
  - an event that fails 3 times goes to the "catalog.dlq" stream instead of
    blocking everything behind it
"""
from __future__ import annotations

import os

os.environ.setdefault("TQDM_DISABLE", "1")

import argparse
import logging
import signal
import socket
import time
from collections import defaultdict
from datetime import datetime, timezone

import redis
from sqlalchemy import bindparam, text

from common import events as ev
from common import vectorstore as vs
from common.db import engine
from scripts.bulk_index import SQL as BULK_SQL
from services.indexer.document import build_document, build_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("worker")

MAX_ATTEMPTS = 3
CONSUMER = os.environ.get("WORKER_NAME", "worker-1")  # fixed name => pending events are reclaimed on restart

ROWS_SQL = text(
    BULK_SQL.format(join="LEFT JOIN")
    .replace("WHERE p.is_active", "WHERE p.is_active AND p.parent_asin IN :asins")
).bindparams(bindparam("asins", expanding=True))

_running = True


def _stop(*_):
    global _running
    _running = False
    log.info("stopping after the current batch...")


def ensure_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(ev.STREAM, ev.GROUP, id="0", mkstream=True)
        log.info("created consumer group %s on %s", ev.GROUP, ev.STREAM)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def load_rows(asins: list[str]) -> dict[str, dict]:
    with engine.connect() as conn:
        return {r["parent_asin"]: dict(r) for r in conn.execute(ROWS_SQL, {"asins": asins}).mappings()}


def _existing_doc_hashes(client, collection, asins: list[str]) -> dict[str, str]:
    pts = client.retrieve(collection, [vs.point_id(a) for a in asins], with_payload=["parent_asin", "doc_hash"])
    return {p.payload.get("parent_asin"): p.payload.get("doc_hash") for p in pts if p.payload}


def handle_batch(r, client, collection, embedder, messages, attempts) -> int:
    """messages: [(event_id, fields)]. Returns how many were finished (ACKed).

    Three paths, cheapest first:
      deactivated               -> delete the point
      changed / reviewed        -> overwrite payload fields only (no embedding), unless the
                                   product's text turns out to differ from what's indexed
      created / updated / enriched (or the fallbacks above) -> embed + upsert
    """
    trimmed = [m for m, f in messages if not f]  # entry trimmed from the stream: just ACK it
    messages = [(m, f) for m, f in messages if f]
    if trimmed:
        r.xack(ev.STREAM, ev.GROUP, *trimmed)

    deletes = {m: f for m, f in messages if f.get("type") == ev.DEACTIVATED}
    others = {m: f for m, f in messages if f.get("type") != ev.DEACTIVATED}
    done: list[str] = []
    try:
        for msg_id, f in deletes.items():
            vs.delete_products(client, collection, [f["asin"]])
            ev.set_status(msg_id, "removed", f["type"], int(f.get("ts", 0)) or None)
            done.append(msg_id)

        if others:
            asins = sorted({f["asin"] for f in others.values()})
            rows = load_rows(asins)
            indexed = _existing_doc_hashes(client, collection, asins)
            to_embed, payload_only = [], []
            for msg_id, f in others.items():
                row = rows.get(f["asin"])
                if row is None:  # inactive/unknown now: nothing to index, still ACK
                    if not any(d["asin"] == f["asin"] for d in deletes.values()):
                        ev.set_status(msg_id, "skipped", f["type"], reason="not active in Postgres")
                    done.append(msg_id)
                    continue
                doc = build_document(row)
                payload = build_payload(row, doc)
                stored_hash = indexed.get(f["asin"])
                if f["type"] in ev.PAYLOAD_EVENTS and stored_hash == payload["doc_hash"]:
                    payload_only.append((msg_id, f, payload))
                else:  # embedding events, or a "cheap" event whose text changed / isn't indexed yet
                    if f["type"] == ev.CREATED:
                        payload["is_new"] = True
                        payload["indexed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    to_embed.append((msg_id, f, doc, payload))

            for msg_id, f, payload in payload_only:
                # set_payload merges: keeps fields like is_new / indexed_at that bulk data lacks
                client.set_payload(collection, payload=payload, points=[vs.point_id(f["asin"])], wait=True)
                ev.set_status(msg_id, "payload", f["type"], int(f.get("ts", 0)) or None)
                done.append(msg_id)
                log.info("payload-only %s (%s)", f["asin"], f["type"])

            if to_embed:
                embs = embedder.encode_documents([b[2] for b in to_embed], batch_size=16)
                points = [vs.make_point(f["asin"], e, payload) for (_, f, _, payload), e in zip(to_embed, embs)]
                client.upsert(collection, points=points, wait=True)
                for msg_id, f, _, _ in to_embed:
                    ev.set_status(msg_id, "indexed", f["type"], int(f.get("ts", 0)) or None)
                    done.append(msg_id)
                    log.info("embedded %s (%s)", f["asin"], f["type"])
    except Exception as e:
        log.exception("batch failed: %s", e)
        for msg_id, f in messages:
            if msg_id in done:
                continue
            attempts[msg_id] += 1
            if attempts[msg_id] >= MAX_ATTEMPTS:
                r.xadd(ev.DLQ, {**f, "error": str(e)[:300], "event_id": msg_id})
                ev.set_status(msg_id, "failed", f.get("type", "?"), error=str(e)[:200])
                done.append(msg_id)
                log.error("gave up on %s after %d attempts -> %s", msg_id, MAX_ATTEMPTS, ev.DLQ)
    if done:
        r.xack(ev.STREAM, ev.GROUP, *done)
    return len(done) + len(trimmed)


def run(batch_size: int = 16, block_ms: int = 5000, once: bool = False) -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    r = ev.get_redis()
    ensure_group(r)
    client = vs.get_client()
    collection = vs.ALIAS  # always write to whatever collection search uses

    from common.embedder import get_embedder
    t0 = time.time()
    embedder = get_embedder()
    embedder.encode(["warmup"], batch_size=1, max_length=16)
    log.info("ready on %s as %s/%s (model loaded in %.1fs)", socket.gethostname(), ev.GROUP, CONSUMER,
             time.time() - t0)

    attempts: dict[str, int] = defaultdict(int)
    read_pending = True  # first drain our own un-ACKed events (from a crash/restart)
    while _running:
        start_id = "0" if read_pending else ">"
        try:
            resp = r.xreadgroup(ev.GROUP, CONSUMER, {ev.STREAM: start_id}, count=batch_size,
                                block=None if read_pending else block_ms)
        except (redis.ConnectionError, redis.TimeoutError) as e:
            log.warning("Redis hiccup (%s); retrying in 2s", e)  # e.g. Docker restarting Redis
            time.sleep(2)
            read_pending = True
            continue
        messages = resp[0][1] if resp else []
        if read_pending and not messages:
            read_pending = False
            continue
        if messages:
            n = handle_batch(r, client, collection, embedder, messages, attempts)
            if n < len(messages):
                read_pending = True  # retry the failed ones next round
                time.sleep(1)
        elif once:
            break
    log.info("worker stopped")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--once", action="store_true", help="process what's queued, then exit (for tests)")
    args = ap.parse_args()
    run(batch_size=args.batch_size, block_ms=1000 if args.once else 2000, once=args.once)


if __name__ == "__main__":
    main()
