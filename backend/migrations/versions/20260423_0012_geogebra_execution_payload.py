"""persist GeoGebra execution payload artifacts

Revision ID: 0012_geogebra_execution_payload
Revises: 0011_viz_hints
Create Date: 2026-04-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_geogebra_execution_payload"
down_revision = "0011_viz_hints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "visualizations",
        sa.Column(
            "execution_payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "visualizations",
        sa.Column(
            "degraded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("visualizations", "degraded", server_default=None)


def downgrade() -> None:
    op.drop_column("visualizations", "degraded")
    op.drop_column("visualizations", "execution_payload_json")
