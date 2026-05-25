from contextlib import asynccontextmanager

import aiosqlite
from config import DATABASE_PATH, DATA_DIR


@asynccontextmanager
async def get_db():
    """获取数据库连接（异步上下文管理器，自动关闭）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        await db.close()


async def init_database():
    """初始化数据库表结构及种子数据"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")

        # ── 插件表（新增 disabled 字段支持下架） ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS plugins (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                version     TEXT DEFAULT '1.0',
                author      TEXT DEFAULT '匿名',
                file_name   TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                file_size   INTEGER DEFAULT 0,
                downloads   INTEGER DEFAULT 0,
                status      TEXT DEFAULT 'pending',
                disabled    INTEGER DEFAULT 0,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── 行业（一级分类） ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sort INTEGER DEFAULT 0
            )
        """)

        # ── 用途标签（二级分类） ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                sort        INTEGER DEFAULT 0
            )
        """)

        # ── 插件-标签关联（多对多） ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS plugin_tags (
                plugin_id INTEGER NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
                tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (plugin_id, tag_id)
            )
        """)

        # ── 打赏配置 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS donation_config (
                id          INTEGER PRIMARY KEY DEFAULT 1,
                qrcode_path TEXT,
                title       TEXT DEFAULT '支持开发者',
                enabled     INTEGER DEFAULT 0
            )
        """)

        # ── 种子数据：仅在表为空时写入 ──
        cursor = await db.execute("SELECT COUNT(*) FROM categories")
        if (await cursor.fetchone())[0] == 0:
            seed_data = [
                ("建筑", [
                    "绘图辅助", "标注尺寸", "图块管理", "图层管理",
                    "打印输出", "其他",
                ]),
                ("机械", [
                    "绘图辅助", "标注尺寸", "零件库", "图层管理",
                    "打印输出", "其他",
                ]),
                ("电气", [
                    "绘图辅助", "标注尺寸", "符号库", "图层管理",
                    "打印输出", "其他",
                ]),
                ("土木", [
                    "绘图辅助", "标注尺寸", "图层管理", "计算工具",
                    "打印输出", "其他",
                ]),
                ("测绘", [
                    "数据处理", "坐标工具", "图层管理", "打印输出",
                    "其他",
                ]),
                ("通用", [
                    "文本处理", "批量修改", "线型工具", "数据导出",
                    "实用工具", "其他",
                ]),
            ]
            for sort_i, (cat_name, tag_names) in enumerate(seed_data):
                await db.execute(
                    "INSERT INTO categories (name, sort) VALUES (?, ?)",
                    (cat_name, sort_i),
                )
                cat_cursor = await db.execute(
                    "SELECT id FROM categories WHERE name=?",
                    (cat_name,),
                )
                cat_row = await cat_cursor.fetchone()
                if cat_row:
                    cat_id = cat_row[0]
                    for sort_j, tag_name in enumerate(tag_names):
                        await db.execute(
                            "INSERT INTO tags (category_id, name, sort) VALUES (?, ?, ?)",
                            (cat_id, tag_name, sort_j),
                        )

        # ── 文章表（通知/使用说明） ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT NOT NULL CHECK(type IN ('notice','guide')),
                title       TEXT NOT NULL,
                content     TEXT NOT NULL DEFAULT '',
                plugin_id   INTEGER REFERENCES plugins(id) ON DELETE SET NULL,
                pinned      INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_type ON articles(type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_plugin ON articles(plugin_id)")

        # ── 迁移：为已有数据库添加 pinned 列 ──
        try:
            await db.execute("ALTER TABLE articles ADD COLUMN pinned INTEGER DEFAULT 0")
        except Exception:
            pass  # 列已存在，忽略

        # ── 打赏初始行 ──
        cursor = await db.execute("SELECT COUNT(*) FROM donation_config")
        if (await cursor.fetchone())[0] == 0:
            await db.execute("INSERT INTO donation_config (id) VALUES (1)")

        # ── 留言表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id   INTEGER REFERENCES plugins(id) ON DELETE CASCADE,
                article_id  INTEGER REFERENCES articles(id) ON DELETE CASCADE,
                parent_id   INTEGER REFERENCES messages(id) ON DELETE CASCADE,
                author      TEXT NOT NULL DEFAULT '匿名',
                content     TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'approved',
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        # ── 迁移：为已有数据库添加 article_id 列 ──
        for col in ("article_id", "parent_id"):
            try:
                await db.execute(f"ALTER TABLE messages ADD COLUMN {col} INTEGER")
            except Exception:
                pass

        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_plugin ON messages(plugin_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_article ON messages(article_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status)")

        # ── 页面访问统计 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS page_views (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                date    TEXT NOT NULL UNIQUE,
                count   INTEGER DEFAULT 0
            )
        """)

        # ── 系统设置键值表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # ── 迁移：已有留言改为默认通过 ──
        try:
            await db.execute("UPDATE messages SET status='approved' WHERE status='pending'")
        except Exception:
            pass

        await db.commit()


# ── 管理员密码管理 ──

import hashlib
import secrets

def _hash_password(password: str) -> str:
    """返回 salt$hash 格式的密码哈希"""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${h.hex()}"


def _check_password(password: str, stored: str) -> bool:
    """验证密码是否匹配 stored（salt$hash 格式）"""
    if "$" not in stored:
        return False
    salt, h = stored.split("$", 1)
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex() == h


async def get_admin_password_hash() -> str | None:
    """从数据库获取管理员密码哈希，没有则返回 None"""
    async with get_db() as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key='admin_password'")
        row = await cursor.fetchone()
        return row["value"] if row else None


async def set_admin_password(password: str):
    """设置新的管理员密码（存入数据库）"""
    hashed = _hash_password(password)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('admin_password', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=?",
            (hashed, hashed),
        )
        await db.commit()


async def verify_admin_password(password: str) -> bool:
    """验证管理员密码（优先查数据库，没有则用环境变量）"""
    from config import ADMIN_PASSWORD
    stored = await get_admin_password_hash()
    if stored:
        return _check_password(password, stored)
    # 没有自定义密码时，对比环境变量默认值
    return password == ADMIN_PASSWORD
