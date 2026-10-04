from enum import Enum

from pydantic import BaseModel, Field


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class ListParams(BaseModel):
    """Base query parameters for every list endpoint."""

    sort_by: str = "created_at"
    sort_order: SortOrder = SortOrder.DESC
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)
