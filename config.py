import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

# 存储根目录 — 通过环境变量 CAD_STORAGE_ROOT 配置
# 开发环境默认 ./storage，生产环境设为 /lhcos-data/cad-plugins/storage
STORAGE_ROOT = Path(os.getenv("CAD_STORAGE_ROOT", str(BASE_DIR / "storage")))

PENDING_DIR = STORAGE_ROOT / "pending"
APPROVED_DIR = STORAGE_ROOT / "approved"
DONATION_DIR = BASE_DIR / "donation_qrcode"

DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

DATABASE_PATH = DATA_DIR / "database.db"

ALLOWED_EXTENSIONS = {
    ".lsp", ".dll", ".fas", ".vlx", ".zip", ".rar", ".7z",
    ".cs", ".arx", ".crx", ".dbx", ".dvb", ".dcl", ".mnl",
    ".cui", ".cuix", ".bundle", ".txt",
}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SESSION_SECRET = os.getenv("SESSION_SECRET", "cad-platform-secret-key-change-it")

ITEMS_PER_PAGE = 12
