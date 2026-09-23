"""
SQLAlchemy ORM models.

Every model module is imported here so that:
  1. app.core.database.Base.metadata contains all 9 tables as soon as
     this package is imported — required for Alembic autogenerate and
     for Base.metadata.create_all() in tests.
  2. String-based relationship() forward references between modules
     (e.g. Merchant.bin_records -> "BinRecord") resolve correctly once
     every mapped class has been registered.
"""
from app.models.bin_record import BinRecord  # noqa: F401
from app.models.merchant import Merchant  # noqa: F401
from app.models.revision import Revision  # noqa: F401
from app.models.sop import SopColumn, SopColumnGroup, SopRow, SopSheet  # noqa: F401
from app.models.user import User, UserSopSheetAccess  # noqa: F401

__all__ = [
    "Merchant",
    "BinRecord",
    "SopSheet",
    "SopColumnGroup",
    "SopColumn",
    "SopRow",
    "User",
    "UserSopSheetAccess",
    "Revision",
]
