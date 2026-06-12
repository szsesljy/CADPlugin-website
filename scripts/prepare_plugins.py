#!/usr/bin/env python3
"""
准备插件导入包：
  1. 单文件插件直接用
  2. 多文件插件（子目录）打包成 zip
  3. 生成 CSV 元数据
"""
import csv
import shutil
import zipfile
from pathlib import Path

SOURCE = Path(r"D:\CAD插件网站\CAD插件整理")
OUTPUT = Path(r"D:\CAD插件网站\Code\plugins_bundle")
CSV_PATH = OUTPUT / "metadata.csv"
ZIP_DIR = OUTPUT / "zips"

ALLOWED = {".lsp", ".vlx", ".fas", ".dll", ".arx", ".crx", ".dbx",
           ".dvb", ".dcl", ".mnl", ".cui", ".cuix", ".bundle",
           ".zip", ".rar", ".7z"}

# 文件夹 -> (分类, 标签)
FOLDER_TAG_MAP = {
    "01 画图类":   ("建筑", "绘图辅助"),
    "02 文字处理类": ("通用", "文本处理"),
    "03 图层类":   ("通用", "图层管理"),
    "04 工具箱类":  ("通用", "实用工具"),
}

OUTPUT.mkdir(parents=True, exist_ok=True)
ZIP_DIR.mkdir(parents=True, exist_ok=True)

rows = []

for cat_dir in sorted(SOURCE.iterdir()):
    if not cat_dir.is_dir():
        continue
    cat_name, tag_name = FOLDER_TAG_MAP.get(cat_dir.name, ("通用", "其他"))
    print(f"\n{'='*50}")
    print(f"  {cat_dir.name}  ->  {cat_name}/{tag_name}")
    print(f"{'='*50}")

    items = list(cat_dir.iterdir())
    subdirs = [d for d in items if d.is_dir()]
    files = [f for f in items if f.is_file() and f.suffix.lower() in ALLOWED]

    # 单文件插件
    for fp in sorted(files):
        dest = OUTPUT / fp.name
        shutil.copy2(fp, dest)
        rows.append({
            "filename": fp.name,
            "name": fp.stem,
            "description": "",
            "author": "",
            "version": "1.0",
            "tags": f"{cat_name}/{tag_name}",
        })
        print(f"  [复制] {fp.name}")

    # 多文件插件 -> zip
    for sd in sorted(subdirs):
        has_plugins = any(
            f.suffix.lower() in ALLOWED
            for f in sd.rglob("*") if f.is_file()
        )
        if not has_plugins:
            print(f"  [跳过-无插件] {sd.name}/")
            continue

        zip_name = f"{sd.name}.zip"
        zip_path = ZIP_DIR / zip_name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(sd.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(sd.parent))
        dest = OUTPUT / zip_name
        shutil.copy2(zip_path, dest)

        rows.append({
            "filename": zip_name,
            "name": sd.name,
            "description": "",
            "author": "",
            "version": "1.0",
            "tags": f"{cat_name}/{tag_name}",
        })
        print(f"  [打包] {sd.name}/ -> {zip_name}")

# 写 CSV
with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["filename","name","description","author","version","tags"])
    writer.writeheader()
    writer.writerows(rows)

shutil.rmtree(ZIP_DIR)
print(f"\n完成! {len(rows)} 个插件, 输出: {OUTPUT}")
print(f"  CSV: {CSV_PATH}")
