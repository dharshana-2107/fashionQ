"""Catalog events over Redis Streams.

A Redis Stream is an append-only log, like a mini Kafka:
  - the catalog service APPENDS events ("product.created", "product.deactivated")
  - the indexer worker READS them through a *consumer group*. Each event is
    delivered to the group once, and stays "pending" until the worker ACKs it.
    If the worker crashes mid-way, the un-ACKed events are re-delivered on restart,
    so no new product is ever lost.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache

import redis

from common.config import settings

STREAM = "catalog.events"         # main event log
GROUP = "indexer"                 # consumer group of indexer workers
DLQ = "catalog.dlq"               # events that failed 3 times ("dead letter queue")
STATUS = "catalog:status:"        # key prefix: catalog:status:<event_id> -> {"state", "at", "latency_ms"}
STATUS_TTL_S = 24 * 3600          # statuses are only for the UI, so they expire
RECENT = "catalog:recent"         # list of recent events (newest first) for the UI
STREAM_MAXLEN = 10_000

CREATED = "product.created"          # new product            -> embed + index (NEW badge)
UPDATED = "product.updated"          # title/features changed -> re-embed
ENRICHED = "product.enriched"        # LLM tags saved         -> re-embed
CHANGED = "product.changed"          # price/image/details    -> payload only
REVIEWED = "product.reviewed"        # new review             -> payload only (rating, count)
DEACTIVATED = "product.deactivated"  # removed                -> delete from index

EMBED_EVENTS = {CREATED, UPDATED, ENRICHED}
PAYLOAD_EVENTS = {CHANGED, REVIEWED}


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    # redis-py 8 defaults to a 5 s socket timeout; blocking stream reads need more headroom
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_timeout=30, socket_connect_timeout=5, health_check_interval=30)


def now_ms() -> int:
    return int(time.time() * 1000)


def publish(event_type: str, asin: str, title: str = "", source: str = "catalog-api",
            ts: int | None = None) -> str:
    """Append one event to the stream. ts = when the change happened (ms); default now."""
    r = get_redis()
    ts = ts or now_ms()
    event_id = r.xadd(STREAM, {"type": event_type, "asin": asin, "ts": str(ts), "source": source},
                      maxlen=STREAM_MAXLEN, approximate=True)
    r.set(STATUS + event_id, json.dumps({"state": "queued", "event": event_type, "at": ts}), ex=STATUS_TTL_S)
    r.lpush(RECENT, json.dumps({"id": event_id, "type": event_type, "asin": asin,
                                "title": title[:120], "ts": ts}))
    r.ltrim(RECENT, 0, 49)
    return event_id


def set_status(event_id: str, state: str, event_type: str, published_ms: int | None = None, **extra) -> None:
    at = now_ms()
    data = {"state": state, "event": event_type, "at": at, **extra}
    if published_ms:
        data["latency_ms"] = at - published_ms
    get_redis().set(STATUS + event_id, json.dumps(data), ex=STATUS_TTL_S)


def get_status(event_ids: list[str]) -> dict[str, dict]:
    if not event_ids:
        return {}
    vals = get_redis().mget([STATUS + i for i in event_ids])
    return {i: json.loads(v) for i, v in zip(event_ids, vals) if v}
