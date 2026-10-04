from enum import Enum
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import SortOrder


def apply_sorting(model, sort_by: str | Enum, sort_order: SortOrder) -> list:
    """ORDER BY clauses, always ending with id as a tiebreaker so pages never skip or repeat rows."""
    field = sort_by.value if isinstance(sort_by, Enum) else sort_by
    column = getattr(model, field)
    primary = column.asc() if sort_order == SortOrder.ASC else column.desc()
    return [primary, model.id.desc()]


async def paginated_select(
    session: AsyncSession, base_query: Select, *, skip: int, limit: int, order_clauses: list
) -> tuple[list[Any], int]:
    """One round-trip: the page of rows AND the total, via COUNT(*) OVER ()."""
    stmt = (
        base_query.add_columns(func.count().over().label("total_count"))
        .order_by(*order_clauses)
        .offset(skip)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    if rows:
        return [row[0] for row in rows], rows[0].total_count
    if skip == 0:
        return [], 0
    # Asked for a page past the end: no row to read the total from, so count separately.
    count_stmt = select(func.count()).select_from(base_query.order_by(None).subquery())
    return [], (await session.scalar(count_stmt)) or 0
