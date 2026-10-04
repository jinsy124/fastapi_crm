from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


# ==================== Exception classes ====================

class AppException(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    message = "Bad request"

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None):
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        super().__init__(self.message)


class UnauthorizedException(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Not authenticated"


class ForbiddenException(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You don't have permission to do this"


class NotFoundException(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Not found"


class ConflictException(AppException):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Conflict"


class UnprocessableException(AppException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "The request has invalid fields"


# ==================== Response helpers ====================

def error_response(status_code: int, code: str, message: str, details: Any = None, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
        headers=headers,
    )


def _validation_details(errors) -> list[dict]:
    details = []
    for err in errors:
        loc = list(err.get("loc", ()))
        if loc and loc[0] in ("body", "query", "path"):
            loc = loc[1:]
        details.append({
            "field": ".".join(str(part) for part in loc) or None,
            "message": err["msg"].removeprefix("Value error, "),
        })
    return details


# ==================== Handlers ====================

async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "validation_error", "The request has invalid fields", _validation_details(exc.errors()))


async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return error_response(422, "validation_error", "The request has invalid fields", _validation_details(exc.errors()))


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("Integrity error on %s %s: %s", request.method, request.url.path, exc.orig)
    return error_response(409, "conflict", "This conflicts with existing data")


async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    logger.error("Database unavailable on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(503, "service_unavailable", "The database is unavailable. Please try again.")


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "Something went wrong. Please try again later.")


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return error_response(400, "bad_request", str(exc))


async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return error_response(403, "forbidden", "You don't have permission to do this")


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return error_response(exc.status_code, codes.get(exc.status_code, "http_error"), message,
                          headers=getattr(exc, "headers", None))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "Something went wrong. Please try again later.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(ValidationError, pydantic_validation_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(OperationalError, operational_error_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(PermissionError, permission_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
