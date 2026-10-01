"""FastAPI 애플리케이션과 오류 응답 형식."""

import argparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .. import __version__
from ..core.errors import LabError
from ..service.registry import LabRegistry
from .routes import create_router


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(registry: LabRegistry | None = None) -> FastAPI:
    app = FastAPI(
        title="Mini Bitcoin Education Lab API",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    # 빈 저장소는 길이가 0이라 거짓으로 평가되므로 None인지 직접 확인한다.
    app.state.registry = LabRegistry() if registry is None else registry
    app.include_router(create_router(app.state.registry))

    @app.exception_handler(LabError)
    def lab_error(_: Request, error: LabError) -> JSONResponse:
        return _error(error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error(400, "INVALID_REQUEST", "요청 형식이 올바르지 않습니다.")

    @app.exception_handler(HTTPException)
    def http_error(_: Request, error: HTTPException) -> JSONResponse:
        return _error(error.status_code, "HTTP_ERROR", str(error.detail))

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini Bitcoin Education Lab 백엔드")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
