"""Outbox relay: moves catalog events from Postgres to the Redis Stream.

  python -m services.catalog.outbox_relay

Postgres triggers write every product change into `catalog_outbox` (in the same
transaction as the change). This small loop publishes those rows to the
"catalog.events" Redis Stream about once per second, then marks them published.

Why not publish straight from the trigger? A trigger can't talk to Redis, and if
Redis or this relay is down, the events simply wait in the table: nothing is lost.
Rows are marked published only after Redis accepted them; if the relay crashes in
between, an event may be published twice, which is harmless (indexing is idempotent).
"""
from __future__ import annotations

import logging
import signal
import time

import redis
from sqlalchemy import text

from common import events as ev
from common.db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s relay: %(message)s")
log = logging.getLogger("relay")

BATCH = 200
POLL_S = 0.5

FETCH = text("""
    SELECT o.id, o.event_type, o.parent_asin, extract(epoch FROM o.created_at) * 1000 AS ts,
           COALESCE(p.title, '') AS title
    FROM catalog_outbox o LEFT JOIN products p ON p.parent_asin = o.parent_asin
    WHERE o.published_at IS NULL
    ORDER BY o.id
    LIMIT :n
    FOR UPDATE OF o SKIP LOCKED
""")
MARK = text("UPDATE catalog_outbox SET published_at = now() WHERE id = ANY(:ids)")

_running = True


def _stop(*_):
    global _running
    _running = False


def relay_once() -> int:
    with engine.begin() as conn:  # row locks held until commit: two relays never double-send
        rows = conn.execute(FETCH, {"n": BATCH}).all()
        if not rows:
            return 0
        for r in rows:
            ev.publish(r.event_type, r.parent_asin, r.title, source="postgres-trigger",
                       ts=int(r.ts))
        conn.execute(MARK, {"ids": [r.id for r in rows]})
    return len(rows)


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("relaying catalog_outbox -> Redis stream '%s'", ev.STREAM)
    while _running:
        try:
            n = relay_once()
            if n:
                log.info("published %d event(s)", n)
                continue  # there may be more waiting
        except (redis.ConnectionError, redis.TimeoutError) as e:
            log.warning("Redis unavailable (%s); events stay in the outbox, retrying", e)
            time.sleep(2)
        except Exception as e:  # e.g. Postgres restarting
            log.warning("relay error (%s); retrying", e)
            time.sleep(2)
        time.sleep(POLL_S)
    log.info("relay stopped")


if __name__ == "__main__":
    main()
