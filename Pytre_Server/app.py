import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from Pytre_Core.context import AppContext
from Pytre_Core.logs_central import ServerDriven

from Pytre_Server.routes.admin_auth import AdminAuthRequired, admin_auth_required_handler
from Pytre_Server.routes.admin_router import router as admin_router
from Pytre_Server.routes.client import router as client_router
from Pytre_Server.query_catalog import QueryCatalog
from Pytre_Server.server_state import ServerResources, ServerState


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Context function to manage server shutdown tasks"""
    yield

    state: ServerState = app.state.server

    await state.resources.shutdown_drivers()

    if state.resources.execution_logs is not None:
        state.resources.execution_logs.stop()


def create_app(
    master_key: str, chunk_size: int = 2000, max_concurrency_per_user: int = 4, heartbeat_secs: int = 30
) -> FastAPI:
    """Build and configure the FastAPI app"""

    app = FastAPI(title="Pytre Server", lifespan=_lifespan)
    _app_ctx = AppContext(master_key=master_key)

    execution_logs: ServerDriven | None = None
    if _app_ctx.settings.logs_are_on:
        execution_logs = ServerDriven(logs_folder=_app_ctx.settings.logs_folder)

    app.state.server = ServerState(
        app_ctx=_app_ctx,
        query_catalog=QueryCatalog.load(_app_ctx.settings.queries_folder),
        chunk_size=chunk_size,
        max_concurrency_per_user=max_concurrency_per_user,
        heartbeat_secs=heartbeat_secs,
        resources=ServerResources(execution_logs=execution_logs),
    )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/admin")

    app.include_router(client_router)
    app.include_router(admin_router)
    app.add_exception_handler(AdminAuthRequired, admin_auth_required_handler)

    return app


if __name__ == "__main__":
    import uvicorn
    from Pytre_Server.logs import setup_file_logging, setup_terminal_logging
    from Pytre_Server.master_key import get_master_key_interactive

    setup_terminal_logging(logging.DEBUG)

    log_file = None  # specify a file to allow file logging
    if log_file is not None:
        setup_file_logging(log_file)

    master_key = get_master_key_interactive()
    if master_key is None:
        print("Startup cancelled: no master password configured.")
    else:
        uvicorn.run(create_app(master_key=master_key), host="127.0.0.1", port=8000)
