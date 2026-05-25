# CAD 插件平台

> 一个轻量级的 CAD 插件分享与下载平台，基于 FastAPI 构建。

## 功能特性

- **插件浏览** — 按行业/用途分类筛选，支持搜索、排序、分页
- **插件上传** — 用户可上传插件，支持 `.lsp` `.fas` `.vlx` `.dll` 等常见 CAD 插件格式
- **审核机制** — 管理员后台审核上传的插件，通过后自动上架
- **留言互动** — 每个插件详情页和文章页支持留言与回复
- **公告与使用说明** — Markdown 富文本文章发布，支持置顶和关联插件
- **打赏功能** — 可配置收款码，开启后前台显示打赏入口
- **访问统计** — 记录每日页面访问量
- **病毒扫描** — 集成 ClamAV 扫描（可选），辅助审核决策

## 技术栈

- **后端**: Python 3.11+ / FastAPI
- **数据库**: SQLite (aiosqlite)
- **模板**: Jinja2
- **前端**: 原生 HTML + CSS，无前端框架依赖
- **病毒扫描**: ClamAV (可选)

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/cad-plugin-platform.git
cd cad-plugin-platform
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动服务

```bash
python main.py
```

服务默认运行在 `http://localhost:8001`。

### 4. 访问管理后台

打开 `http://localhost:8001/admin`，默认密码 `admin123`。

> **安全提醒**: 首次登录后请立即修改默认密码！

## 环境变量配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_PASSWORD` | 管理员密码 | `admin123` |
| `SESSION_SECRET` | Session 加密密钥 | `change-this-to-a-random-secret` |
| `CAD_STORAGE_ROOT` | 插件文件存储根目录 | `./storage` |

## 项目结构

```
cad-plugin-platform/
├── main.py              # 应用入口
├── config.py            # 全局配置
├── database.py          # 数据库初始化与连接
├── dependencies.py      # 公共依赖（模板、统计等）
├── models.py            # Pydantic 数据模型
├── security.py          # ClamAV 病毒扫描（可选）
├── requirements.txt     # Python 依赖
├── routers/
│   ├── __init__.py
│   ├── upload.py        # 插件上传
│   ├── download.py      # 插件浏览与下载
│   └── admin.py         # 管理后台所有 API
├── templates/           # Jinja2 模板
│   ├── base.html
│   ├── index.html
│   ├── detail.html
│   ├── upload.html
│   ├── admin.html
│   ├── admin_login.html
│   ├── notices.html
│   └── notice_detail.html
└── static/
    ├── style.css
    ├── favicon.svg
    └── logo.svg
```

## 病毒扫描（可选）

如需启用 ClamAV 病毒扫描功能：

```bash
# CentOS / OpenCloudOS
yum install clamav clamav-update
freshclam

# Ubuntu / Debian
apt install clamav clamav-daemon
freshclam
```

扫描仅在管理员审核时触发（仅日志提醒，不阻塞审核流程）。

## 免责声明

本站插件仅供学习交流，请勿用于商业用途。如有侵权，请联系删除。

## License

MIT
