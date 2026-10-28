"""Cross-client keyword pool: place-free keyword data fetched once, reused by every client.

The pool never stores a client name, client id or URL. A fetch row records one
provider call (a seed or a lookup, for one country and language) and its
outcome; the entry rows are the place-free keywords that call returned.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID, JSONType


class KeywordPoolFetch(Base):
    __tablename__ = "keyword_pool_fetches"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "seed_key",
            "location_code",
            "language_code",
            name="uq_keyword_pool_fetch",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    # seed | lookup
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    seed_key: Mapped[str] = mapped_column(Text, nullable=False)
    seed_text: Mapped[str] = mapped_column(Text, nullable=False)
    concept_key: Mapped[str | None] = mapped_column(Text)
    industry_key: Mapped[str | None] = mapped_column(Text)
    location_code: Mapped[int] = mapped_column(Integer, nullable=False)
    language_code: Mapped[str] = mapped_column(Text, nullable=False)
    # complete | partial | empty
    status: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_volume: Mapped[int] = mapped_column(Integer, nullable=False)
    row_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    errors: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KeywordPoolEntry(Base):
    __tablename__ = "keyword_pool_entries"
    __table_args__ = (
        UniqueConstraint("fetch_id", "keyword_norm", name="uq_keyword_pool_entry"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    fetch_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("keyword_pool_fetches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword_norm: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    volume: Mapped[int | None] = mapped_column(Integer)
    # The full provider row: cpc, competition, difficulty, intent, funnel, trend,
    # match class, seed origin.
    row: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
