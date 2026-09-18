# CAD 插件平台

一个基于 FastAPI 的 CAD 插件分享与管理平台，面向 CAD 插件作者与使用者：
浏览、搜索、下载插件，配套留言交流、公告发布与完整的后台审核流程。

线上示例：<https://cadchajian.com>

## 功能

- **插件管理**：上传、分类、搜索 CAD 插件（LSP / FAS / VLX / DLL / ARX / CUIX 等 18 种格式）
- **板块系统**：动态管理前台导航板块，每个板块下可添加条目和网盘链接
- **留言系统**：全站留言 + 楼中楼回复，支持 Markdown，配图形验证码防风控刷屏
- **公告系统**：分类公告（使用指南 / 版本公告），支持 Markdown 编辑器
- **人机验证门禁**：访问任意外页面前需通过图形验证码，通过后写入 Session 有效期 24 小时
- **访问统计**：PV / 独立访客趋势、插件浏览与下载明细，含访客 IP 归属地（中国大陆 / 其他地区）
- **后台管理**：插件审核、留言审核、IP 管理、板块与分类管理、访问统计看板
- **安全特性**：IP 黑名单、可选的 ClamAV 病毒扫描、验证码限流
- **响应式设计**：全宽布局，PC 与移动端均可使用；移动版可用独立子域名启用

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/szsesljy/CADPlugin-website.git
cd CADPlugin-website

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动（开发模式）
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload

# 4. 打开浏览器访问
# http://127.0.0.1:8001
```

首次启动会自动建库并写入种子数据（行业分类、用途标签）。管理后台位于 `/admin`。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CAD_STORAGE_ROOT` | 插件文件存储路径 | `./storage` |
| `ADMIN_PASSWORD` | 管理后台密码 | `admin123` |
| `SESSION_SECRET` | Session 加密密钥 | `cad-platform-secret-key-change-it` |
| `MOBILE_SITE_HOST_PREFIX` | 手机版子域名前缀，匹配时启用移动端布局；留空则关闭 | `phone.` |
| `CAD_PORT` | 部署脚本写入 systemd 时使用的端口 | `8001` |

> **生产环境务必修改 `ADMIN_PASSWORD` 和 `SESSION_SECRET`。**

## 人机验证门禁

`main.py` 的 `HumanVerifyMiddleware` 会拦截未通过验证的访问：浏览器页面请求 302 跳转到
`/verify` 完成图形验证码，接口/文件请求直接返回 403。验证通过后写入 Session，有效期 24 小时。

以下请求不拦截，可按需调整 `main.py` 顶部的常量：

- `/verify`、`/static`、`/unblock`、`/admin/api/blocked-ips`（前缀豁免）
- `/ads.txt`、`/favicon.ico`、`/robots.txt`（精确豁免）
- 搜索引擎蜘蛛与可用性监控的 User-Agent（Googlebot、Bingbot、Baiduspider、UptimeRobot 等）
  —— 保留这段是为了不影响页面收录与站点监控；如需「所有访客一律验证」，删掉
  `_SEARCH_ENGINE_HINTS` 的判断即可。

## 手机版

前台布局由模板变量 `is_mobile_site` 控制。当请求的 Host 以 `MOBILE_SITE_HOST_PREFIX`
（默认 `phone.`）开头时启用移动端布局：底部标签导航栏、汉堡菜单抽屉、隐藏桌面端导航，
并提供「电脑版 / 手机版」互相切换入口。

例如配置 `MOBILE_SITE_HOST_PREFIX=phone.` 后：

| 访问域名 | 布局 |
|----------|------|
| `example.com` | 桌面版 |
| `phone.example.com` | 手机版 |

不需要独立手机版时，把 `MOBILE_SITE_HOST_PREFIX` 设为空字符串即可关闭。

## 技术栈

- **后端**：Python / FastAPI + aiosqlite
- **前端**：Jinja2 + 原生 JavaScript + CSS（无前端构建步骤）
- **数据库**：SQLite
- **其他**：Pillow（图形验证码）、maxminddb-geolite2（IP 归属地，离线库）
- **部署**：Uvicorn + Nginx（反向代理）

## 项目结构

```
├── main.py                 # 应用入口 + 中间件（IP 黑名单 / Session / 人机验证门禁）
├── config.py               # 配置（环境变量集中在此）
├── database.py             # 数据库建表、迁移与种子数据
├── models.py               # Pydantic 模型
├── dependencies.py         # 模板上下文注入、访问统计、点击记录
├── security.py             # ClamAV 病毒扫描
├── geoip.py                # IP 归属地判断（离线 GeoLite2 国家库）
├── import_plugins.py       # 批量导入插件工具
├── deploy*.bat / deploy.sh / package.sh / start.bat   # 部署打包脚本（git 忽略）
├── routers/
│   ├── admin.py            # 后台管理 API
│   ├── download.py         # 前台页面 + 公开 API
│   ├── upload.py           # 插件上传
│   └── verify.py           # 人机验证（图形验证码生成、校验、限流）
├── templates/              # Jinja2 模板
│   ├── base.html           # 基础布局（导航、手机版底部导航、页脚）
│   ├── index.html          # 首页（筛选、热榜、插件网格、留言区）
│   ├── detail.html         # 插件详情 + 留言反馈
│   ├── board_list.html / board_detail.html   # 板块列表 / 条目明细
│   ├── notices.html / notice_detail.html     # 公告列表 / 阅读页
│   ├── upload.html         # 插件上传
│   ├── captcha.html        # 人机验证页
│   ├── unblock.html        # 申请解封
│   └── admin.html / admin_login.html         # 后台看板 / 登录
├── static/                 # 静态文件（CSS / SVG 图标）
├── storage/                # 插件文件存储（pending / approved，git 忽略）
├── scripts/                # 工具脚本（prepare_plugins、cleanup_messages、verify_admin_download_list）
├── docs/                   # 文档（部署、计划、更新日志、使用方法、验证码样例等）
├── archives/               # 构建产物与备份包（git 忽略）
└── requirements.txt        # Python 依赖
```

> 注：`deploy.bat`、`deploy_update.bat`、`deploy.sh`、`package.sh`、`start.bat`
> 与 `import_plugins.py` 必须留在根目录 —— 打包脚本依赖它们在项目根运行，
> `deploy.sh` 在服务器端要求与 `main.py` 同目录，移动会导致部署失败。

## 部署

`deploy.sh` 会安装依赖、创建系统用户与虚拟环境、生成 `.env`（含随机密码）、
配置 systemd 服务与 Nginx 反向代理，并可选申请 Let's Encrypt 证书。

```bash
# 本地打包
bash package.sh
# 上传
scp cad-plugin-platform.tar.gz root@你的服务器IP:/opt/
# 服务器端部署
ssh root@你的服务器IP "cd /opt && bash deploy.sh"
```

Windows 下可用 `deploy_update.bat` 做增量更新（仅打包源码文件并重启服务）。
注意：新增模板时需同步加入该脚本的打包清单，否则服务器上会缺文件。

详见 `docs/DEPLOY.md`。

## 截图

<!-- TODO: 添加截图 -->

## License

[MIT](LICENSE)
