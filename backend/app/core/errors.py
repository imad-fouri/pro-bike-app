"""Consistent error envelope: {error: {code, message, details}}."""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        # ctx may hold non-serializable objects (Decimal, ValueError) — sanitize.
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "Invalid request.",
                {"errors": jsonable_encoder(exc.errors())},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            429: "RATE_LIMITED",
        }
        code = mapping.get(exc.status_code, "HTTP_ERROR")
        message = str(exc.detail)
        details: dict = {}
        if isinstance(exc.detail, dict):
            # Services raise {code, message} — keep them structured so clients
            # can branch on ROUTE_NOT_FOUND / VERSION_CONFLICT instead of prose.
            code = str(exc.detail.get("code") or code)
            message = str(exc.detail.get("message") or message)
            details = dict(exc.detail)  # legacy readers look at details.message
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message, details),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces; log full traceback server-side.
        import logging

        logging.getLogger("cyclecoach").exception("unhandled: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Something went wrong."),
        )
