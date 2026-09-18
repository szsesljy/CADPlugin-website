import random
import time
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from config import STORAGE_ROOT, ITEMS_PER_PAGE
from database import get_db
from dependencies import templates, get_donation_info, logger, record_page_view, record_click_event

router = APIRouter()


def _client_ip(request: Request) -> str:
    """从请求中提取客户端 IP（优先取 x-forwarded-for 第一项）"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.1f} KB"


async def _get_categories(db):
    """获取完整分类树"""
    cursor = await db.execute("SELECT * FROM categories ORDER BY sort")
    cats = []
    for r in await cursor.fetchall():
        c = dict(r)
        tc = await db.execute("SELECT * FROM tags WHERE category_id=? ORDER BY sort", (c["id"],))
        c["tags"] = [dict(t) for t in await tc.fetchall()]
        cats.append(c)
    return cats


async def _get_plugin_tags(db, plugin_id: int) -> list[dict]:
    cursor = await db.execute(
        """SELECT t.id, t.name, c.name AS category_name
           FROM tags t
           JOIN categories c ON c.id = t.category_id
           JOIN plugin_tags pt ON pt.tag_id = t.id
           WHERE pt.plugin_id = ?""",
        (plugin_id,),
    )
    return [dict(r) for r in await cursor.fetchall()]


# ── 首页 ──

@router.get("/")
async def home(
    request: Request,
    search: str = "",
    category: int = 0,
    tag: int = 0,
    sort: str = "newest",
    page: int = 1,
):
    await record_page_view(request)
    async with get_db() as db:
        categories = await _get_categories(db)
        conditions = ["p.status='approved'", "p.disabled=0"]
        params: list = []

        if search:
            conditions.append("(p.name LIKE ? OR p.description LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        # 按标签筛选
        if tag > 0:
            conditions.append(
                "p.id IN (SELECT plugin_id FROM plugin_tags WHERE tag_id=?)"
            )
            params.append(tag)
        elif category > 0:
            conditions.append(
                "p.id IN (SELECT pt.plugin_id FROM plugin_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.category_id=?)"
            )
            params.append(category)

        where = " AND ".join(conditions)

        count_cursor = await db.execute(
            f"SELECT COUNT(*) FROM plugins p WHERE {where}", params
        )
        total = (await count_cursor.fetchone())[0]

        order = "p.created_at DESC"
        if sort == "downloads":
            order = "p.downloads DESC"
        elif sort == "name":
            order = "p.name ASC"

        cursor = await db.execute(
            f"SELECT p.* FROM plugins p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, ITEMS_PER_PAGE, (page - 1) * ITEMS_PER_PAGE],
        )
        plugins_raw = await cursor.fetchall()

        # 为每个插件组装标签
        plugins = []
        for r in plugins_raw:
            p = dict(r)
            p["tags"] = await _get_plugin_tags(db, p["id"])
            p["size_str"] = _format_size(p["file_size"])
            plugins.append(p)

        # 热门下载排行榜
        hot_cursor = await db.execute(
            "SELECT * FROM plugins WHERE status='approved' AND disabled=0 ORDER BY downloads DESC LIMIT 5"
        )
        hot_plugins = [dict(r) for r in await hot_cursor.fetchall()]

    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    donation = await get_donation_info()

    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "plugins": plugins,
        "hot_plugins": hot_plugins,
        "categories": categories,
        "search": search,
        "category": category,
        "tag": tag,
        "sort": sort,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "donation": donation,
    })


# ── 插件详情页 ──

@router.get("/plugin/{plugin_id}")
async def plugin_detail(request: Request, plugin_id: int):
    await record_page_view(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE id=? AND status='approved' AND disabled=0",
            (plugin_id,),
        )
        plugin = await cursor.fetchone()
        if not plugin:
            raise HTTPException(404, "插件不存在或已下架")

        p = dict(plugin)
        p["tags"] = await _get_plugin_tags(db, p["id"])
        p["size_str"] = _format_size(p["file_size"])

        # 增加浏览次数
        await db.execute(
            "UPDATE plugins SET view_count = COALESCE(view_count, 0) + 1 WHERE id=?",
            (plugin_id,),
        )

        # 查找关联的使用说明
        guide_cursor = await db.execute(
            "SELECT id, title FROM articles WHERE type='guide' AND plugin_id=?",
            (plugin_id,),
        )
        guide_row = await guide_cursor.fetchone()
        p["guide_article"] = dict(guide_row) if guide_row else None

        categories = await _get_categories(db)
        await db.commit()

    donation = await get_donation_info()
    return templates.TemplateResponse(request, "detail.html", {
        "request": request,
        "plugin": p,
        "categories": categories,
        "donation": donation,
    })


# ── 下载 ──

@router.get("/download/{plugin_id}")
async def download_plugin(plugin_id: int, request: Request):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE id=? AND status='approved' AND disabled=0",
            (plugin_id,),
        )
        plugin = await cursor.fetchone()
        if not plugin:
            raise HTTPException(404, "插件不存在或已下架")

        if plugin["download_mode"] == "netdisk_only":
            raise HTTPException(403, "该插件仅支持网盘下载")

        await db.execute("UPDATE plugins SET downloads=downloads+1 WHERE id=?", (plugin_id,))
        await db.execute(
            "INSERT INTO click_log (event_type, target_id, target_type, ip_address) VALUES (?, ?, ?, ?)",
            ("download", plugin_id, "plugin", _client_ip(request)),
        )
        await db.commit()

    file_path = STORAGE_ROOT / plugin["file_path"]
    if not file_path.exists():
        raise HTTPException(404, "文件已丢失")

    logger.info("下载插件: %s (ID: %d)", plugin["name"], plugin_id)
    return FileResponse(str(file_path), filename=plugin["file_name"])


# ── 网盘链接点击追踪 ──

@router.get("/track-netdisk/{plugin_id}")
async def track_netdisk_click(plugin_id: int, request: Request):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT netdisk_url FROM plugins WHERE id=? AND status='approved' AND disabled=0",
            (plugin_id,),
        )
        plugin = await cursor.fetchone()
        if not plugin:
            raise HTTPException(404, "插件不存在或已下架")

        netdisk_url = plugin["netdisk_url"]
        if not netdisk_url:
            raise HTTPException(400, "该插件没有网盘链接")

        # 递增网盘点击计数
        await db.execute(
            "UPDATE plugins SET netdisk_clicks = netdisk_clicks + 1 WHERE id=?",
            (plugin_id,),
        )

        # 每5次网盘点击算作一次下载
        cursor = await db.execute(
            "SELECT netdisk_clicks FROM plugins WHERE id=?", (plugin_id,)
        )
        clicks = (await cursor.fetchone())[0]
        if clicks % 5 == 0:
            await db.execute(
                "UPDATE plugins SET downloads = downloads + 1 WHERE id=?",
                (plugin_id,),
            )
            logger.info("网盘点击满5次，下载+1: 插件ID %d (累计点击 %d)", plugin_id, clicks)

        # 记录点击日志用于每日统计
        await db.execute(
            "INSERT INTO click_log (event_type, target_id, target_type, ip_address) VALUES (?, ?, ?, ?)",
            ("netdisk", plugin_id, "plugin", _client_ip(request)),
        )

        await db.commit()

    return RedirectResponse(url=netdisk_url)


# ── 板块条目点击跟踪 ──

@router.get("/track-board-item/{item_id}")
async def track_board_item_click(request: Request, item_id: int):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT file_url FROM board_items WHERE id=?", (item_id,)
        )
        item = await cursor.fetchone()
        if not item:
            raise HTTPException(404, "条目不存在")

        file_url = item["file_url"]
        await db.execute(
            "UPDATE board_items SET clicks = COALESCE(clicks, 0) + 1 WHERE id=?",
            (item_id,),
        )
        await db.commit()

    # 记录点击事件
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else request.client.host
    await record_click_event("board_item", item_id, "board_item", ip)

    return RedirectResponse(url=file_url)


# ── 综合统计数据 API（用于管理后台） ──

@router.get("/api/stats/overview")
async def api_stats_overview():
    """返回所有统计数据的概览"""
    from dependencies import get_page_view_stats
    return await get_page_view_stats()


# ── 动态板块 ──

@router.get("/board/{slug}")
async def board_list(request: Request, slug: str):
    await record_page_view(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM boards WHERE slug=?", (slug,)
        )
        board = await cursor.fetchone()
        if not board:
            raise HTTPException(404, "板块不存在")
        board = dict(board)
        cursor = await db.execute(
            "SELECT * FROM board_items WHERE board_id=? ORDER BY sort ASC, created_at DESC",
            (board["id"],),
        )
        items = [dict(r) for r in await cursor.fetchall()]
    donation = await get_donation_info()
    return templates.TemplateResponse(request, "board_list.html", {
        "request": request,
        "board": board,
        "items": items,
        "donation": donation,
    })


@router.get("/board/{slug}/{item_id}")
async def board_detail(request: Request, slug: str, item_id: int):
    await record_page_view(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM boards WHERE slug=?", (slug,)
        )
        board = await cursor.fetchone()
        if not board:
            raise HTTPException(404, "板块不存在")
        cursor = await db.execute(
            "SELECT * FROM board_items WHERE id=? AND board_id=?",
            (item_id, board["id"]),
        )
        item = await cursor.fetchone()
        if not item:
            raise HTTPException(404, "条目不存在")
    donation = await get_donation_info()
    return templates.TemplateResponse(request, "board_detail.html", {
        "request": request,
        "board": dict(board),
        "item": dict(item),
        "donation": donation,
    })





# ── 公告页面 ──

@router.get("/notices")
async def notices_page(request: Request, type: str = "notice"):
    await record_page_view(request)
    async with get_db() as db:
        if type == "guide":
            cursor = await db.execute(
                """SELECT a.id, a.title, a.type, a.created_at, a.plugin_id, a.pinned, p.name AS plugin_name,
                          substr(a.content, 1, 200) AS excerpt
                   FROM articles a
                   LEFT JOIN plugins p ON p.id = a.plugin_id
                   WHERE a.type='guide' ORDER BY a.pinned DESC, a.created_at DESC""",
            )
        else:
            cursor = await db.execute(
                """SELECT id, title, type, created_at, pinned, NULL AS plugin_name,
                          substr(content, 1, 200) AS excerpt
                   FROM articles WHERE type='notice' ORDER BY pinned DESC, created_at DESC"""
            )
        articles = [dict(r) for r in await cursor.fetchall()]
    donation = await get_donation_info()
    return templates.TemplateResponse(request, "notices.html", {
        "request": request,
        "articles": articles,
        "current_type": type,
        "donation": donation,
    })


@router.get("/notice/{article_id}")
async def notice_detail(request: Request, article_id: int):
    await record_page_view(request)
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT a.*, p.name AS plugin_name
               FROM articles a
               LEFT JOIN plugins p ON p.id = a.plugin_id
               WHERE a.id=?""",
            (article_id,),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(404, "文章不存在")
    donation = await get_donation_info()
    return templates.TemplateResponse(request, "notice_detail.html", {
        "request": request,
        "article": dict(article),
        "donation": donation,
    })


