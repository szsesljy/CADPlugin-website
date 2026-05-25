import logging
from fastapi.templating import Jinja2Templates
from config import TEMPLATES_DIR
from database import get_db

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


async def get_donation_info():
    """获取打赏配置信息"""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM donation_config WHERE id=1")
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"qrcode_path": None, "title": "支持开发者", "enabled": 0}


async def record_page_view():
    """记录一次页面访问"""
    from datetime import date
    today = date.today().isoformat()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO page_views (date, count) VALUES (?, 1)
               ON CONFLICT(date) DO UPDATE SET count = count + 1""",
            (today,),
        )
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
