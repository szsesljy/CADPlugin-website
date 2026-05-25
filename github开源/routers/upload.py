import time
from pathlib import Path
from fastapi import APIRouter, Request, Form, UploadFile, File

from config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, PENDING_DIR
from database import get_db
from dependencies import templates, get_donation_info, logger


router = APIRouter()


@router.get("/upload")
async def upload_page(request: Request):
    donation = await get_donation_info()
    categories = []
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM categories ORDER BY sort")
        for cat in await cursor.fetchall():
            c = dict(cat)
            tc = await db.execute("SELECT * FROM tags WHERE category_id=? ORDER BY sort", (c["id"],))
            c["tags"] = [dict(t) for t in await tc.fetchall()]
            categories.append(c)

    return templates.TemplateResponse(request, "upload.html", {
        "request": request, "donation": donation, "categories": categories,
    })


@router.post("/upload")
async def upload_plugin(request: Request):
    donation = await get_donation_info()
    ctx = {"request": request, "donation": donation}

    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM categories ORDER BY sort")
        categories = []
        for cat in await cursor.fetchall():
            c = dict(cat)
            tc = await db.execute("SELECT * FROM tags WHERE category_id=? ORDER BY sort", (c["id"],))
            c["tags"] = [dict(t) for t in await tc.fetchall()]
            categories.append(c)
        ctx["categories"] = categories

    form = await request.form()
    name = form.get("name", "").strip()
    description = form.get("description", "")
    version = form.get("version", "1.0")
    author = form.get("author", "匿名")
    tag_ids_str = form.get("tag_ids", "")
    file = form.get("file")

    if not name:
        ctx["error"] = "插件名称不能为空"
        return templates.TemplateResponse(request, "upload.html", ctx)

    if not file or not file.filename:
        ctx["error"] = "请选择要上传的文件"
        return templates.TemplateResponse(request, "upload.html", ctx)

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ctx["error"] = f"不支持的文件类型: {ext}"
        return templates.TemplateResponse(request, "upload.html", ctx)

    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{file.filename}"
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    save_path = PENDING_DIR / safe_filename

    size = 0
    with open(save_path, "wb") as f:
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                save_path.unlink(missing_ok=True)
                ctx["error"] = "文件大小超过 100MB 限制"
                return templates.TemplateResponse(request, "upload.html", ctx)
            f.write(chunk)

    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO plugins (name, description, version, author, file_name, file_path, file_size, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            (name, description, version, author, file.filename, f"pending/{safe_filename}", size),
        )
        plugin_id = cursor.lastrowid

        if tag_ids_str:
            for tid_str in tag_ids_str.split(","):
                tid_str = tid_str.strip()
                if tid_str.isdigit():
                    await db.execute(
                        "INSERT OR IGNORE INTO plugin_tags (plugin_id, tag_id) VALUES (?, ?)",
                        (plugin_id, int(tid_str)),
                    )
        await db.commit()

    logger.info("新插件上传: %s | %s | %d bytes", name, author, size)
    ctx["success"] = True
    return templates.TemplateResponse(request, "upload.html", ctx)
