import time
from pathlib import Path
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse

from config import ADMIN_PASSWORD, PENDING_DIR, APPROVED_DIR, DONATION_DIR
from config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, STORAGE_ROOT
from database import get_db, verify_admin_password, set_admin_password
from dependencies import templates, get_donation_info, logger, get_page_view_stats
from models import PluginEdit
from security import scan_file

router = APIRouter()


def _is_admin(request: Request) -> bool:
    return bool(request.session.get("admin"))


def _check_admin(request: Request):
    if not _is_admin(request):
        raise HTTPException(303, headers={"Location": "/admin/login"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  页面路由
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin")
async def admin_page(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)

    async with get_db() as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM plugins")).fetchone())[0]
        approved = (await (await db.execute("SELECT COUNT(*) FROM plugins WHERE status='approved'")).fetchone())[0]
        pending = (await (await db.execute("SELECT COUNT(*) FROM plugins WHERE status='pending'")).fetchone())[0]
        downloads = (await (await db.execute("SELECT COALESCE(SUM(downloads),0) FROM plugins")).fetchone())[0]

    views = await get_page_view_stats()
    donation = await get_donation_info()
    return templates.TemplateResponse(request, "admin.html", {
        "request": request,
        "stats": {"total": total, "approved": approved, "pending": pending, "downloads": downloads},
        "views": views,
        "donation": donation,
    })


@router.get("/admin/login")
async def admin_login_page(request: Request):
    if _is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "admin_login.html", {"request": request})


@router.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    if await verify_admin_password(password):
        request.session["admin"] = True
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "admin_login.html", {
        "request": request, "error": "密码错误",
    })


@router.get("/admin/logout")
async def admin_logout(request: Request):
    request.session["admin"] = False
    return RedirectResponse("/admin/login", status_code=303)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  待审核 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/pending")
async def api_pending(request: Request):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE status='pending' ORDER BY created_at DESC"
        )
        return [dict(r) for r in await cursor.fetchall()]


@router.get("/admin/api/pending/{plugin_id}/download")
async def api_download_pending(request: Request, plugin_id: int):
    """下载待审核插件文件"""
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE id=? AND status='pending'", (plugin_id,)
        )
        plugin = await cursor.fetchone()
        if not plugin:
            return JSONResponse({"error": "插件不存在"}, status_code=404)
        plugin = dict(plugin)

    file_path = PENDING_DIR / Path(plugin["file_path"]).name
    if not file_path.exists():
        return JSONResponse({"error": "文件不存在"}, status_code=404)

    return FileResponse(str(file_path), filename=plugin["file_name"])


@router.post("/admin/api/scan/{plugin_id}")
async def api_scan(request: Request, plugin_id: int):
    """扫描指定插件文件，返回扫描结果"""
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE id=? AND status='pending'", (plugin_id,)
        )
        plugin = await cursor.fetchone()
        if not plugin:
            return JSONResponse({"error": "插件不存在"}, status_code=404)
        plugin = dict(plugin)

    file_path = PENDING_DIR / Path(plugin["file_path"]).name
    if not file_path.exists():
        return JSONResponse({"error": "文件不存在"}, status_code=404)

    result = await scan_file(file_path)
    if not result.get("clean"):
        logger.warning("扫描结果: %s | %s", plugin["name"], result.get("virus", "未知"))
    else:
        logger.info("扫描结果: %s | 安全", plugin["name"])

    return result


@router.post("/admin/api/approve/{plugin_id}")
async def api_approve(request: Request, plugin_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE id=? AND status='pending'", (plugin_id,)
        )
        plugin = await cursor.fetchone()
        if not plugin:
            return JSONResponse({"error": "插件不存在或已处理"}, status_code=404)
        plugin = dict(plugin)

        old_path = PENDING_DIR / Path(plugin["file_path"]).name

        ts = int(time.time())
        safe_name = f"{ts}_{plugin['file_name']}"
        APPROVED_DIR.mkdir(parents=True, exist_ok=True)
        if old_path.exists():
            old_path.rename(APPROVED_DIR / safe_name)

        await db.execute(
            "UPDATE plugins SET status='approved', file_path=? WHERE id=?",
            (f"approved/{safe_name}", plugin_id),
        )
        await db.commit()

    logger.info("审核通过: %s (ID: %d)", plugin["name"], plugin_id)
    return {"success": True}