# ── 文章 API ──

@router.get("/api/articles")
async def api_articles(type: str = "notice"):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM articles WHERE type=? ORDER BY created_at DESC", (type,)
        )
        return [dict(r) for r in await cursor.fetchall()]


# ── 分类 API（前端动态加载） ──

@router.get("/api/categories")
async def api_categories():
    async with get_db() as db:
        return await _get_categories(db)


# ── 留言 ──

@router.get("/api/messages")
async def api_messages(plugin_id: int = 0, article_id: int = 0):
    """获取已审核的留言（含回复，含位置信息）"""
    async with get_db() as db:
        if article_id:
            cursor = await db.execute(
                """SELECT m.*, a.title AS _location FROM messages m
                   LEFT JOIN articles a ON a.id = m.article_id
                   WHERE m.status='approved' AND m.article_id=? ORDER BY m.created_at ASC""",
                (article_id,),
            )
        elif plugin_id:
            cursor = await db.execute(
                """SELECT m.*, p.name AS _location FROM messages m
                   LEFT JOIN plugins p ON p.id = m.plugin_id
                   WHERE m.status='approved' AND m.plugin_id=? ORDER BY m.created_at ASC""",
                (plugin_id,),
            )
        else:
            cursor = await db.execute(
                """SELECT m.*, p.name AS _location_p, a.title AS _location_a FROM messages m
                   LEFT JOIN plugins p ON p.id = m.plugin_id
                   LEFT JOIN articles a ON a.id = m.article_id
                   WHERE m.status='approved' AND m.created_at >= datetime('now', '-30 days', 'localtime')
                   ORDER BY m.created_at ASC""",
            )
        rows = [dict(r) for r in await cursor.fetchall()]

    # 补充 location 字段
    for r in rows:
        if r.get("_location"):
            r["location"] = r["_location"]
        elif r.get("_location_p"):
            r["location"] = r["_location_p"]
        elif r.get("_location_a"):
            r["location"] = r["_location_a"]
        else:
            r["location"] = "全站"
        # 清理临时字段
        r.pop("_location", None)
        r.pop("_location_p", None)
        r.pop("_location_a", None)

    # 按 parent_id 组装成树结构
    top = [r for r in rows if not r["parent_id"]]
    replies = {r["id"]: r for r in rows}
    for r in rows:
        if r["parent_id"] and r["parent_id"] in replies:
            parent = replies[r["parent_id"]]
            parent.setdefault("replies", []).append(r)
    return top


