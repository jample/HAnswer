"""persist visualization interactive hints

Revision ID: 0011_viz_hints
Revises: 0010_visualization_spec_storage
Create Date: 2026-04-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_viz_hints"
down_revision = "0010_visualization_spec_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "visualizations",
        sa.Column(
            "interactive_hints_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("visualizations", "interactive_hints_json", server_default=None)


def downgrade() -> None:
    op.drop_column("visualizations", "interactive_hints_json")