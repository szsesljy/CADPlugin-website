import logging
import sqlite3
from fastapi import Request
from fastapi.templating import Jinja2Templates
from config import TEMPLATES_DIR, DATABASE_PATH
from database import get_db

logger = logging.getLogger(__name__)


async def get_boards():
    """获取所有板块列表"""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM boards ORDER BY sort")
        return [dict(r) for r in await cursor.fetchall()]


def inject_boards(request: Request):
    """注入板块数据到所有模板上下文（同步，Jinja2Templates context_processor 要求同步）"""
    try:
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM boards ORDER BY sort")
        boards = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return {"boards": boards}
    except Exception:
        return {"boards": []}


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[inject_boards],
)


async def get_donation_info():
    """获取打赏配置信息"""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM donation_config WHERE id=1")
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"qrcode_path": None, "title": "支持开发者", "enabled": 0}


async def record_page_view(request: Request = None):
    """记录一次页面访问及访客 IP"""
    from datetime import date
    today = date.today().isoformat()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO page_views (date, count) VALUES (?, 1)
               ON CONFLICT(date) DO UPDATE SET count = count + 1""",
            (today,),
        )
        # 记录访客 IP
        if request:
            forwarded = request.headers.get("x-forwarded-for")
            ip = forwarded.split(",")[0].strip() if forwarded else request.client.host
            cursor = await db.execute("SELECT id FROM visitor_ips WHERE ip_address=?", (ip,))
            if await cursor.fetchone():
                await db.execute(
                    "UPDATE visitor_ips SET visit_count=visit_count+1, last_visited_at=datetime('now','localtime') WHERE ip_address=?",
                    (ip,),
                )
            else:
                await db.execute("INSERT INTO visitor_ips (ip_address) VALUES (?)", (ip,))
        await db.commit()


async def get_page_view_stats():
    """获取访问统计（今日、昨日、总览）"""
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    async with get_db() as db:
        # 今日
        cursor = await db.execute("SELECT count FROM page_views WHERE date=?", (today,))
        row = await cursor.fetchone()
        today_count = row["count"] if row else 0
        # 昨日
        cursor = await db.execute("SELECT count FROM page_views WHERE date=?", (yesterday,))
        row = await cursor.fetchone()
        yesterday_count = row["count"] if row else 0
        # 总计
        cursor = await db.execute("SELECT COALESCE(SUM(count),0) FROM page_views")
        total = (await cursor.fetchone())[0]
        # 近 7 天
        cursor = await db.execute(
            "SELECT date, count FROM page_views WHERE date >= ? ORDER BY date",
            ((date.today() - timedelta(days=6)).isoformat(),),
        )
        daily = [dict(r) for r in await cursor.fetchall()]
    return {"today": today_count, "yesterday": yesterday_count, "total": total, "daily": daily}