# ── 留言防垃圾配置 ──
# 验证码字符集（去掉易混淆的 0/O/1/I）
CAPTCHA_CHARS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
# 同一 IP 每小时最多留言条数（超出自动拉黑；直接展示模式下阈值放宽，主要靠 1 秒刷屏规则拦截）
MSG_RATE_LIMIT = 30
# 命中关键词即拒绝并自动拉黑该 IP
SPAM_KEYWORDS = [
    "赌博", "博彩", "彩票", "时时彩", "六合彩", "百家乐", "棋牌",
    "兼职", "刷单", "代开发票", "开发票", "办证", "贷款", "套现",
    "加微信", "微信号", "加qq", "加QQ", "qq群", "QQ群", "招代理",
    "色情", "裸聊", "约炮", "同城约", "上门服务", "外围",
    "usdt", "U币", "搬砖", "薅羊毛", "引流", "推广赚钱",
]


async def _auto_block_ip(db, ip: str, reason: str):
    """把 IP 加入黑名单（失败静默，不影响主流程）"""
    try:
        await db.execute(
            "INSERT OR IGNORE INTO blocked_ips (ip_address, reason) VALUES (?, ?)",
            (ip, reason),
        )
    except Exception:
        logger.warning("自动拉黑 IP 失败: %s", ip)


