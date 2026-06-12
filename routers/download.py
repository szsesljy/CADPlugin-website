from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse

from config import STORAGE_ROOT, ITEMS_PER_PAGE
from database import get_db
from dependencies import templates, get_donation_info, logger, record_page_view

router = APIRouter()


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

        # 查找关联的使用说明
        guide_cursor = await db.execute(
            "SELECT id, title FROM articles WHERE type='guide' AND plugin_id=?",
            (plugin_id,),
        )
        guide_row = await guide_cursor.fetchone()
        p["guide_article"] = dict(guide_row) if guide_row else None

        categories = await _get_categories(db)

    donation = await get_donation_info()
    return templates.TemplateResponse(request, "detail.html", {
        "request": request,
        "plugin": p,
        "categories": categories,
        "donation": donation,
    })


# ── 下载 ──

@router.get("/download/{plugin_id}")
async def download_plugin(plugin_id: int):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE id=? AND status='approved' AND disabled=0",
            (plugin_id,),
        )
        plugin = await cursor.fetchone()
        if not plugin:
            raise HTTPException(404, "插件不存在或已下架")

        await db.execute("UPDATE plugins SET downloads=downloads+1 WHERE id=?", (plugin_id,))
        await db.commit()
        plugin = dict(plugin)

    file_path = STORAGE_ROOT / plugin["file_path"]
    if not file_path.exists():
        raise HTTPException(404, "文件已丢失")

    logger.info("下载插件: %s (ID: %d)", plugin["name"], plugin_id)
    return FileResponse(str(file_path), filename=plugin["file_name"])


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
                   WHERE m.status='approved' ORDER BY m.created_at ASC LIMIT 50""",
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

    async with get_db() as db:
        # 检查是否为管理员 IP
        cursor = await db.execute("SELECT id FROM admin_ips WHERE ip_address=?", (client_ip,))
        if await cursor.fetchone():
            author = "管理员"
        elif "管理员" in author.strip():
            # 非管理员 IP 禁止昵称含"管理员"
            author = "匿名"

        if parent_id:
            cursor = await db.execute("SELECT id FROM messages WHERE id=?", (parent_id,))
            if not await cursor.fetchone():
                raise HTTPException(404, "被回复的留言不存在")
        await db.execute(
            "INSERT INTO messages (plugin_id, article_id, parent_id, author, content, status, ip_address) VALUES (?, ?, ?, ?, ?, 'approved', ?)",
            (plugin_id if plugin_id else None,
             article_id if article_id else None,
             parent_id if parent_id else None,
             author, content, client_ip),
        )
        await db.commit()

    logger.info("新留言: %s (IP: %s)", content[:50], client_ip)
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
