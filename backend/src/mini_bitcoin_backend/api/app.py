"""FastAPI 애플리케이션, 오류 응답 형식, 빌드된 화면 제공."""

import argparse
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.types import Scope

from .. import __version__
from ..core.errors import LabError
from ..service.registry import LabRegistry
from .routes import create_router

FRONTEND_DIR_ENV = "MINI_BITCOIN_FRONTEND_DIR"
BUILD_HINT = "frontend 폴더에서 `npm.cmd run build`를 실행한 뒤 백엔드를 다시 시작하세요."


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _has_page(directory: Path) -> bool:
    return (directory / "index.html").is_file()


def default_frontend_dir() -> Path | None:
    """빌드된 화면 폴더. 환경 변수로 지정하지 않으면 저장소의 frontend/dist를 찾는다.

    index.html이 없으면 None을 돌려주고, 이때 백엔드는 API만 제공한다.
    """
    configured = os.environ.get(FRONTEND_DIR_ENV)
    # backend/src/mini_bitcoin_backend/api/app.py → 저장소 루트
    directory = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[4] / "frontend" / "dist"
    )
    return directory if _has_page(directory) else None


class FrontendFiles(StaticFiles):
    """빌드된 화면 파일. 이름에 해시가 없는 HTML은 매번 확인해 새 빌드가 바로 보이게 한다."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(registry: LabRegistry | None = None, frontend_dir: Path | None = None) -> FastAPI:
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
    # /api 경로를 먼저 등록했으므로 API가 우선한다. 그 밖의 경로는 빌드된 화면에서 찾는다.
    if frontend_dir is not None:
        app.mount("/", FrontendFiles(directory=frontend_dir, html=True), name="frontend")

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


app = create_app(frontend_dir=default_frontend_dir())


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini Bitcoin Education Lab 백엔드")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--frontend",
        type=Path,
        metavar="DIR",
        help="빌드된 화면 폴더 (기본: 저장소의 frontend/dist)",
    )
    parser.add_argument("--api-only", action="store_true", help="화면을 제공하지 않고 API만 제공")
    args = parser.parse_args()
    if args.frontend is not None and not _has_page(args.frontend):
        parser.error(f"{args.frontend}에 index.html이 없습니다. {BUILD_HINT}")

    frontend_dir = None if args.api_only else (args.frontend or default_frontend_dir())
    if frontend_dir is not None:
        print(
            f"화면과 API를 함께 제공합니다: http://{args.host}:{args.port}  (화면: {frontend_dir})"
        )
    elif args.api_only:
        print("API만 제공합니다(--api-only). 화면은 frontend의 개발 서버로 실행하세요.")
    else:
        print(f"빌드된 화면이 없어 API만 제공합니다. 한 서버로 실행하려면 {BUILD_HINT}")
    uvicorn.run(create_app(frontend_dir=frontend_dir), host=args.host, port=args.port)
