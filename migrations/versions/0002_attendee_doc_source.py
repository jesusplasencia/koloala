"""pass_attendees.doc_source: OCR (foto del reverso del DNI, leída en el teléfono) o MANUAL

Revision ID: 0002_attendee_doc_source
Revises: 0001_initial_schema
Create Date: 2026-11-12 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_attendee_doc_source"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pass_attendees", sa.Column("doc_source", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("pass_attendees", "doc_source")
