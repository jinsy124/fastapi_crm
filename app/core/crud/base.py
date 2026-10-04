import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """Generic database operations. NEVER commits: the service owns the transaction."""

    def __init__(self, model: type[ModelType]):
        self.model = model

    async def get(self, session: AsyncSession, id: uuid.UUID) -> ModelType | None:
        return await session.get(self.model, id)

    async def create(self, session: AsyncSession, *, obj_in: CreateSchemaType | dict[str, Any]) -> ModelType:
        data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump()
        db_obj = self.model(**data)
        session.add(db_obj)
        await session.flush()          # sends the INSERT, so DB errors appear here
        await session.refresh(db_obj)  # reloads it, including joined relationships
        return db_obj

    async def update(
        self, session: AsyncSession, *, db_obj: ModelType, obj_in: UpdateSchemaType | dict[str, Any]
    ) -> ModelType:
        data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(db_obj, field, value)
        await session.flush()
        await session.refresh(db_obj)
        return db_obj
