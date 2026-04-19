"""visualization render engines (geogebra)

Adds ``engine``, ``ggb_commands_json``, ``ggb_settings_json`` columns to the
``visualizations`` table so the LLM can emit GeoGebra command lists in
addition to JSXGraph code. Existing rows default to ``engine='jsxgraph'``
keeping their ``jsx_code`` payload intact.

Revision ID: 0007_visualization_engines
Revises: 0006_solution_retrieval_index
Create Date: 2026-04-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_visualization_engines"
down_revision = "0006_solution_retrieval_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "visualizations",
        sa.Column(
            "engine",
            sa.String(length=16),
            nullable=False,
            server_default="jsxgraph",
        ),
    )
    op.add_column(
        "visualizations",
        sa.Column(
            "ggb_commands_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "visualizations",
        sa.Column(
            "ggb_settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    # jsx_code is now optional (geogebra rows leave it empty).
    op.alter_column(
        "visualizations",
        "jsx_code",
        existing_type=sa.Text(),
        nullable=False,
        server_default="",
    )


def downgrade() -> None:
    op.alter_column(
        "visualizations",
        "jsx_code",
        existing_type=sa.Text(),
        nullable=False,
        server_default=None,
    )
    op.drop_column("visualizations", "ggb_settings_json")
    op.drop_column("visualizations", "ggb_commands_json")
    op.drop_column("visualizations", "engine")
