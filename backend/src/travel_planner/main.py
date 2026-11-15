from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from travel_planner.api.middleware import BodyLimitMiddleware
from travel_planner.api.routes import router
from travel_planner.config import Settings
from travel_planner.domain.errors import AppError
from travel_planner.knowledge.service import KnowledgeService
from travel_planner.maintenance.validation import RESTORE_MARKER
from travel_planner.planner.editor import ItineraryEditor
from travel_planner.planner.executor import PlanExecutor
from travel_planner.planner.storage import JobStore
from travel_planner.planner.workflow import Workflow
from travel_planner.providers.services import Services
from travel_planner.storage.database import Database
from travel_planner.storage.instance_lock import instance_lock
from travel_planner.storage.repository import Repository


def create_app(
    settings: Settings | None = None, *, provider_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with instance_lock(settings.data_dir):
            if (settings.data_dir / RESTORE_MARKER).exists():
                raise RuntimeError("数据恢复未完成，请使用另一空目录或空卷重新恢复后启动")
            database = Database(settings.data_dir / "app.sqlite3")
            database.migrate()
            repository = Repository(database)
            repository.initialize()
            knowledge = KnowledgeService(database, settings.data_dir)
            app.state.settings = settings
            app.state.db = database
            app.state.repository = repository
            app.state.knowledge = knowledge
            knowledge.start()
            try:
                async with httpx.AsyncClient(
                    timeout=settings.provider_timeout_seconds,
                    follow_redirects=False,
                    transport=provider_transport,
                ) as client:
                    app.state.services = Services(settings, client)
                    jobs = JobStore(database)
                    planner = PlanExecutor(
                        jobs,
                        Workflow(jobs, repository, knowledge, app.state.services),
                        ItineraryEditor(jobs, app.state.services),
                    )
                    app.state.jobs = jobs
                    app.state.planner = planner
                    await planner.start()
                    try:
                        yield
                    finally:
                        await planner.close()
            finally:
                await run_in_threadpool(knowledge.close)

    app = FastAPI(
        title="旅行规划 API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(router)

    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        return JSONResponse({"error": exc.payload()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        return JSONResponse(
            {
                "error": {
                    **AppError(
                        "validation_error", "输入格式无效，请检查字段、日期范围与整数金额", 422
                    ).payload(),
                    "fields": fields,
                    "details": [
                        {
                            "field": ".".join(str(part) for part in error["loc"]),
                            "message": error["msg"],
                        }
                        for error in exc.errors()
                    ],
                }
            },
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return JSONResponse(
            {
                "error": AppError(
                    code,
                    "接口或文件不存在" if exc.status_code == 404 else "请求无法处理",
                    exc.status_code,
                ).payload()
            },
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return JSONResponse(
            {
                "error": AppError(
                    "internal_error", "服务处理失败，请稍后重试", 500, retryable=True
                ).payload()
            },
            status_code=500,
        )

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise AppError("not_found", "接口不存在", 404)
        root = settings.web_dist_dir.resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise AppError("not_found", "文件不存在", 404)
        if path and candidate.is_file():
            return FileResponse(candidate)
        index = root / "index.html"
        if index.is_file() and not Path(path).suffix:
            return FileResponse(index)
        if not path:
            return {"service": "旅行规划 API", "docs": "/api/docs"}
        raise AppError("not_found", "文件不存在", 404)

    return app


app = create_app()