@router.post("/admin/api/reject/{plugin_id}")
async def api_reject(request: Request, plugin_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM plugins WHERE id=? AND status='pending'", (plugin_id,)
        )
        plugin = await cursor.fetchone()
        if not plugin:
            return JSONResponse({"error": "插件不存在或已处理"}, status_code=404)
        plugin = dict(plugin)

        file_path = PENDING_DIR / Path(plugin["file_path"]).name
        if file_path.exists():
            file_path.unlink()
        await db.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
        await db.commit()

    logger.info("审核拒绝: %s (ID: %d)", plugin["name"], plugin_id)
    return {"success": True}


@router.post("/admin/api/batch-approve")
async def api_batch_approve(request: Request):
    _check_admin(request)
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        return JSONResponse({"error": "请选择插件"}, status_code=400)

    async with get_db() as db:
        for pid in ids:
            cursor = await db.execute(
                "SELECT * FROM plugins WHERE id=? AND status='pending'", (pid,)
            )
            plugin = await cursor.fetchone()
            if not plugin:
                continue
            plugin = dict(plugin)
            old_path = PENDING_DIR / Path(plugin["file_path"]).name

            ts = int(time.time())
            safe_name = f"{ts}_{plugin['file_name']}"
            APPROVED_DIR.mkdir(parents=True, exist_ok=True)
            if old_path.exists():
                old_path.rename(APPROVED_DIR / safe_name)
            await db.execute(
                "UPDATE plugins SET status='approved', file_path=? WHERE id=?",
                (f"approved/{safe_name}", pid),
            )
        await db.commit()

    logger.info("批量审核通过: %d 个插件", len(ids))
    return {"success": True}


@router.post("/admin/api/batch-reject")
async def api_batch_reject(request: Request):
    _check_admin(request)
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        return JSONResponse({"error": "请选择插件"}, status_code=400)

    async with get_db() as db:
        for pid in ids:
            cursor = await db.execute(
                "SELECT * FROM plugins WHERE id=? AND status='pending'", (pid,)
            )
            plugin = await cursor.fetchone()
            if not plugin:
                continue
            plugin = dict(plugin)
            file_path = PENDING_DIR / Path(plugin["file_path"]).name
            if file_path.exists():
                file_path.unlink()
            await db.execute("DELETE FROM plugins WHERE id=?", (pid,))
        await db.commit()

    logger.info("批量拒绝: %d 个插件", len(ids))
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  已审核插件管理（编辑/下架/删除/上架）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/approved")
async def api_approved(request: Request, search: str = "", page: int = 1, grouped: str = ""):
    _check_admin(request)
    if grouped == "true":
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM categories ORDER BY sort")
            categories = [dict(r) for r in await cursor.fetchall()]

            cursor = await db.execute(
                "SELECT p.* FROM plugins p WHERE p.status='approved' ORDER BY p.name"
            )
            plugins = [dict(r) for r in await cursor.fetchall()]

            for p in plugins:
                cursor = await db.execute(
                    """SELECT t.id, t.name, t.category_id FROM tags t
                       JOIN plugin_tags pt ON pt.tag_id = t.id
                       WHERE pt.plugin_id = ?""",
                    (p["id"],),
                )
                p["tags"] = [dict(r) for r in await cursor.fetchall()]

            return {"categories": categories, "plugins": plugins}

    from config import ITEMS_PER_PAGE
    async with get_db() as db:
        if search:
            count_cursor = await db.execute(
                "SELECT COUNT(*) FROM plugins WHERE status='approved' AND (name LIKE ? OR description LIKE ?)",
                (f"%{search}%", f"%{search}%"),
            )
            total = (await count_cursor.fetchone())[0]
            cursor = await db.execute(
                "SELECT * FROM plugins WHERE status='approved' AND (name LIKE ? OR description LIKE ?) "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (f"%{search}%", f"%{search}%", ITEMS_PER_PAGE, (page - 1) * ITEMS_PER_PAGE),
            )
        else:
            count_cursor = await db.execute(
                "SELECT COUNT(*) FROM plugins WHERE status='approved'"
            )
            total = (await count_cursor.fetchone())[0]
            cursor = await db.execute(
                "SELECT * FROM plugins WHERE status='approved' ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (ITEMS_PER_PAGE, (page - 1) * ITEMS_PER_PAGE),
            )
        plugins = [dict(r) for r in await cursor.fetchall()]

    return {"plugins": plugins, "total": total}


