import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import SESSION_SECRET, LOGS_DIR, STATIC_DIR
from database import init_database
from routers.upload import router as upload_router
from routers.download import router as download_router
from routers.admin import router as admin_router

# ── 日志 ──
LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(str(LOGS_DIR / "app.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    logger.info("CAD Plugin Platform started")
    yield
    logger.info("CAD Plugin Platform stopped")


app = FastAPI(title="CAD Plugin Platform", lifespan=lifespan)

# Session（24 小时过期）
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=86400)

# 路由
app.include_router(upload_router)
app.include_router(download_router)
app.include_router(admin_router)

# 静态文件
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
