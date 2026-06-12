# CAD 插件平台

一个基于 FastAPI 的 CAD 插件分享与管理平台。

## 功能

- **插件管理**：上传、分类、搜索 CAD 插件（LSP / FAS / VLX / DLL 等格式）
- **板块系统**：动态管理前台导航板块，每个板块下可添加条目和网盘链接
- **留言系统**：全站留言 + 楼中楼回复，支持 Markdown
- **公告系统**：分类公告（使用指南 / 版本公告），支持 Markdown 编辑器
- **后台管理**：插件审核、留言审核、IP 管理、访问统计
- **安全特性**：IP 黑名单、ClamAV 病毒扫描（可选）
- **响应式设计**：全宽布局，PC 和移动端均可使用

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/你的用户名/cad-plugin-platform.git
cd cad-plugin-platform

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动（开发模式）
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload

# 4. 打开浏览器访问
# http://127.0.0.1:8001
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CAD_STORAGE_ROOT` | 插件文件存储路径 | `./storage` |
| `ADMIN_PASSWORD` | 管理后台密码 | `admin123` |
| `SESSION_SECRET` | Session 加密密钥 | `cad-platform-secret-key-change-it` |

> **生产环境务必修改 `ADMIN_PASSWORD` 和 `SESSION_SECRET`。**

## 技术栈

- **后端**：Python / FastAPI + aiosqlite
- **前端**：Jinja2 + 原生 JavaScript + CSS
- **数据库**：SQLite
- **部署**：Uvicorn + Nginx（反向代理）

## 项目结构

```
├── main.py                 # 应用入口
├── config.py               # 配置（环境变量）
├── database.py             # 数据库初始化与操作
├── models.py               # Pydantic 模型
├── security.py             # ClamAV 病毒扫描
├── dependencies.py         # 模板注入、统计等依赖
├── routers/
│   ├── admin.py            # 后台管理 API
│   ├── download.py         # 前台页面 + 公开 API
│   └── upload.py           # 插件上传
├── templates/              # Jinja2 模板
│   ├── base.html           # 基础布局
│   ├── index.html          # 首页
│   ├── detail.html         # 插件详情
│   ├── admin.html          # 管理后台
│   └── ...
├── static/                 # 静态文件（CSS / 图片）
└── requirements.txt        # Python 依赖
```

## 截图

<!-- TODO: 添加截图 -->

## License

[MIT](LICENSE)