@router.get("/admin/api/approved/{plugin_id}")
async def api_get_approved(request: Request, plugin_id: int):
    """获取单个插件完整信息"""
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,))
        plugin = await cursor.fetchone()
        if not plugin:
            return JSONResponse({"error": "插件不存在"}, status_code=404)
        p = dict(plugin)
        # 获取标签
        cursor = await db.execute(
            """SELECT t.id FROM tags t
               JOIN plugin_tags pt ON pt.tag_id = t.id
               WHERE pt.plugin_id = ?""",
            (plugin_id,),
        )
        p["tag_ids"] = [r["id"] for r in await cursor.fetchall()]
        return p


@router.post("/admin/api/approved/{plugin_id}/edit")
async def api_edit_approved(request: Request, plugin_id: int):
    _check_admin(request)
    body = await request.json()

    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,))
        if not await cursor.fetchone():
            return JSONResponse({"error": "插件不存在"}, status_code=404)

        sets = []
        params = []
        for field in ("name", "description", "version", "author", "netdisk_url", "download_mode"):
            if field in body:
                sets.append(f"{field}=?")
                params.append(body[field])
        if sets:
            params.append(plugin_id)
            await db.execute(
                f"UPDATE plugins SET {', '.join(sets)} WHERE id=?", params
            )

        # 更新标签
        if "tag_ids" in body:
            await db.execute("DELETE FROM plugin_tags WHERE plugin_id=?", (plugin_id,))
            for tid in body["tag_ids"]:
                await db.execute(
                    "INSERT OR IGNORE INTO plugin_tags (plugin_id, tag_id) VALUES (?, ?)",
                    (plugin_id, tid),
                )

        await db.commit()

    logger.info("插件编辑: ID=%d", plugin_id)
    return {"success": True}


@router.post("/admin/api/approved/{plugin_id}/toggle-disable")
async def api_toggle_disable(request: Request, plugin_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT disabled FROM plugins WHERE id=?", (plugin_id,))
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "插件不存在"}, status_code=404)
        new_val = 0 if row["disabled"] else 1
        await db.execute("UPDATE plugins SET disabled=? WHERE id=?", (new_val, plugin_id))
        await db.commit()
    status = "已下架" if new_val else "已上架"
    logger.info("插件 %s: ID=%d", status, plugin_id)
    return {"success": True, "disabled": bool(new_val)}


@router.delete("/admin/api/approved/{plugin_id}")
async def api_delete_approved(request: Request, plugin_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,))
        plugin = await cursor.fetchone()
        if not plugin:
            return JSONResponse({"error": "插件不存在"}, status_code=404)
        plugin = dict(plugin)

        file_path = STORAGE_ROOT / plugin["file_path"]
        if file_path.exists():
            file_path.unlink()
        await db.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
        await db.commit()

    logger.info("插件删除: %s (ID: %d)", plugin["name"], plugin_id)
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  管理员直接上传（跳过审核）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/admin/api/direct-upload")
async def api_direct_upload(request: Request):
    _check_admin(request)

    form = await request.form()
    name = form.get("name", "").strip()
    description = form.get("description", "")
    version = form.get("version", "1.0")
    author = form.get("author", "匿名")
    netdisk_url = form.get("netdisk_url", "")
    download_mode = form.get("download_mode", "direct_only")
    tag_ids = form.getlist("tag_ids")
    file = form.get("file")

    if not name:
        return JSONResponse({"error": "插件名称不能为空"}, status_code=400)
    if not file or not file.filename:
        return JSONResponse({"error": "请选择文件"}, status_code=400)

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": f"不支持的文件类型: {ext}"}, status_code=400)

    ts = int(time.time())
    safe_name = f"{ts}_{file.filename}"
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    save_path = APPROVED_DIR / safe_name

    size = 0
    with open(save_path, "wb") as f:
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                save_path.unlink(missing_ok=True)
                return JSONResponse({"error": "文件过大"}, status_code=413)
            f.write(chunk)

    # 扫描参考（仅日志）
    scan_result = await scan_file(save_path)
    if not scan_result.get("clean"):
        logger.warning("管理直传提醒: 文件可能含病毒 %s | %s", name, scan_result.get("virus", ""))

    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO plugins (name, description, version, author, file_name, file_path, file_size, status, netdisk_url, download_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
            (name, description, version, author, file.filename, f"approved/{safe_name}", size, netdisk_url, download_mode),
        )
        plugin_id = cursor.lastrowid

        # 标签
        for tid in tag_ids:
            if tid.isdigit():
                await db.execute(
                    "INSERT OR IGNORE INTO plugin_tags (plugin_id, tag_id) VALUES (?, ?)",
                    (plugin_id, int(tid)),
                )
        await db.commit()

    logger.info("管理直传: %s (%d bytes)", name, size)
    return {"success": True, "plugin_id": plugin_id}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  分类管理（CRUD）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/categories")