def _hit_spam_keyword(text: str):
    """命中垃圾关键词返回命中的词，否则返回 None"""
    low = text.lower()
    for w in SPAM_KEYWORDS:
        if w in low:
            return w
    return None


# 5x7 点阵字模（1=亮点），用于像素化渲染验证码字符（非标准字体，天然颗粒/毛糙）
_GLYPH_5x7 = {
    '2': ["01110","10001","00001","00010","00100","01000","11111"],
    '3': ["11110","00001","00001","01110","00001","00001","11110"],
    '4': ["00010","00110","01010","10010","11111","00010","00010"],
    '5': ["11111","10000","10000","11110","00001","00001","11110"],
    '6': ["01110","10000","10000","11110","10001","10001","01110"],
    '7': ["11111","00001","00010","00100","01000","01000","01000"],
    '8': ["01110","10001","10001","01110","10001","10001","01110"],
    '9': ["01110","10001","10001","01111","00001","00001","01110"],
    'A': ["01110","10001","10001","11111","10001","10001","10001"],
    'B': ["11110","10001","10001","11110","10001","10001","11110"],
    'C': ["01110","10001","10000","10000","10000","10001","01110"],
    'D': ["11110","10001","10001","10001","10001","10001","11110"],
    'E': ["11111","10000","10000","11110","10000","10000","11111"],
    'F': ["11111","10000","10000","11110","10000","10000","10000"],
    'G': ["01110","10001","10000","10111","10001","10001","01110"],
    'H': ["10001","10001","10001","11111","10001","10001","10001"],
    'J': ["00111","00010","00010","00010","00010","10010","01100"],
    'K': ["10001","10010","10100","11000","10100","10010","10001"],
    'M': ["10001","11011","10101","10101","10001","10001","10001"],
    'N': ["10001","10001","11001","10101","10011","10001","10001"],
    'P': ["11110","10001","10001","11110","10000","10000","10000"],
    'Q': ["01110","10001","10001","10001","10101","10010","01101"],
    'R': ["11110","10001","10001","11110","10100","10010","10001"],
    'S': ["01111","10000","10000","01110","00001","00001","11110"],
    'T': ["11111","00100","00100","00100","00100","00100","00100"],
    'U': ["10001","10001","10001","10001","10001","10001","01110"],
    'V': ["10001","10001","10001","10001","10001","01010","00100"],
    'W': ["10001","10001","10001","10101","10101","10101","01010"],
    'X': ["10001","10001","01010","00100","01010","10001","10001"],
    'Y': ["10001","10001","01010","00100","00100","00100","00100"],
    'Z': ["11111","00001","00010","00100","01000","10000","11111"],
}


