"""Map service exceptions to HTTP status codes, in one place."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.payments import InvalidState, NotFound
from app.services.ports import GatewayError, GatewayUnreachable


def install_error_handlers(app: FastAPI) -> None:
    def handler(status: int):
        async def respond(request: Request, error: Exception) -> JSONResponse:
            body: dict[str, object] = {"error": type(error).__name__, "message": str(error)}
            if isinstance(error, GatewayError):
                body["paypal_debug_id"] = error.debug_id
            return JSONResponse(body, status_code=status)

        return respond

    app.add_exception_handler(NotFound, handler(404))
    app.add_exception_handler(InvalidState, handler(409))
    app.add_exception_handler(GatewayError, handler(502))  # PayPal said no
    app.add_exception_handler(GatewayUnreachable, handler(504))  # PayPal didn't answer
