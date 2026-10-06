"""Install the change-data-capture triggers in Postgres. Safe to re-run.

  python -m scripts.install_triggers              # install / update
  python -m scripts.install_triggers --uninstall  # remove triggers (keeps the outbox table)
  python -m scripts.install_triggers --status     # show outbox backlog

Every meaningful change to a product writes one row into `catalog_outbox`,
INSIDE the same transaction as the change. So if the product is saved, its
event is saved too, even if the relay or worker is down at that moment.

  products INSERT (active)                         -> product.created    (embed + index, NEW badge)
  products UPDATE of title/features/description/store -> product.updated (re-embed)
  products UPDATE of price/image/details only      -> product.changed    (payload only, no embedding)
  products is_active true->false, or DELETE        -> product.deactivated (remove from search)
  products is_active false->true                   -> product.created
  review_stats INSERT/UPDATE (a new review)        -> product.reviewed   (payload only: rating, count)
  product_enrichment INSERT/UPDATE                 -> product.enriched   (re-embed: tags + summary)

Unchanged rewrites (e.g. the loader re-saving identical rows) produce no events.
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from common.db import engine

DDL = r"""
CREATE TABLE IF NOT EXISTS catalog_outbox (
    id           BIGSERIAL PRIMARY KEY,
    event_type   TEXT        NOT NULL,
    parent_asin  TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS catalog_outbox_unpublished ON catalog_outbox (id) WHERE published_at IS NULL;

CREATE OR REPLACE FUNCTION fq_products_event() RETURNS trigger AS $$
DECLARE ev TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.is_active THEN ev := 'product.created'; END IF;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO catalog_outbox (event_type, parent_asin) VALUES ('product.deactivated', OLD.parent_asin);
        RETURN OLD;
    ELSE  -- UPDATE
        IF OLD.is_active AND NOT NEW.is_active THEN
            ev := 'product.deactivated';
        ELSIF NOT OLD.is_active AND NEW.is_active THEN
            ev := 'product.created';
        ELSIF NOT NEW.is_active THEN
            ev := NULL;  -- changes to an inactive product don't matter for search
        ELSIF (OLD.title, OLD.features, OLD.description, OLD.store)
              IS DISTINCT FROM (NEW.title, NEW.features, NEW.description, NEW.store) THEN
            ev := 'product.updated';
        ELSIF (OLD.price, OLD.image_url, OLD.details)
              IS DISTINCT FROM (NEW.price, NEW.image_url, NEW.details) THEN
            ev := 'product.changed';
        END IF;
    END IF;
    IF ev IS NOT NULL THEN
        INSERT INTO catalog_outbox (event_type, parent_asin) VALUES (ev, NEW.parent_asin);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fq_reviews_event() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' OR (OLD.review_count, OLD.avg_rating, OLD.bayes_rating)
                           IS DISTINCT FROM (NEW.review_count, NEW.avg_rating, NEW.bayes_rating) THEN
        INSERT INTO catalog_outbox (event_type, parent_asin) VALUES ('product.reviewed', NEW.parent_asin);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fq_enrichment_event() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' OR OLD.attributes IS DISTINCT FROM NEW.attributes THEN
        INSERT INTO catalog_outbox (event_type, parent_asin) VALUES ('product.enriched', NEW.parent_asin);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fq_products_cdc ON products;
CREATE TRIGGER fq_products_cdc AFTER INSERT OR UPDATE OR DELETE ON products
    FOR EACH ROW EXECUTE FUNCTION fq_products_event();
DROP TRIGGER IF EXISTS fq_reviews_cdc ON review_stats;
CREATE TRIGGER fq_reviews_cdc AFTER INSERT OR UPDATE ON review_stats
    FOR EACH ROW EXECUTE FUNCTION fq_reviews_event();
DROP TRIGGER IF EXISTS fq_enrichment_cdc ON product_enrichment;
CREATE TRIGGER fq_enrichment_cdc AFTER INSERT OR UPDATE ON product_enrichment
    FOR EACH ROW EXECUTE FUNCTION fq_enrichment_event();
"""

UNINSTALL = """
DROP TRIGGER IF EXISTS fq_products_cdc ON products;
DROP TRIGGER IF EXISTS fq_reviews_cdc ON review_stats;
DROP TRIGGER IF EXISTS fq_enrichment_cdc ON product_enrichment;
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if engine.dialect.name != "postgresql":
        raise SystemExit("Triggers need PostgreSQL.")
    with engine.begin() as conn:
        if args.status:
            row = conn.execute(text("SELECT count(*) FILTER (WHERE published_at IS NULL), count(*) "
                                    "FROM catalog_outbox")).one()
            print(f"outbox: {row[0]} waiting to be published, {row[1]} total")
            return
        if args.uninstall:
            conn.exec_driver_sql(UNINSTALL)
            print("Triggers removed (catalog_outbox table kept).")
            return
        conn.exec_driver_sql(DDL)
    print("Installed: catalog_outbox table + triggers on products, review_stats, product_enrichment.")
    print("Next: start the relay (python -m services.catalog.outbox_relay) and the indexer worker.")


if __name__ == "__main__":
    main()