def _captcha_svg(code: str) -> str:
    """生成 SVG 格式验证码图片（无第三方图片依赖）。

    参考图效果：深灰圆角背景 + 点阵像素字符（非标准字体，颗粒/毛糙）
    + 高斯模糊朦胧 + 亮粗干扰线贯穿字符，形成真正的视觉干扰。
    """
    w, h = 120, 48
    rnd = random.Random()
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        # 高斯模糊滤镜（只用于字符层，制造朦胧）
        '<defs><filter id="soft" x="-40%" y="-40%" width="180%" height="180%">'
        '<feGaussianBlur stdDeviation="0.55"/></filter></defs>',
        # 深灰圆角背景（四角留透明）
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="7" fill="#2f2f34"/>',
    ]
    # 底层干扰线（稍淡，画在字符下方）——只留 1 条，避免过多
    for _ in range(1):
        y1 = rnd.randint(8, h - 8)
        parts.append(
            f'<line x1="{rnd.randint(0, 20)}" y1="{y1}" x2="{rnd.randint(w - 20, w)}" y2="{max(2, min(h - 2, y1 + rnd.randint(-10, 10)))}" '
            f'stroke="rgba(250,250,252,0.55)" stroke-width="{rnd.uniform(1.2, 1.8):.1f}"/>'
        )

    # ── 点阵字符层（模糊、颗粒） ──
    pw, ph = 2.5, 3.0          # 每个点的大小
    cw = 5 * pw                 # 字符宽 12.5
    x0 = (w - (4 * cw + 3 * 6)) // 2 + rnd.randint(-2, 2)
    top_y = (h - 7 * ph) // 2  # 垂直居中
    step = cw + 6
    for idx, ch in enumerate(code):
        glyph = _GLYPH_5x7.get(ch)
        if not glyph:
            # 兜底：未定义字符用普通文本
            parts.append(
                f'<text x="{x0 + idx * step}" y="{top_y + 5 * ph}" font-family="Arial" font-size="24" fill="rgba(235,235,240,0.9)" filter="url(#soft)">{ch}</text>'
            )
            continue
        angle = rnd.uniform(-11, 11)
        # 围绕字符中心旋转
        cx = x0 + idx * step + cw / 2
        cy = top_y + (7 * ph) / 2
        rot = f'rotate({angle:.1f} {cx:.1f} {cy:.1f})' if angle else ''
        for row, line in enumerate(glyph):
            for col, v in enumerate(line):
                if v != '1':
                    continue
                px = x0 + idx * step + col * pw
                py = top_y + row * ph
                # 随机毛糙：点大小/亮度/位置轻微抖动
                jx = rnd.uniform(-0.35, 0.35)
                jy = rnd.uniform(-0.35, 0.35)
                s = rnd.uniform(2.2, 2.7)
                alpha = rnd.uniform(0.6, 0.95)
                parts.append(
                    f'<rect x="{px + jx:.1f}" y="{py + jy:.1f}" width="{s:.1f}" height="{s + 0.5:.1f}" rx="0.4" '
                    f'fill="rgba(232,232,240,{alpha:.2f})" filter="url(#soft)" transform="{rot}"/>'
                )
        # 每个字符再补 2~3 个杂点，破坏完整性
        for _ in range(rnd.randint(2, 3)):
            parts.append(
                f'<circle cx="{cx + rnd.uniform(-6, 6):.1f}" cy="{cy + rnd.uniform(-9, 9):.1f}" r="{rnd.uniform(0.6, 1.2):.1f}" '
                f'fill="rgba(240,240,245,{rnd.uniform(0.4, 0.7):.2f})" filter="url(#soft)"/>'
            )

    # ── 顶层干扰线（亮、粗，压在字符上方，形成强干扰）——2 条，避免过多 ──
    for _ in range(2):
        y1 = rnd.randint(6, h - 6)
        parts.append(
            f'<line x1="{rnd.randint(0, 24)}" y1="{y1}" x2="{rnd.randint(w - 24, w)}" y2="{max(2, min(h - 2, y1 + rnd.randint(-12, 12)))}" '
            f'stroke="rgba(252,252,255,0.85)" stroke-width="{rnd.uniform(1.4, 2.2):.1f}"/>'
        )
    # 噪点
    for _ in range(26):
        parts.append(
            f'<circle cx="{rnd.randint(2, w - 2)}" cy="{rnd.randint(2, h - 2)}" r="{rnd.uniform(0.5, 1.1):.1f}" fill="rgba(252,252,255,0.5)"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


@router.get("/api/captcha")
async def api_captcha(request: Request):
    """生成验证码：答案存入 Session，一次性使用"""
    code = "".join(random.choices(CAPTCHA_CHARS, k=4))
    request.session["captcha_code"] = code
    return Response(
        content=_captcha_svg(code),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store, no-cache, max-age=0", "Pragma": "no-cache"},
    )


@router.post("/api/messages")
async def api_create_message(request: Request):
    body = await request.json()
    plugin_id = body.get("plugin_id")
    article_id = body.get("article_id")
    parent_id = body.get("parent_id")
    author = (body.get("author", "").strip() or "匿名")[:20]
    content = body.get("content", "").strip()

    if not content:
        raise HTTPException(400, "留言内容不能为空")
    if len(content) > 1000:
        raise HTTPException(400, "留言内容过长（最多1000字）")

    # 获取客户端 IP（支持反向代理）
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else request.client.host

    # ── 1. 验证码校验：错误/缺失直接拒绝，验证码一次性使用 ──
    captcha = (body.get("captcha") or "").strip().upper()
    saved = request.session.pop("captcha_code", "")
    if not saved or not captcha or captcha != saved:
        logger.info("留言验证码错误/缺失: IP=%s", client_ip)
        raise HTTPException(400, "验证码错误或已过期，请刷新后重试")

    async with get_db() as db:
        # ── 2. 关键词过滤：命中垃圾词直接拒绝并拉黑 ──
        hit = _hit_spam_keyword(content) or _hit_spam_keyword(author)
        if hit:
            logger.info("留言命中垃圾词(%s): IP=%s", hit, client_ip)
            await _auto_block_ip(db, client_ip, f"垃圾内容:{hit}")
            await db.commit()
            raise HTTPException(400, "留言内容包含违规内容，已被系统拦截")

        # ── 3. 频率限制兜底：同一 IP 一小时内超过上限自动拉黑 ──
        cursor = await db.execute(
            """SELECT COUNT(*) FROM messages
               WHERE ip_address=? AND created_at >= datetime('now','localtime','-1 hour')""",
            (client_ip,),
        )
        recent = (await cursor.fetchone())[0]
        if recent >= MSG_RATE_LIMIT:
            logger.info("留言频率超限(%d条/小时): IP=%s", recent, client_ip)
            await _auto_block_ip(db, client_ip, "留言频率过高")
            await db.commit()
            raise HTTPException(429, "留言过于频繁，请稍后再试")

        # ── 4. 检查是否为管理员 IP（昵称显示为"管理员"） ──
        cursor = await db.execute("SELECT id FROM admin_ips WHERE ip_address=?", (client_ip,))
        is_admin_ip = await cursor.fetchone() is not None
        if is_admin_ip:
            author = "管理员"
        elif "管理员" in author.strip():
            # 非管理员 IP 禁止昵称含"管理员"
            author = "匿名"

        if parent_id:
            cursor = await db.execute("SELECT id FROM messages WHERE id=?", (parent_id,))
            if not await cursor.fetchone():
                raise HTTPException(404, "被回复的留言不存在")

        # ── 5. 刷屏检测：同一 IP 距上一条留言时间差 < 1 秒 → 本条直接放入已拒绝 ──
        now_ms = int(time.time() * 1000)
        status = "approved"
        cursor = await db.execute(
            "SELECT created_ms FROM messages WHERE ip_address=? ORDER BY id DESC LIMIT 1",
            (client_ip,),
        )
        last_row = await cursor.fetchone()
        if last_row and (now_ms - (last_row["created_ms"] or 0)) < 1000:
            status = "rejected"
            logger.info("同一IP 1秒内连发(疑似刷屏)，已置为已拒绝: IP=%s", client_ip)

        # ── 6. 入库：验证码通过后直接展示(approved)；1 秒内连发置为已拒绝(rejected) ──
        await db.execute(
            "INSERT INTO messages (plugin_id, article_id, parent_id, author, content, status, ip_address, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (plugin_id if plugin_id else None,
             article_id if article_id else None,
             parent_id if parent_id else None,
             author, content, status, client_ip, now_ms),
        )
        await db.commit()

    logger.info("新留言(%s): %s (IP: %s)", status, content[:50], client_ip)
    return {"success": True, "message": "留言成功"}


