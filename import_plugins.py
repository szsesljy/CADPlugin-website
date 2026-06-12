#!/usr/bin/env python3
"""
批量导入插件脚本：
  将文件夹中的插件文件直接导入到 approved 目录（免审核上架）。

  用法:
    python import_plugins.py ./插件文件夹/
    python import_plugins.py ./插件文件夹/ --author "作者名"
    python import_plugins.py ./插件文件夹/ --check-hash

  高级用法（带 CSV 元数据，支持标签）:
    python import_plugins.py ./插件文件夹/ --csv metadata.csv

  CSV 格式 (UTF-8):
    filename,name,description,author,version,tags
    文件.lsp,显示名称,描述文字,作者,1.0,建筑/绘图辅助;机械/图层管理
    tags 列用 分类名/标签名 格式，多个标签用分号隔开
"""

import asyncio
import csv
import hashlib
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import ALLOWED_EXTENSIONS, APPROVED_DIR
from database import init_database, get_db


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_csv_meta(csv_path: str) -> dict:
    """读取 CSV 元数据，返回 {文件名: {name, description, author, version, tags}}"""
    meta = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fname = row.get("filename", "").strip()
            if not fname:
                continue
            tags_raw = row.get("tags", "").strip()
            tag_pairs = []
            if tags_raw:
                for part in tags_raw.split(";"):
                    part = part.strip()
                    if "/" in part:
                        cat_name, tag_name = part.split("/", 1)
                        tag_pairs.append((cat_name.strip(), tag_name.strip()))
            meta[fname] = {
                "name": row.get("name", "").strip() or Path(fname).stem,
                "description": row.get("description", "").strip(),
                "author": row.get("author", "").strip() or "匿名",
                "version": row.get("version", "").strip() or "1.0",
                "tags": tag_pairs,
            }
    return meta


async def resolve_tags(db, tag_pairs: list) -> list[int]:
    """根据 (分类名, 标签名) 查找或创建标签，返回 tag_id 列表"""
    tag_ids = []
    for cat_name, tag_name in tag_pairs:
        cursor = await db.execute("SELECT id FROM categories WHERE name=?", (cat_name,))
        row = await cursor.fetchone()
        if row:
            cat_id = row[0]
        else:
            cursor = await db.execute("SELECT COALESCE(MAX(sort),-1)+1 FROM categories")
            sort = (await cursor.fetchone())[0]
            await db.execute("INSERT INTO categories (name, sort) VALUES (?, ?)", (cat_name, sort))
            await db.commit()
            cursor = await db.execute("SELECT id FROM categories WHERE name=?", (cat_name,))
            cat_id = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT id FROM tags WHERE category_id=? AND name=?", (cat_id, tag_name)
        )
        row = await cursor.fetchone()
        if row:
            tag_ids.append(row[0])
        else:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(sort),-1)+1 FROM tags WHERE category_id=?", (cat_id,)
            )
            sort = (await cursor.fetchone())[0]
            await db.execute(
                "INSERT INTO tags (category_id, name, sort) VALUES (?, ?, ?)",
                (cat_id, tag_name, sort),
            )
            tag_ids.append(cursor.lastrowid)
    return tag_ids


def import_plugins(folder: str, author: str = "匿名", check_hash: bool = False, csv_path: str = None):
    folder_path = Path(folder).resolve()
    if not folder_path.is_dir():
        print(f"错误: 目录不存在: {folder_path}")
        sys.exit(1)

    csv_meta = {}
    if csv_path:
        csv_file = Path(csv_path).resolve()
        if not csv_file.exists():
            print(f"错误: CSV 文件不存在: {csv_file}")
            sys.exit(1)
        csv_meta = load_csv_meta(str(csv_file))
        print(f"  加载元数据: {len(csv_meta)} 个插件")

    async def _run():
        await init_database()
        async with get_db() as db:
            cursor = await db.execute("SELECT file_name FROM plugins WHERE status='approved'")
            existing_names = {row[0] for row in await cursor.fetchall()}

            existing_hashes = set()
            if check_hash:
                cursor = await db.execute("SELECT file_path FROM plugins")
                for (row,) in await cursor.fetchall():
                    fp = APPROVED_DIR / Path(row).name
                    if fp.exists():
                        existing_hashes.add(file_sha256(fp))

            files = []
            for f in folder_path.rglob("*"):
                if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
                    files.append(f)

            if not files:
                print("未找到支持的插件文件")
                return

            imported = 0
            skipped = 0
            APPROVED_DIR.mkdir(parents=True, exist_ok=True)

            for fp in files:
                if fp.name in existing_names:
                    skipped += 1
                    continue
                if check_hash:
                    if file_sha256(fp) in existing_hashes:
                        skipped += 1
                        continue

                meta = csv_meta.get(fp.name, {})
                name = meta.get("name", fp.stem)
                description = meta.get("description", "")
                ver = meta.get("version", "1.0")
                au = meta.get("author", author)

                ts = int(time.time())
                safe_name = f"{ts}_{fp.name}"
                dest = APPROVED_DIR / safe_name
                dest.write_bytes(fp.read_bytes())
                size = fp.stat().st_size

                cursor = await db.execute(
                    "INSERT INTO plugins (name, description, version, author, file_name, file_path, file_size, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved')",
                    (name, description, ver, au, fp.name, f"approved/{safe_name}", size),
                )
                plugin_id = cursor.lastrowid

                tag_pairs = meta.get("tags", [])
                if tag_pairs:
                    tag_ids = await resolve_tags(db, tag_pairs)
                    for tid in tag_ids:
                        await db.execute(
                            "INSERT OR IGNORE INTO plugin_tags (plugin_id, tag_id) VALUES (?, ?)",
                            (plugin_id, tid),
                        )

                imported += 1
                tag_info = f" [{len(tag_pairs)} 标签]" if tag_pairs else ""
                print(f"  [+] {fp.name} -> {name}{tag_info}")

            await db.commit()
            print(f"\n完成! 导入: {imported}, 跳过: {skipped}")

    asyncio.run(_run())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量导入 CAD 插件")
    parser.add_argument("folder", help="插件文件夹路径")
    parser.add_argument("--author", default="匿名", help="作者名称（默认: 匿名）")
    parser.add_argument("--check-hash", action="store_true", help="启用 SHA256 去重（默认按文件名去重）")
    parser.add_argument("--csv", default=None, help="CSV 元数据文件路径（支持标签、描述）")
    args = parser.parse_args()
    import_plugins(args.folder, args.author, args.check_hash, args.csv)
