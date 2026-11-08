from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from awning.context import AppContext

health_router = APIRouter()


@health_router.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


def create_app(ctx: AppContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup work from later tasks hooks in here.
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.ctx = ctx

    for router in (health_router,):  # later tasks append routers here
        app.include_router(router)

    # Catch-all mounts (e.g. the MCP app at "/") must stay LAST.
    return app
