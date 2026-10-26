"""FastAPI 入口。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.deps import get_cache, get_store
from app.routers import orders as orders_router
from app.services.orders import OrderRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aeris")

app = FastAPI(
    title="Aeris Order Pricing & Tracking API",
    version="1.0.0",
    description="数据驱动的大麻药房订单核算 + AusPost 运费/轨迹服务",
)

# 开发环境：Vite dev server 在 5173，双端口部署时需要 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders_router.router)


@app.get("/api/health", tags=["ops"], summary="健康检查：数据与凭证配置状态")
async def health() -> Dict[str, Any]:
    settings: Settings = get_settings()
    repo = OrderRepository(settings, get_store(settings), get_cache())
    return await repo.health()


@app.post("/api/cache/clear", tags=["ops"], summary="清空 partner API 结果缓存")
async def clear_cache() -> Dict[str, Any]:
    cache = get_cache()
    size = len(cache)
    cache.clear()
    return {"cleared": size}


# 生产模式：把构建好的前端 dist 挂在根路径（单端口运行）
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> Any:
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Any:
        candidate = _FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