async def api_get_categories(request: Request):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM categories ORDER BY sort")
        cats = [dict(r) for r in await cursor.fetchall()]
        for cat in cats:
            cursor = await db.execute(
                "SELECT * FROM tags WHERE category_id=? ORDER BY sort",
                (cat["id"],),
            )
            cat["tags"] = [dict(r) for r in await cursor.fetchall()]
        return cats


@router.post("/admin/api/categories")
async def api_create_category(request: Request):
    _check_admin(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "名称不能为空"}, status_code=400)
    async with get_db() as db:
        cursor = await db.execute("SELECT COALESCE(MAX(sort),-1)+1 FROM categories")
        sort = (await cursor.fetchone())[0]
        await db.execute("INSERT INTO categories (name, sort) VALUES (?, ?)", (name, sort))
        await db.commit()
        new_id = (await db.execute("SELECT id FROM categories WHERE name=?", (name,))).fetchone()[0]
    return {"success": True, "id": new_id}


@router.put("/admin/api/categories/{cat_id}")
async def api_update_category(request: Request, cat_id: int):
    _check_admin(request)
    body = await request.json()
    name = body.get("name")
    sort = body.get("sort")
    sets = []
    params = []
    if name is not None:
        sets.append("name=?")
        params.append(name)
    if sort is not None:
        sets.append("sort=?")
        params.append(sort)
    if sets:
        params.append(cat_id)
        async with get_db() as db:
            await db.execute(
                f"UPDATE categories SET {', '.join(sets)} WHERE id=?", params
            )
            await db.commit()
    return {"success": True}


@router.delete("/admin/api/categories/{cat_id}")
async def api_delete_category(request: Request, cat_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
        await db.commit()
    return {"success": True}


@router.post("/admin/api/categories/{cat_id}/tags")
async def api_create_tag(request: Request, cat_id: int):
    _check_admin(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "名称不能为空"}, status_code=400)
    async with get_db() as db:
        cursor = await db.execute("SELECT COALESCE(MAX(sort),-1)+1 FROM tags WHERE category_id=?", (cat_id,))
        sort = (await cursor.fetchone())[0]
        await db.execute(
            "INSERT INTO tags (category_id, name, sort) VALUES (?, ?, ?)",
            (cat_id, name, sort),
        )
        await db.commit()
    return {"success": True}


