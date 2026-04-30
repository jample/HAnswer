"""persist visualization planning/spec artifacts

Revision ID: 0010_visualization_spec_storage
Revises: 0009_dialog_solution_anchor
Create Date: 2026-04-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_visualization_spec_storage"
down_revision = "0009_dialog_solution_anchor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "question_solutions",
        sa.Column(
            "visualization_plan_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "visualizations",
        sa.Column(
            "spec_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("visualizations", "spec_json")
    op.drop_column("question_solutions", "visualization_plan_json")