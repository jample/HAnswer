"""Helpers for encoding retrieval refs that may target a solution."""

from __future__ import annotations

import uuid


def encode_solution_ref(
    *,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None,
) -> str:
    if solution_id is None:
        return str(question_id)
    return f"{question_id}::{solution_id}"


def decode_solution_ref(ref_id: str) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    if "::" in ref_id:
        left, right = ref_id.split("::", 1)
        try:
            return uuid.UUID(left), uuid.UUID(right)
        except ValueError:
            return None
    try:
        return uuid.UUID(ref_id), None
    except ValueError:
        return None
