from enum import Enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Every model inherits from this. Alembic reads Base.metadata."""


def enum_values(enum_cls: type[Enum]) -> list[str]:
    """Store an Enum's lowercase *values* in the database, not its UPPER member names."""
    return [member.value for member in enum_cls]
