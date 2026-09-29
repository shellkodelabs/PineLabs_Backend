"""
SQLAlchemy ORM models.

Every model module is imported here so that:
  1. app.core.database.Base.metadata contains all 15 tables as soon as
     this package is imported — required for Alembic autogenerate and
     for Base.metadata.create_all() in tests.
  2. String-based relationship() forward references between modules
     (e.g. Merchant.bin_records -> "BinRecord") resolve correctly once
     every mapped class has been registered.

New Excel-format Bin Series task: `BinRecord`/`BinCustomColumn` (the
old, single-table Bin Series models) stay registered UNCHANGED — their
tables (`bin_records`, `bin_custom_columns`) are being kept as legacy
data, still relied on by app/seed/seed_from_mock_data.py and the
existing Dashboard KPIs (both explicitly out of scope for this task).
`GiftCardBinRecord`/`WalletBinRecord`/`GiftCardBinCustomColumn`/
`WalletBinCustomColumn` are the new, completely independent tables —
no shared base class, no discriminator, no relationship to the old
models or to each other's registry.
"""
from app.models.bin_custom_column import BinCustomColumn  # noqa: F401
from app.models.bin_record import BinRecord  # noqa: F401
from app.models.gift_card_bin_custom_column import GiftCardBinCustomColumn  # noqa: F401
from app.models.gift_card_bin_record import GiftCardBinRecord  # noqa: F401
from app.models.instance import Instance  # noqa: F401
from app.models.merchant import Merchant  # noqa: F401
from app.models.revision import Revision  # noqa: F401
from app.models.sop import SopColumn, SopColumnGroup, SopRow, SopSheet  # noqa: F401
from app.models.user import User, UserSopSheetAccess  # noqa: F401
from app.models.wallet_bin_custom_column import WalletBinCustomColumn  # noqa: F401
from app.models.wallet_bin_record import WalletBinRecord  # noqa: F401

__all__ = [
    "Merchant",
    "BinRecord",
    "BinCustomColumn",
    "GiftCardBinRecord",
    "GiftCardBinCustomColumn",
    "WalletBinRecord",
    "WalletBinCustomColumn",
    "Instance",
    "SopSheet",
    "SopColumnGroup",
    "SopColumn",
    "SopRow",
    "User",
    "UserSopSheetAccess",
    "Revision",
]
