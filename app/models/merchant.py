"""
SQLAlchemy model for `merchants`.

See backend design Part 3 §5. Deliberately minimal — only id, name,
classification, and timestamps. No description/logo/slug/etc.: none of
those are justified by the frontend analysis (Parts 1-2).
"""
from datetime import datetime
from typing import List

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Single source of truth for the allowed classification values — used to
# build the CHECK constraint below. A native PostgreSQL ENUM type was
# deliberately NOT used (Part 3 §5): this is a business-managed taxonomy,
# more likely to change than a technical constant, and a CHECK constraint
# is far cheaper to alter via a future migration than a PG ENUM type.
CLASSIFICATION_VALUES = (
    "Digital Gift Card",
    "Physical Gift Card",
    "Corporate Gifting",
    "Reward Card",
)
_classification_check_sql = "classification IN ({})".format(
    ", ".join(f"'{value}'" for value in CLASSIFICATION_VALUES)
)


class Merchant(Base):
    __tablename__ = "merchants"
    __table_args__ = (
        CheckConstraint(_classification_check_sql, name="ck_merchants_classification"),
        Index("ix_merchants_classification", "classification"),
        # NOTE: the trigram search index on `name` (ix_merchants_name_trgm,
        # requires pg_trgm) is created directly in the Alembic migration
        # rather than declared here — see migrations/versions/ — since it
        # is a search-optimization index, not part of the logical schema,
        # and expression/opclass indexes are more reliably hand-authored
        # in the migration than reproduced by autogenerate on every diff.
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships — one-directional where nothing needs the reverse.
    bin_records: Mapped[List["BinRecord"]] = relationship(back_populates="merchant")
    sop_sheets: Mapped[List["SopSheet"]] = relationship(back_populates="merchant")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Merchant id={self.id} name={self.name!r}>"
