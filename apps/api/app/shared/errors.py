import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from .middleware import request_id_var

logger = logging.getLogger("app.errors")


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict | None = None


def _current_request_id() -> str | None:
    value = request_id_var.get()
    return None if value == "-" else value


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorBody(
                code="HTTP_ERROR",
                message=str(exc.detail),
                request_id=_current_request_id(),
            ).model_dump(exclude_none=True),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=ErrorBody(
                code="VALIDATION_ERROR",
                message="请求参数校验失败",
                request_id=_current_request_id(),
                details={"errors": jsonable_encoder(exc.errors())},
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        # The JSON formatter records only the exception type: tracebacks often
        # carry request/provider values and are not safe local telemetry.
        logger.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content=ErrorBody(
                code="INTERNAL_ERROR",
                message="服务器内部错误",
                request_id=_current_request_id(),
            ).model_dump(exclude_none=True),
        )