@router.delete("/admin/api/tags/{tag_id}")
async def api_delete_tag(request: Request, tag_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        await db.commit()
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/articles")
async def api_list_articles(request: Request):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT a.*, p.name AS plugin_name
               FROM articles a
               LEFT JOIN plugins p ON p.id = a.plugin_id
               ORDER BY a.created_at DESC"""
        )
        return [dict(r) for r in await cursor.fetchall()]


@router.get("/admin/api/articles/{article_id}")
async def api_get_article(request: Request, article_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM articles WHERE id=?", (article_id,))
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "文章不存在"}, status_code=404)
        return dict(row)


@router.post("/admin/api/articles")
async def api_create_article(request: Request):
    _check_admin(request)
    body = await request.json()
    type_ = body.get("type", "notice")
    title = body.get("title", "").strip()
    content = body.get("content", "").strip()
    plugin_id = body.get("plugin_id")
    pinned = 1 if body.get("pinned") else 0

    if not title:
        return JSONResponse({"error": "标题不能为空"}, status_code=400)
    if type_ not in ("notice", "guide"):
        return JSONResponse({"error": "类型无效"}, status_code=400)

    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO articles (type, title, content, plugin_id, pinned) VALUES (?, ?, ?, ?, ?)",
            (type_, title, content, plugin_id if plugin_id else None, pinned),
        )
        await db.commit()

    logger.info("文章创建: %s", title)
    return {"success": True, "id": cursor.lastrowid}


@router.put("/admin/api/articles/{article_id}")
async def api_update_article(request: Request, article_id: int):
    _check_admin(request)
    body = await request.json()
    sets = []
    params = []
    for field in ("type", "title", "content", "plugin_id", "pinned"):
        if field in body:
            val = body[field]
            if field == "plugin_id":
                val = int(val) if val and str(val).isdigit() else None
            sets.append(f"{field}=?")
            params.append(val)
    if not sets:
        return JSONResponse({"error": "无修改内容"}, status_code=400)
    params.append(article_id)
    async with get_db() as db:
        await db.execute(
            f"UPDATE articles SET {', '.join(sets)}, updated_at=datetime('now','localtime') WHERE id=?",
            params,
        )
        await db.commit()
    return {"success": True}


@router.delete("/admin/api/articles/{article_id}")
async def api_delete_article(request: Request, article_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("DELETE FROM articles WHERE id=?", (article_id,))
        await db.commit()
    logger.info("文章删除: ID=%d", article_id)
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  留言管理 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/messages")
async def api_list_messages(request: Request, status: str = "pending"):
    _check_admin(request)
    async with get_db() as db:
        base_sql = """SELECT m.*, p.name AS plugin_name,
                             CASE WHEN b.id IS NOT NULL THEN 1 ELSE 0 END AS is_blocked
                      FROM messages m
                      LEFT JOIN plugins p ON p.id=m.plugin_id
                      LEFT JOIN blocked_ips b ON b.ip_address = m.ip_address"""
        if status == "all":
            cursor = await db.execute(
                f"{base_sql} ORDER BY m.created_at DESC"
            )
        else:
            cursor = await db.execute(
                f"{base_sql} WHERE m.status=? ORDER BY m.created_at DESC",
                (status,),
            )
        return [dict(r) for r in await cursor.fetchall()]


@router.post("/admin/api/messages/{msg_id}/approve")
async def api_approve_message(request: Request, msg_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("UPDATE messages SET status='approved' WHERE id=?", (msg_id,))
        await db.commit()
    logger.info("留言审核通过: ID=%d", msg_id)
    return {"success": True}


@router.post("/admin/api/messages/{msg_id}/reject")
async def api_reject_message(request: Request, msg_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("UPDATE messages SET status='rejected' WHERE id=?", (msg_id,))
        await db.commit()
    logger.info("留言拒绝: ID=%d", msg_id)
    return {"success": True}


@router.delete("/admin/api/messages/{msg_id}")
async def api_delete_message(request: Request, msg_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
        await db.commit()
    logger.info("留言删除: ID=%d", msg_id)
    return {"success": True}


@router.post("/admin/api/messages/batch-approve")
async def api_batch_approve_messages(request: Request):
    _check_admin(request)
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        return JSONResponse({"error": "请选择留言"}, status_code=400)
    async with get_db() as db:
        for mid in ids:
            await db.execute("UPDATE messages SET status='approved' WHERE id=?", (mid,))
        await db.commit()
    logger.info("批量审核留言: %d 条", len(ids))
    return {"success": True}


@router.post("/admin/api/messages/batch-reject")
async def api_batch_reject_messages(request: Request):
    _check_admin(request)
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        return JSONResponse({"error": "请选择留言"}, status_code=400)
    async with get_db() as db:
        for mid in ids:
            await db.execute("UPDATE messages SET status='rejected' WHERE id=?", (mid,))
        await db.commit()
    logger.info("批量拒绝留言: %d 条", len(ids))
    return {"success": True}


@router.post("/admin/api/messages/batch-delete")
async def api_batch_delete_messages(request: Request):
    _check_admin(request)
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        return JSONResponse({"error": "请选择留言"}, status_code=400)
    async with get_db() as db:
        for mid in ids:
            await db.execute("DELETE FROM messages WHERE id=?", (mid,))
        await db.commit()
    logger.info("批量删除留言: %d 条", len(ids))
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  板块管理 API（管理前台导航板块及其条目）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/boards")
async def api_list_boards(request: Request):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM boards ORDER BY sort")
        boards = [dict(r) for r in await cursor.fetchall()]
        for b in boards:
            cursor = await db.execute(
                "SELECT * FROM board_items WHERE board_id=? ORDER BY sort ASC, created_at DESC",
                (b["id"],),
            )
            b["items"] = [dict(r) for r in await cursor.fetchall()]
        return boards


@router.post("/admin/api/boards")
async def api_create_board(request: Request):
    _check_admin(request)
    body = await request.json()
    name = body.get("name", "").strip()
    slug = body.get("slug", "").strip()
    if not name or not slug:
        return JSONResponse({"error": "名称和标识不能为空"}, status_code=400)
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM boards WHERE slug=?", (slug,))
        if await cursor.fetchone():
            return JSONResponse({"error": "标识已存在，请更换"}, status_code=400)
        cursor = await db.execute("SELECT COALESCE(MAX(sort),-1)+1 FROM boards")
        sort = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "INSERT INTO boards (name, slug, sort) VALUES (?, ?, ?)",
            (name, slug, sort),
        )
        await db.commit()
    logger.info("板块创建: %s (slug=%s)", name, slug)
    return {"success": True, "id": cursor.lastrowid}


@router.put("/admin/api/boards/{board_id}")
async def api_update_board(request: Request, board_id: int):
    _check_admin(request)
    body = await request.json()
    sets = []
    params = []
    for field in ("name", "slug", "sort"):
        if field in body:
            val = body[field]
            if field == "slug":
                async with get_db() as db:
                    cursor = await db.execute(
                        "SELECT id FROM boards WHERE slug=? AND id!=?", (val, board_id)
                    )
                    if await cursor.fetchone():
                        return JSONResponse({"error": "标识已被使用"}, status_code=400)
            sets.append(f"{field}=?")
            params.append(val)
    if sets:
        params.append(board_id)
        async with get_db() as db:
            await db.execute(
                f"UPDATE boards SET {', '.join(sets)} WHERE id=?", params
            )
            await db.commit()
    return {"success": True}


@router.delete("/admin/api/boards/{board_id}")
async def api_delete_board(request: Request, board_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("DELETE FROM boards WHERE id=?", (board_id,))
        await db.commit()
    logger.info("板块删除: ID=%d", board_id)
    return {"success": True}


@router.post("/admin/api/boards/reorder")
async def api_reorder_boards(request: Request):
    _check_admin(request)
    body = await request.json()
    ids = body.get("ids", [])
    async with get_db() as db:
        for i, bid in enumerate(ids):
            await db.execute("UPDATE boards SET sort=? WHERE id=?", (i, bid))
        await db.commit()
    return {"success": True}


@router.get("/admin/api/boards/{board_id}")
async def api_get_board(request: Request, board_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM boards WHERE id=?", (board_id,))
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "板块不存在"}, status_code=404)
        return dict(row)


@router.post("/admin/api/boards/{board_id}/items")
async def api_create_board_item(request: Request, board_id: int):
    _check_admin(request)
    body = await request.json()
    title = body.get("title", "").strip()
    file_name = (body.get("file_name") or "").strip()
    file_url = (body.get("file_url") or "").strip()
    if not title:
        return JSONResponse({"error": "标题不能为空"}, status_code=400)
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM boards WHERE id=?", (board_id,))
        if not await cursor.fetchone():
            return JSONResponse({"error": "板块不存在"}, status_code=404)
        cursor = await db.execute(
            "SELECT COALESCE(MAX(sort),-1)+1 FROM board_items WHERE board_id=?",
            (board_id,),
        )
        sort = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "INSERT INTO board_items (board_id, title, file_name, file_url, sort) VALUES (?, ?, ?, ?, ?)",
            (board_id, title, file_name, file_url, sort),
        )
        await db.commit()
    logger.info("板块条目创建: %s", title)
    return {"success": True, "id": cursor.lastrowid}


@router.put("/admin/api/board-items/{item_id}")
async def api_update_board_item(request: Request, item_id: int):
    _check_admin(request)
    body = await request.json()
    sets = []
    params = []
    for field in ("title", "file_name", "file_url", "sort"):
        if field in body:
            sets.append(f"{field}=?")
            params.append(body[field])
    if sets:
        params.append(item_id)
        async with get_db() as db:
            await db.execute(
                f"UPDATE board_items SET {', '.join(sets)} WHERE id=?", params
            )
            await db.commit()
    return {"success": True}


@router.delete("/admin/api/board-items/{item_id}")
async def api_delete_board_item(request: Request, item_id: int):
    _check_admin(request)
    async with get_db() as db:
        await db.execute("DELETE FROM board_items WHERE id=?", (item_id,))
        await db.commit()
    logger.info("板块条目删除: ID=%d", item_id)
    return {"success": True}


@router.post("/admin/api/board-items/reorder")
async def api_reorder_board_items(request: Request):
    _check_admin(request)
    body = await request.json()
    board_id = body.get("board_id")
    ids = body.get("ids", [])
    async with get_db() as db:
        for i, item_id in enumerate(ids):
            await db.execute(
                "UPDATE board_items SET sort=? WHERE id=? AND board_id=?",
                (i, item_id, board_id),
            )
        await db.commit()
    return {"success": True}
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  打赏 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/admin/api/donation")
async def api_upload_qrcode(request: Request, file: UploadFile = File(...)):
    _check_admin(request)
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in {".jpg", ".jpeg", ".png"}:
        return JSONResponse({"error": "仅支持 JPG / PNG 图片"}, status_code=400)
    DONATION_DIR.mkdir(parents=True, exist_ok=True)

    old = await get_donation_info()
    if old and old.get("qrcode_path"):
        old_file = DONATION_DIR / old["qrcode_path"]
        if old_file.exists():
            old_file.unlink()

    safe_name = f"qrcode_{int(time.time())}{ext}"
    content = await file.read()
    (DONATION_DIR / safe_name).write_bytes(content)

    async with get_db() as db:
        await db.execute(
            "UPDATE donation_config SET qrcode_path=?, enabled=1 WHERE id=1",
            (safe_name,),
        )
        await db.commit()
    logger.info("收款码已更新")
    return {"success": True}


@router.delete("/admin/api/donation")
async def api_delete_qrcode(request: Request):
    _check_admin(request)
    old = await get_donation_info()
    if old and old.get("qrcode_path"):
        old_file = DONATION_DIR / old["qrcode_path"]
        if old_file.exists():
            old_file.unlink()
    async with get_db() as db:
        await db.execute("UPDATE donation_config SET qrcode_path=NULL, enabled=0 WHERE id=1")
        await db.commit()
    logger.info("收款码已删除")
    return {"success": True}


@router.post("/admin/api/donation/toggle")
async def api_toggle_donation(request: Request):
    _check_admin(request)
    body = await request.json()
    enabled = body.get("enabled", False)
    async with get_db() as db:
        await db.execute("UPDATE donation_config SET enabled=? WHERE id=1", (1 if enabled else 0,))
        await db.commit()
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  密码修改
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/admin/api/change-password")
async def api_change_password(request: Request):
    _check_admin(request)
    body = await request.json()
    old_pw = body.get("old_password", "")
    new_pw = body.get("new_password", "")

    if not await verify_admin_password(old_pw):
        return JSONResponse({"error": "原密码错误"}, status_code=403)
    if len(new_pw) < 6:
        return JSONResponse({"error": "新密码至少 6 个字符"}, status_code=400)
    if len(new_pw) > 128:
        return JSONResponse({"error": "密码过长"}, status_code=400)

    await set_admin_password(new_pw)
    logger.info("管理员密码已修改")
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  IP 黑名单管理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/blocked-ips")
async def api_list_blocked_ips(request: Request):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM blocked_ips ORDER BY created_at DESC"
        )
        return [dict(r) for r in await cursor.fetchall()]


@router.post("/admin/api/blocked-ips")
async def api_add_blocked_ip(request: Request):
    _check_admin(request)
    body = await request.json()
    ip_address = body.get("ip_address", "").strip()
    reason = body.get("reason", "").strip()

    if not ip_address:
        return JSONResponse({"error": "IP 地址不能为空"}, status_code=400)

    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO blocked_ips (ip_address, reason) VALUES (?, ?)",
                (ip_address, reason),
            )
            await db.commit()
        except Exception:
            return JSONResponse({"error": "该 IP 已在黑名单中"}, status_code=409)

    logger.info("IP 已加入黑名单: %s (%s)", ip_address, reason)
    return {"success": True}


@router.delete("/admin/api/blocked-ips/{ip_id}")
async def api_delete_blocked_ip(request: Request, ip_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT ip_address FROM blocked_ips WHERE id=?", (ip_id,))
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "记录不存在"}, status_code=404)
        ip_address = row["ip_address"]
        await db.execute("DELETE FROM blocked_ips WHERE id=?", (ip_id,))
        await db.commit()
    logger.info("IP 已移出黑名单: %s", ip_address)
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  管理员 IP 管理（发留言显示为"管理员"）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/admin-ips")
async def api_list_admin_ips(request: Request):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM admin_ips ORDER BY created_at DESC"
        )
        return [dict(r) for r in await cursor.fetchall()]


@router.post("/admin/api/admin-ips")
async def api_add_admin_ip(request: Request):
    _check_admin(request)
    body = await request.json()
    ip_address = body.get("ip_address", "").strip()
    remark = body.get("remark", "").strip()

    if not ip_address:
        return JSONResponse({"error": "IP 地址不能为空"}, status_code=400)

    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO admin_ips (ip_address, remark) VALUES (?, ?)",
                (ip_address, remark),
            )
            await db.commit()
        except Exception:
            return JSONResponse({"error": "该 IP 已在管理员列表中"}, status_code=409)

    logger.info("管理员 IP 已添加: %s (%s)", ip_address, remark)
    return {"success": True}


@router.delete("/admin/api/admin-ips/{ip_id}")
async def api_delete_admin_ip(request: Request, ip_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute("SELECT ip_address FROM admin_ips WHERE id=?", (ip_id,))
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "记录不存在"}, status_code=404)
        ip_address = row["ip_address"]
        await db.execute("DELETE FROM admin_ips WHERE id=?", (ip_id,))
        await db.commit()
    logger.info("管理员 IP 已移除: %s", ip_address)
    return {"success": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  访客 IP 记录
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/visitor-ips")
async def api_list_visitor_ips(request: Request, range: str = "history"):
    _check_admin(request)
    async with get_db() as db:
        where = ""
        if range == "today":
            where = "WHERE date(v.last_visited_at) = date('now','localtime')"
        elif range == "yesterday":
            where = "WHERE date(v.last_visited_at) = date('now','localtime','-1 days')"
        elif range == "7days":
            where = "WHERE v.last_visited_at >= datetime('now','localtime','-7 days')"

        cursor = await db.execute(
            f"""SELECT v.*, CASE WHEN b.id IS NOT NULL THEN 1 ELSE 0 END AS is_blocked
               FROM visitor_ips v
               LEFT JOIN blocked_ips b ON b.ip_address = v.ip_address
               {where}
               ORDER BY v.last_visited_at DESC"""
        )
        return [dict(r) for r in await cursor.fetchall()]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  解封申请管理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/admin/api/unblock-requests")
async def api_list_unblock_requests(request: Request):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM unblock_requests ORDER BY created_at DESC"
        )
        return [dict(r) for r in await cursor.fetchall()]


@router.post("/admin/api/unblock-requests/{req_id}/approve")
async def api_approve_unblock(request: Request, req_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, ip_address, status FROM unblock_requests WHERE id=?", (req_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "申请不存在")
        req = dict(row)
        if req["status"] != "pending":
            raise HTTPException(400, "该申请已处理")

        # 从黑名单移除
        await db.execute("DELETE FROM blocked_ips WHERE ip_address=?", (req["ip_address"],))
        # 更新申请状态
        await db.execute(
            "UPDATE unblock_requests SET status='approved' WHERE id=?", (req_id,)
        )
        await db.commit()

    logger.info("解封申请已通过: %s", req["ip_address"])
    return {"success": True, "message": "已解封"}


@router.post("/admin/api/unblock-requests/{req_id}/reject")
async def api_reject_unblock(request: Request, req_id: int):
    _check_admin(request)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, ip_address, status FROM unblock_requests WHERE id=?", (req_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "申请不存在")
        req = dict(row)
        if req["status"] != "pending":
            raise HTTPException(400, "该申请已处理")

        await db.execute(
            "UPDATE unblock_requests SET status='rejected' WHERE id=?", (req_id,)
        )
        await db.commit()

    logger.info("解封申请已拒绝: %s (id=%d)", req["ip_address"], req_id)
    return {"success": True, "message": "已拒绝"}
