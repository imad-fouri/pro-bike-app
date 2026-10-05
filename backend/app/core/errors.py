"""Consistent error envelope: {error: {code, message, details}}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

#: Pydantic error keys that are safe to return.
#:
#: An ALLOWLIST, not a denylist. Pydantic can add keys to `errors()` between
#: versions, and a denylist would silently start publishing whatever it added.
_ALLOWED_ERROR_KEYS = ("type", "loc", "msg")


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _safe_errors(exc: RequestValidationError) -> list[dict]:
    """Strip rejected values out of a validation error list.

    Pydantic's `errors()` includes `input`: the value that FAILED validation. On
    this API that is a plaintext `password`, `password_confirm`, `new_password` or
    `refresh_token`, and a response body is not a private channel — reverse
    proxies, load balancers and APM tools all capture bodies by default, and a
    developer reading the client console sees it too. Echoing a credential back
    to a caller that just supplied it protects nobody while multiplying the
    number of places the credential lives.

    Verified before this change: `POST /auth/register` with a short password
    returned `"input": "short"` for both password fields, and `POST /auth/refresh`
    returned the submitted token.

    `type`, `loc` and `msg` are kept: they are what a client needs to attach the
    message to the right form field, and none of them carries user data. `ctx`
    is reduced to numeric constraints only (`min_length`, `ge`, ...) for the same
    reason — pydantic also puts live objects there, which is why this function
    exists at all.

    The rejected value is still available to the developer: it is in the request
    they just sent.
    """
    safe: list[dict] = []
    for raw in exc.errors():
        entry: dict = {}
        for key in _ALLOWED_ERROR_KEYS:
            if key in raw:
                entry[key] = raw[key]
        # Numeric constraints only. `min_length`, `ge`, `le` are what let a client
        # pre-empt the error, and a number cannot carry a credential. Everything
        # else in `ctx` is dropped, because pydantic also puts live objects there
        # — which is the non-serializability this function exists to solve.
        ctx = raw.get("ctx")
        if isinstance(ctx, dict):
            numbers = {
                key: value
                for key, value in ctx.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            if numbers:
                entry["ctx"] = numbers
        safe.append(entry)
    return safe


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR", "Invalid request.", {"errors": _safe_errors(exc)}
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
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces; log full traceback server-side.
        #
        # `type(exc).__name__` rather than `exc` itself. A SQLAlchemy exception
        # renders as the statement PLUS its bound parameters, and this schema puts
        # an email address and an Argon2id hash in those parameters. The engine
        # also sets hide_parameters=True as a second layer, because one unwrapped
        # `log.exception("%s", exc)` anywhere would otherwise undo it.
        import logging

        logging.getLogger("cyclecoach").exception("unhandled: %s", type(exc).__name__)
        response = JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Something went wrong."),
        )
        # The correlation middleware sits INSIDE Starlette's ServerErrorMiddleware,
        # so an unhandled exception propagates past it and its `headers[...]` never
        # runs. Without this the id is in the log but unreachable from the client
        # on exactly the requests worth tracing.
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response
