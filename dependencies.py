import logging
import sqlite3
from fastapi import Request
from fastapi.templating import Jinja2Templates
from config import TEMPLATES_DIR, DATABASE_PATH
from database import get_db
from geoip import ip_region

logger = logging.getLogger(__name__)


async def get_boards():
    """获取前台显示的板块列表（过滤掉隐藏的）"""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM boards WHERE hidden IS NULL OR hidden=0 ORDER BY sort")
        return [dict(r) for r in await cursor.fetchall()]


def inject_globals(request: Request):
    """注入全局变量到所有模板上下文"""
    ctx = {}
    # 注入板块（过滤隐藏的）
    try:
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM boards WHERE hidden IS NULL OR hidden=0 ORDER BY sort")
        ctx["boards"] = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception:
        ctx["boards"] = []
    # 检测子域名
    host = request.headers.get("host", "")
    ctx["is_mobile_site"] = host.startswith("phone.") or "phone.cadchajian" in host
    return ctx


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR),
    context_processors=[inject_globals],
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
        # 今日 PV
        cursor = await db.execute("SELECT count FROM page_views WHERE date=?", (today,))
        row = await cursor.fetchone()
        today_count = row["count"] if row else 0
        # 昨日 PV
        cursor = await db.execute("SELECT count FROM page_views WHERE date=?", (yesterday,))
        row = await cursor.fetchone()
        yesterday_count = row["count"] if row else 0
        # 总计 PV
        cursor = await db.execute("SELECT COALESCE(SUM(count),0) FROM page_views")
        total = (await cursor.fetchone())[0]
        # 近 7 天 PV
        cursor = await db.execute(
            "SELECT date, count FROM page_views WHERE date >= ? ORDER BY date",
            ((date.today() - timedelta(days=6)).isoformat(),),
        )
        daily = [dict(r) for r in await cursor.fetchall()]

        # ── 访客统计 ──
        # 总独立访客
        cursor = await db.execute("SELECT COUNT(*) FROM visitor_ips")
        total_visitors = (await cursor.fetchone())[0]
        # 今日新访客
        cursor = await db.execute(
            "SELECT COUNT(*) FROM visitor_ips WHERE date(first_visited_at) = ?", (today,)
        )
        today_new_visitors = (await cursor.fetchone())[0]
        # 本月新访客
        month_start = today[:7] + "-01"
        cursor = await db.execute(
            "SELECT COUNT(*) FROM visitor_ips WHERE date(first_visited_at) >= ?", (month_start,)
        )
        month_new_visitors = (await cursor.fetchone())[0]
        # 近 7 天新访客趋势
        cursor = await db.execute(
            """SELECT date(first_visited_at) as d, COUNT(*) as c
               FROM visitor_ips
               WHERE date(first_visited_at) >= ?
               GROUP BY d ORDER BY d""",
            ((date.today() - timedelta(days=6)).isoformat(),),
        )
        nv_map = {r["d"]: r["c"] for r in await cursor.fetchall()}
        # 与 daily 的日期序列对齐（某天无新访客补 0），避免双系列折线索引错位
        new_visitor_trend = [{"date": d["date"], "count": nv_map.get(d["date"], 0)} for d in daily]

        # ── 插件浏览统计 ──
        cursor = await db.execute("SELECT COALESCE(SUM(view_count),0) FROM plugins")
        total_plugin_views = (await cursor.fetchone())[0]

        # ── 网盘点击统计 ──
        cursor = await db.execute("SELECT COALESCE(SUM(netdisk_clicks),0) FROM plugins")
        total_netdisk_clicks = (await cursor.fetchone())[0]

        # ── 板块条目点击统计 ──
        cursor = await db.execute("SELECT COALESCE(SUM(clicks),0) FROM board_items")
        total_board_clicks = (await cursor.fetchone())[0]

        # ── 各板块点击统计 ──
        cursor = await db.execute(
            """SELECT b.name, COALESCE(SUM(bi.clicks),0) as clicks
               FROM boards b
               LEFT JOIN board_items bi ON bi.board_id = b.id
               GROUP BY b.id ORDER BY b.sort"""
        )
        board_clicks = [dict(r) for r in await cursor.fetchall()]

        # ── 近 7 天下载趋势（口径与每日统计一致：直接下载 + 网盘点击/5） ──
        # 每日总量
        cursor = await db.execute(
            """SELECT date(created_at) as d,
                      SUM(CASE WHEN event_type='download' THEN 1 ELSE 0 END) as dl,
                      SUM(CASE WHEN event_type='netdisk' THEN 1 ELSE 0 END) as nd
               FROM click_log
               WHERE date(created_at) >= ?
               GROUP BY d ORDER BY d""",
            ((date.today() - timedelta(days=6)).isoformat(),),
        )
        trend_rows = [dict(r) for r in await cursor.fetchall()]
        download_trend = []
        for r in trend_rows:
            # 该日各 IP 的下载量（同一口径）
            cursor = await db.execute(
                """SELECT ip_address as ip,
                          SUM(CASE WHEN event_type='download' THEN 1 ELSE 0 END) as dl,
                          SUM(CASE WHEN event_type='netdisk' THEN 1 ELSE 0 END) as nd
                   FROM click_log
                   WHERE date(created_at)=? AND ip_address != ''
                   GROUP BY ip_address
                   ORDER BY (SUM(CASE WHEN event_type='download' THEN 1 ELSE 0 END)
                             + SUM(CASE WHEN event_type='netdisk' THEN 1 ELSE 0 END) / 5) DESC""",
                (r["d"],),
            )
            ips = []
            for rr in await cursor.fetchall():
                count = rr["dl"] + rr["nd"] // 5
                if count > 0:
                    ips.append({"ip": rr["ip"], "count": count, "region": ip_region(rr["ip"])})
            # 中国大陆优先，组内按下载量降序
            ips.sort(key=lambda x: (x["region"] != "中国大陆", -x["count"]))
            cn_count = sum(i["count"] for i in ips if i["region"] == "中国大陆")
            other_count = sum(i["count"] for i in ips if i["region"] == "其他地区")
            download_trend.append({
                "date": r["d"], "count": r["dl"] + r["nd"] // 5,
                "cn_count": cn_count, "other_count": other_count,
                "ips": ips,
            })

    return {
        "today": today_count,
        "yesterday": yesterday_count,
        "total": total,
        "daily": daily,
        "total_visitors": total_visitors,
        "today_new_visitors": today_new_visitors,
        "month_new_visitors": month_new_visitors,
        "new_visitor_trend": new_visitor_trend,
        "total_plugin_views": total_plugin_views,
        "total_netdisk_clicks": total_netdisk_clicks,
        "total_board_clicks": total_board_clicks,
        "board_clicks": board_clicks,
        "download_trend": download_trend,
    }


async def record_click_event(event_type: str, target_id: int = 0, target_type: str = "",
                              ip_address: str = ""):
    """记录点击事件到 click_log 表"""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO click_log (event_type, target_id, target_type, ip_address) VALUES (?, ?, ?, ?)",
            (event_type, target_id, target_type, ip_address),
        )
        await db.commit()
