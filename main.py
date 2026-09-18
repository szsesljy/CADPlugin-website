import logging
from contextlib import asynccontextmanager
from pathlib import Path

import time
from collections import OrderedDict

from urllib.parse import quote

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response

from config import BASE_DIR, SESSION_SECRET, LOGS_DIR, STATIC_DIR
from database import get_db, init_database
from routers.upload import router as upload_router
from routers.download import router as download_router
from routers.admin import router as admin_router
from routers.verify import router as verify_router, is_human_verified

# ── IP 黑名单中间件 ──

class BlockedIPMiddleware(BaseHTTPMiddleware):
    """拦截被拉黑 IP 的所有请求"""

    def __init__(self, app):
        super().__init__(app)
        self._cache = OrderedDict()
        self._cache_ttl = 15  # 秒

    async def dispatch(self, request, call_next):
        path = request.url.path
        # 放行：黑名单管理 API、登录、静态文件
        # 放行：管理后台的拉黑 API（否则封错 IP 无法通过网页解封）
        if path.startswith("/admin/api/blocked-ips") or path.startswith("/unblock"):
            return await call_next(request)

        # 获取客户端 IP（支持反向代理）
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = forwarded.split(",")[0].strip() if forwarded else request.client.host

        # 缓存命中 —— 减少 DB 查询
        now = time.time()
        if client_ip in self._cache:
            entry = self._cache[client_ip]
            if now - entry["time"] < self._cache_ttl:
                if entry["blocked"]:
                    return Response("您的 IP 已被封禁", status_code=403)
                return await call_next(request)
            del self._cache[client_ip]

        # 查数据库
        try:
            async with get_db() as db:
                cursor = await db.execute(
                    "SELECT id FROM blocked_ips WHERE ip_address=?", (client_ip,)
                )
                row = await cursor.fetchone()
        except Exception:
            # 数据库异常时放行，避免网站完全不可用
            return await call_next(request)

        is_blocked = row is not None
        self._cache[client_ip] = {"blocked": is_blocked, "time": now}
        # 限制缓存大小
        if len(self._cache) > 10000:
            self._cache.popitem(last=False)

        if is_blocked:
            return Response("您的 IP 已被封禁", status_code=403)

        return await call_next(request)


# ── 人机验证门禁 ──

# 不需要人机验证的路径（验证页本身、静态资源、申请解封、黑名单管理接口等）
_GATE_EXEMPT_PREFIX = ("/verify", "/static", "/unblock", "/admin/api/blocked-ips")
_GATE_EXEMPT_EXACT = {"/ads.txt", "/favicon.ico", "/robots.txt"}
# 搜索引擎蜘蛛/可用性监控直接放行，保证页面收录与监控正常；如需“严格所有访客”，删掉本段判断即可
_SEARCH_ENGINE_HINTS = (
    "googlebot", "bingbot", "baiduspider", "yandex",
    "sogou", "360spider", "bytespider", "yisouspider",
    "uptimerobot", "pingdom", "statuscake", "newrelic",
)


class HumanVerifyMiddleware(BaseHTTPMiddleware):
    """拦截未通过人机验证的访问：浏览器页面跳转验证页，接口请求直接拒绝"""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith(_GATE_EXEMPT_PREFIX) or path in _GATE_EXEMPT_EXACT:
            return await call_next(request)
        ua = (request.headers.get("user-agent") or "").lower()
        if any(hint in ua for hint in _SEARCH_ENGINE_HINTS):
            return await call_next(request)
        if is_human_verified(request.session):
            return await call_next(request)

        # 浏览器直接打开页面/下载 → 先去完成验证，验证通过后自动回到原地址
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            target = request.url.path
            if request.url.query:
                target += "?" + request.url.query
            return RedirectResponse(f"/verify?next={quote(target)}", status_code=302)
        # API / 文件等非页面请求 → 直接拒绝
        return JSONResponse(
            status_code=403,
            content={"error": "请先完成人机验证", "verify_url": "/verify"},
        )


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
    logger.info("CAD 插件平台启动完成")
    yield
    logger.info("CAD 插件平台关闭")


app = FastAPI(title="CAD 插件平台", lifespan=lifespan)

# 中间件注册顺序：越晚注册越靠外、越先执行。
# 最终链路：BlockedIP(黑名单) → Session(解析 session) → HumanVerify(人机验证门禁)
app.add_middleware(HumanVerifyMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=86400)
app.add_middleware(BlockedIPMiddleware)

# 路由
app.include_router(upload_router)
app.include_router(download_router)
app.include_router(admin_router)
app.include_router(verify_router)

# 静态文件
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ads.txt（Google AdSense 验证用，随项目部署到根目录）
@app.get("/ads.txt", response_class=Response)
async def ads_txt():
    ads_file = BASE_DIR / "ads.txt"
    if ads_file.exists():
        return Response(ads_file.read_text(encoding="utf-8"), media_type="text/plain")
    return Response("", status_code=404)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
