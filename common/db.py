"""Postgres tables and connection, shared by every service.

Phase 1 tables:
  products        cleaned product metadata (one row per parent_asin)
  review_stats    per-product review aggregates (counts, Bayesian rating, dates)
  review_samples  the most helpful reviews per product (input for Phase 2 summaries)
Phase 2 table:
  product_enrichment  LLM-extracted attributes + review summary per product
"""
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, create_engine, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from common.config import settings

engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    parent_asin: Mapped[str] = mapped_column(String(20), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    store: Mapped[str | None] = mapped_column(String(200))
    main_category: Mapped[str | None] = mapped_column(String(100))
    features: Mapped[list] = mapped_column(JSONB, default=list)      # list of strings
    description: Mapped[list] = mapped_column(JSONB, default=list)   # list of strings
    details: Mapped[dict] = mapped_column(JSONB, default=dict)       # e.g. {"Department": "mens"}
    price: Mapped[float | None] = mapped_column(Float)
    image_url: Mapped[str | None] = mapped_column(Text)
    amazon_avg_rating: Mapped[float | None] = mapped_column(Float)
    amazon_rating_count: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReviewStats(Base):
    __tablename__ = "review_stats"

    parent_asin: Mapped[str] = mapped_column(
        ForeignKey("products.parent_asin", ondelete="CASCADE"), primary_key=True)
    review_count: Mapped[int] = mapped_column(Integer)
    avg_rating: Mapped[float] = mapped_column(Float)
    bayes_rating: Mapped[float] = mapped_column(Float)       # use this for ranking
    verified_ratio: Mapped[float] = mapped_column(Float)
    first_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # ~launch date
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rating_hist: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"1": 12, ..., "5": 340}


class ReviewSample(Base):
    __tablename__ = "review_samples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_asin: Mapped[str] = mapped_column(
        ForeignKey("products.parent_asin", ondelete="CASCADE"), index=True)
    rating: Mapped[float] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    helpful_vote: Mapped[int] = mapped_column(Integer, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductEnrichment(Base):
    __tablename__ = "product_enrichment"

    parent_asin: Mapped[str] = mapped_column(
        ForeignKey("products.parent_asin", ondelete="CASCADE"), primary_key=True)
    attributes: Mapped[dict] = mapped_column(JSONB)            # full validated ProductAttributes
    category: Mapped[str] = mapped_column(String(40), index=True)  # copied out for easy queries
    gender: Mapped[str] = mapped_column(String(20), index=True)
    review_summary: Mapped[str | None] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))        # detects changed products (Phase 5)
    prompt_version: Mapped[str] = mapped_column(String(20))    # re-run when the prompt changes
    model: Mapped[str] = mapped_column(String(100))
    enriched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def init_db() -> None:
    """Create any missing tables (safe to call repeatedly)."""
    Base.metadata.create_all(engine)


def drop_all() -> None:
    """Delete all tables and their data. Used by --reset."""
    Base.metadata.drop_all(engine)
