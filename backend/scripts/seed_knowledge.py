"""Taxonomy seed (§3.6, §10 M6).

Inserts ~60 foundational KnowledgePoints covering the junior/senior
math and physics curricula so that `/api/knowledge/tree` is non-empty
on a fresh install. All seeded nodes have `status='live'`; LLM-proposed
nodes remain 'pending' until promoted.

Idempotent: re-running the script is a no-op (checks
`(subject, grade_band, path_cached)` uniqueness).

Usage:
    cd backend
    python -m scripts.seed_knowledge
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import KnowledgePoint
from app.db.session import session_scope


# ── Seed data ────────────────────────────────────────────────────────
# Each row: (subject, grade_band, path as ">"-joined nodes)

SEEDS: list[tuple[str, str, str]] = [
    # Junior math
    ("math", "junior", "代数"),
    ("math", "junior", "代数>整式"),
    ("math", "junior", "代数>方程"),
    ("math", "junior", "代数>方程>一元一次方程"),
    ("math", "junior", "代数>方程>二元一次方程组"),
    ("math", "junior", "代数>方程>一元二次方程"),
    ("math", "junior", "代数>不等式"),
    ("math", "junior", "代数>函数"),
    ("math", "junior", "代数>函数>一次函数"),
    ("math", "junior", "代数>函数>二次函数"),
    ("math", "junior", "几何"),
    ("math", "junior", "几何>三角形"),
    ("math", "junior", "几何>三角形>全等三角形"),
    ("math", "junior", "几何>三角形>相似三角形"),
    ("math", "junior", "几何>四边形"),
    ("math", "junior", "几何>圆"),
    ("math", "junior", "几何>变换"),
    ("math", "junior", "几何>变换>平移"),
    ("math", "junior", "几何>变换>旋转"),
    ("math", "junior", "几何>变换>轴对称"),
    ("math", "junior", "统计与概率"),

    # Senior math
    ("math", "senior", "集合与函数"),
    ("math", "senior", "集合与函数>指数函数"),
    ("math", "senior", "集合与函数>对数函数"),
    ("math", "senior", "三角"),
    ("math", "senior", "三角>三角函数"),
    ("math", "senior", "三角>解三角形"),
    ("math", "senior", "数列"),
    ("math", "senior", "数列>等差数列"),
    ("math", "senior", "数列>等比数列"),
    ("math", "senior", "不等式"),
    ("math", "senior", "立体几何"),
    ("math", "senior", "解析几何"),
    ("math", "senior", "解析几何>直线"),
    ("math", "senior", "解析几何>圆"),
    ("math", "senior", "解析几何>椭圆"),
    ("math", "senior", "解析几何>双曲线"),
    ("math", "senior", "解析几何>抛物线"),
    ("math", "senior", "向量"),
    ("math", "senior", "导数与积分"),
    ("math", "senior", "概率与统计"),

    # Junior physics
    ("physics", "junior", "力学"),
    ("physics", "junior", "力学>力与运动"),
    ("physics", "junior", "力学>压强与浮力"),
    ("physics", "junior", "力学>简单机械"),
    ("physics", "junior", "热学"),
    ("physics", "junior", "光学"),
    ("physics", "junior", "电学"),
    ("physics", "junior", "电学>欧姆定律"),
    ("physics", "junior", "电学>电功与电热"),

    # Senior physics
    ("physics", "senior", "力学"),
    ("physics", "senior", "力学>运动学"),
    ("physics", "senior", "力学>牛顿运动定律"),
    ("physics", "senior", "力学>功与能"),
    ("physics", "senior", "力学>动量"),
    ("physics", "senior", "力学>万有引力与天体运动"),
    ("physics", "senior", "电磁学"),
    ("physics", "senior", "电磁学>静电场"),
    ("physics", "senior", "电磁学>恒定电流"),
    ("physics", "senior", "电磁学>磁场"),
    ("physics", "senior", "电磁学>电磁感应"),
    ("physics", "senior", "热学"),
    ("physics", "senior", "光学"),
    ("physics", "senior", "近代物理"),
]


async def seed() -> int:
    """Insert seeds if missing; return number of rows created."""
    created = 0
    async with session_scope() as session:
        # Group by (subject, grade_band) and walk each path, so parents
        # are always inserted before children.
        for subject, grade_band, path in SEEDS:
            parts = path.split(">")
            parent_id = None
            for i in range(len(parts)):
                path_cached = ">".join(parts[: i + 1])
                stmt = select(KnowledgePoint).where(
                    KnowledgePoint.subject == subject,
                    KnowledgePoint.grade_band == grade_band,
                    KnowledgePoint.path_cached == path_cached,
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing is not None:
                    parent_id = existing.id
                    # Ensure seeded nodes are live even if they were
                    # previously created as pending by the LLM path.
                    if existing.status != "live":
                        existing.status = "live"
                    continue
                node = KnowledgePoint(
                    parent_id=parent_id,
                    name_cn=parts[i],
                    path_cached=path_cached,
                    subject=subject,
                    grade_band=grade_band,
                    status="live",
                    seen_count=0,
                )
                session.add(node)
                await session.flush()
                parent_id = node.id
                created += 1
    return created


def main() -> None:
    n = asyncio.run(seed())
    print(f"Seeded {n} new knowledge points (existing ones promoted to live).")


if __name__ == "__main__":
    main()
