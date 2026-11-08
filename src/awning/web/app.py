from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from awning.context import AppContext
from awning.mcp_server import build_mcp, build_transport_security
from awning.seed import DEFAULT_SEED_PATH, seed_demo_shop
from awning.web.routes_shop import WEB_DIR
from awning.web.routes_shop import router as shop_router

health_router = APIRouter()


@health_router.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


def create_app(ctx: AppContext) -> FastAPI:
    mcp = build_mcp(ctx)
    mcp_app = mcp.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=build_transport_security(ctx)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        seed_demo_shop(
            ctx.catalog,
            DEFAULT_SEED_PATH,
            payee_merchant_id=ctx.settings.demo_payee_merchant_id,
            payee_email=ctx.settings.demo_payee_email,
        )
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.ctx = ctx

    for router in (health_router, shop_router):  # later tasks append routers here
        app.include_router(router)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    # Catch-all mount: serves exactly /mcp and shadows anything registered after it, so keep LAST.
    app.mount("/", mcp_app)
    return app
