"""Reciprocal Rank Fusion (RRF) for multi-route retrieval (§3.4).

Given N ranked candidate lists from independent routes (dense ANN,
sparse BM25, structural pattern/kp overlap), RRF produces a single
combined ranking whose only tuning knob is the damping constant `k`.

    RRF_score(d) = Σ_route w_route * 1 / (k + rank_d_in_route)

`rank` is 1-based; missing entries contribute 0. k=60 is the value
from Cormack, Clarke & Büttcher (2009); we keep it configurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class FusedHit:
    ref_id: str
    score: float
    ranks: dict[str, int]                    # route_name → rank (1-based)


def fuse(
    routes: dict[str, list[str]],
    *,
    k: int = 60,
    weights: dict[str, float] | None = None,
) -> list[FusedHit]:
    """Fuse ranked ID lists from multiple routes into one ranking.

    Args:
      routes:  route_name → ordered list of ref_ids (best first).
      k:       RRF damping constant; higher k smooths tail differences.
      weights: optional per-route weight multiplier.

    Returns:
      Candidates sorted by fused score descending.
    """
    if not routes:
        return []
    weights = weights or {}
    acc: dict[str, FusedHit] = {}
    for name, ids in routes.items():
        w = float(weights.get(name, 1.0))
        if w == 0 or not ids:
            continue
        for rank, ref in enumerate(ids, start=1):
            hit = acc.get(ref)
            if hit is None:
                hit = FusedHit(ref_id=ref, score=0.0, ranks={})
                acc[ref] = hit
            hit.ranks[name] = rank
            hit.score += w / (k + rank)
    return sorted(acc.values(), key=lambda h: h.score, reverse=True)


__all__: Iterable[str] = ["FusedHit", "fuse"]