# ── 打赏 ──

@router.get("/donation")
async def get_donation():
    return await get_donation_info()


@router.get("/donation/qrcode")
async def donation_qrcode():
    from config import DONATION_DIR
    info = await get_donation_info()
    if not info or not info.get("qrcode_path") or not info.get("enabled"):
        raise HTTPException(404, "打赏未启用")
    file_path = DONATION_DIR / info["qrcode_path"]
    if not file_path.exists():
        raise HTTPException(404, "收款码不存在")
    return FileResponse(str(file_path))


# ── 申请解封 ──

@router.get("/unblock")
async def unblock_page(request: Request):
    return templates.TemplateResponse(request, "unblock.html", {"donation": await get_donation_info()})


@router.post("/unblock")
async def submit_unblock(request: Request):
    body = await request.json()
    reason = body.get("reason", "").strip()
    if not reason:
        raise HTTPException(400, "请填写解封理由")
    if len(reason) > 500:
        raise HTTPException(400, "理由过长（最多500字）")

    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else request.client.host

    async with get_db() as db:
        # 检查该 IP 是否有待处理的申请
        cursor = await db.execute(
            "SELECT id FROM unblock_requests WHERE ip_address=? AND status='pending'",
            (client_ip,),
        )
        if await cursor.fetchone():
            raise HTTPException(400, "您已提交过申请，请等待审核")

        await db.execute(
            "INSERT INTO unblock_requests (ip_address, reason) VALUES (?, ?)",
            (client_ip, reason),
        )
        await db.commit()

    logger.info("解封申请: %s - %s", client_ip, reason[:50])
    return {"success": True, "message": "申请已提交，请等待管理员审核。"}
