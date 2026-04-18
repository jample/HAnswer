"""solution scoped retrieval artifacts

Revision ID: 0006_solution_retrieval_index
Revises: 0005_question_solutions
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_solution_retrieval_index"
down_revision = "0005_question_solutions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints("question_retrieval_profiles"):
        cols = list(constraint.get("column_names") or [])
        if cols == ["question_id"]:
            op.drop_constraint(
                constraint["name"],
                "question_retrieval_profiles",
                type_="unique",
            )
            break
    op.add_column(
        "question_retrieval_profiles",
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("question_solutions.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_question_retrieval_profiles_solution_id",
        "question_retrieval_profiles",
        ["solution_id"],
    )

    existing_indexes = {
        idx["name"]: list(idx.get("column_names") or [])
        for idx in inspector.get_indexes("retrieval_units")
    }
    for name, cols in existing_indexes.items():
        if cols == ["question_id", "unit_kind"]:
            op.drop_index(name, table_name="retrieval_units")
            break
    op.add_column(
        "retrieval_units",
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("question_solutions.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_retrieval_units_question_solution_kind",
        "retrieval_units",
        ["question_id", "solution_id", "unit_kind"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if any(idx["name"] == "ix_retrieval_units_question_solution_kind" for idx in inspector.get_indexes("retrieval_units")):
        op.drop_index("ix_retrieval_units_question_solution_kind", table_name="retrieval_units")
    op.drop_column("retrieval_units", "solution_id")
    op.create_index(
        "ix_retrieval_units_question_kind",
        "retrieval_units",
        ["question_id", "unit_kind"],
    )

    for constraint in inspector.get_unique_constraints("question_retrieval_profiles"):
        if constraint["name"] == "uq_question_retrieval_profiles_solution_id":
            op.drop_constraint(
                "uq_question_retrieval_profiles_solution_id",
                "question_retrieval_profiles",
                type_="unique",
            )
            break
    op.drop_column("question_retrieval_profiles", "solution_id")
    op.create_unique_constraint(
        "question_retrieval_profiles_question_id_key",
        "question_retrieval_profiles",
        ["question_id"],
    )
