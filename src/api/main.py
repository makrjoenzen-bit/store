import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.core.database import engine, Base
from src.api.websocket.manager import ws_manager
from src.api.routes import auth, suppliers, products, pricing, orders, dashboard, bot_config, webhooks, support, treasury, customers, marketing, miniapp, reseller_api
from src.config import settings, validate_production_secrets


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_secrets()
    # Startup: Ensure database schema exists
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema validated.")

    # Start WebSocket Redis listener
    ws_task = asyncio.create_task(ws_manager.start_redis_listener())
    logger.info("Real-time WebSocket event listener started.")

    # Async supplier delivery poller (PENDING_SUPPLIER_DELIVERY → COMPLETED)
    from src.workers.delivery_poller import delivery_poller
    delivery_task = asyncio.create_task(delivery_poller.run())
    logger.info("Delivery poller background loop started.")

    yield

    # Shutdown
    delivery_task.cancel()
    ws_task.cancel()
    await engine.dispose()
    logger.info("Cleaned up engine and background tasks.")


app = FastAPI(
    title="JIT Digital Aggregator API & Control Plane",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS — credentials required for HttpOnly auth cookie
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register route modules under /api
app.include_router(auth.router, prefix="/api")
app.include_router(suppliers.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(pricing.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(orders.sham_cash_router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(bot_config.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")
app.include_router(support.router, prefix="/api")
app.include_router(treasury.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
app.include_router(marketing.router, prefix="/api")
app.include_router(miniapp.router, prefix="/api")
app.include_router(reseller_api.router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time streaming socket for Dashboard live metrics and order updates."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep socket alive and accept ping/pong
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await ws_manager.disconnect(websocket)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "jit-aggregator-api"}
