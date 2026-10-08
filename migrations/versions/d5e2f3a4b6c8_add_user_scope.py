"""add user scope (selected instances + issuers)

Revision ID: d5e2f3a4b6c8
Revises: c4f1a2b3d5e7
Create Date: 2026-10-08 14:30:00.000000

Adds `users.scope` — a JSONB document holding the user's selected
instances and issuers from the Create/Edit User screen, shaped as
    {"instanceIds": [1, 2, ...], "issuers": ["Aurora Retail", ...]}

Previously the Create User modal collected these selections but they
were never persisted (the create payload sent an empty access map). This
column stores them so the User Management list can show how many
instances / issuers each user has, and the Edit modal can pre-fill them.

Chosen as a JSONB column rather than new join tables because the
frontend collects exactly these two flat lists and there is no
normalized issuer entity to reference (issuers are free-text strings
derived from BIN data). NOT NULL with a '{}' server_default so every
existing row (and any insert that omits it) has a well-formed object —
no backfill needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d5e2f3a4b6c8"
down_revision: Union[str, None] = "c4f1a2b3d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "scope")
