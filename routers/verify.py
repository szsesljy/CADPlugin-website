import base64
import io
import random
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from dependencies import logger, templates

router = APIRouter()

# ── 人机验证参数 ──
BG_W, BG_H = 320, 160       # 背景图尺寸（像素，与前端 1:1 对应）
PUZZ = 46                   # 拼图块边长
TOLERANCE = 6               # 判定通过允许的误差（像素）
CHALLENGE_TTL = 120         # 一次挑战的有效期（秒）
CHALLENGE_MAX = 300         # 内存中最多保留的挑战数
RATE_LIMIT = 20             # 同一 IP 每分钟最多获取/尝试挑战次数
VERIFY_TTL = 24 * 3600      # 一次验证通过后的免验证时长（秒）
SESSION_VERIFIED_KEY = "verified_at"

# 背景渐变配色池
_BG_PALETTES = [
    ((58, 96, 178), (126, 168, 236)),
    ((56, 130, 140), (125, 200, 205)),
    ((122, 90, 176), (190, 155, 235)),
    ((146, 90, 110), (226, 165, 180)),
    ((52, 110, 90), (130, 200, 170)),
    ((96, 106, 132), (170, 182, 214)),
]

# 叠加装饰色（半透明亮色块）
_DECO_COLORS = [
    (255, 255, 255), (210, 224, 255), (255, 226, 180),
    (196, 244, 214), (255, 200, 222), (150, 205, 255),
]


def _client_ip(request: Request) -> str:
    """从请求中提取客户端 IP（优先取 x-forwarded-for 第一项）"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def is_human_verified(session: dict) -> bool:
    """session 中有人机验证通过标记且在有效期内则返回 True"""
    ts = session.get(SESSION_VERIFIED_KEY)
    if not isinstance(ts, (int, float)):
        return False
    # 用 <= 兜底：Windows 上 time.time() 精度低，刚写入后立刻校验可能拿到相同值
    return 0 <= time.time() - ts < VERIFY_TTL


# ── 图片生成 ──

def _make_background(rng) -> Image.Image:
    """随机生成一张拼图背景：上下渐变 + 半透明色块/弧线 + 轻微模糊"""
    top, bottom = rng.choice(_BG_PALETTES)
    img = Image.new("RGB", (BG_W, BG_H))
    d = ImageDraw.Draw(img)
    for y in range(BG_H):
        t = y / (BG_H - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (BG_W - 1, y)], fill=color)

    overlay = Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for _ in range(rng.randint(14, 20)):
        r = rng.randint(10, 70)
        x = rng.randint(-30, BG_W + 20)
        y = rng.randint(-30, BG_H + 20)
        color = rng.choice(_DECO_COLORS) + (rng.randint(30, 90),)
        od.ellipse([x - r, y - r, x + r, y + r], fill=color)
    for _ in range(rng.randint(3, 5)):
        x0, y0 = rng.randint(0, BG_W), rng.randint(0, BG_H)
        x1 = rng.randint(0, BG_W)
        y1 = rng.randint(0, BG_H)
        width = rng.randint(2, 6)
        color = rng.choice(_DECO_COLORS) + (rng.randint(25, 60),)
        od.line([x0, y0, x1, y1], fill=color, width=width)

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img.filter(ImageFilter.GaussianBlur(0.7))


def _make_piece_mask(rng) -> Image.Image:
    """生成拼图块的镂空蒙版：圆角方块 + 四条边各挖一个内凹半圆缺口"""
    mask = Image.new("L", (PUZZ, PUZZ), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, PUZZ - 1, PUZZ - 1), radius=7, fill=255)
    r = rng.choice([5, 5, 6])
    # 左右边缘挖口（垂直位置随机）
    for cx in (0, PUZZ - 1):
        cy = rng.randint(12, 34)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
    # 上下边缘挖口（水平位置随机）
    for cy in (0, PUZZ - 1):
        cx = rng.randint(12, 34)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
    return mask


def _make_challenge(rng):
    """生成一张挑战图，返回 (背景图RGB, 拼图块RGBA, 缺口纵坐标y, 目标横坐标x)"""
    bg = _make_background(rng)
    mask = _make_piece_mask(rng)
    tx = rng.randint(60, BG_W - PUZZ - 24)
    ty = rng.randint(6, BG_H - PUZZ - 6)
    box = (tx, ty, tx + PUZZ, ty + PUZZ)

    # 挖口：缺口区域压暗为原纹理的约一半亮度（保留纹理，观感更接近主流拼图验证）
    orig_crop = bg.crop(box)
    dimmed = ImageEnhance.Brightness(orig_crop).enhance(0.45)
    bg.paste(Image.composite(dimmed, orig_crop, mask), (tx, ty))
    # 拼图块：背景原图按蒙版裁切，四周透明
    piece = Image.new("RGBA", (PUZZ, PUZZ), (0, 0, 0, 0))
    piece.paste(orig_crop, (0, 0), mask)
    return bg, piece, ty, tx


def _png_bytes(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── 内存挑战状态（单机内存即可，无需落库） ──

_CHALLENGES: dict[str, dict] = {}
_RATE: dict[str, list] = {}


def _prune_challenges():
    now = time.time()
    for k in [k for k, v in _CHALLENGES.items() if now - v["at"] > CHALLENGE_TTL]:
        _CHALLENGES.pop(k, None)
    while len(_CHALLENGES) > CHALLENGE_MAX:
        _CHALLENGES.pop(next(iter(_CHALLENGES)))


def _rate_limited(ip: str) -> bool:
    """同一 IP 每分钟操作次数限制，超限返回 True"""
    now = time.time()
    stamps = [t for t in _RATE.get(ip, []) if now - t < 60]
    if len(stamps) >= RATE_LIMIT:
        _RATE[ip] = stamps
        return True
    stamps.append(now)
    _RATE[ip] = stamps
    return False


# ── 路由 ──

def _safe_next(next_url: str) -> str:
    """只允许站内相对路径跳转，防止开放重定向"""
    next_url = next_url or "/"
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@router.get("/verify")
async def verify_page(request: Request, next: str = "/"):
    """人机验证页（已通过则直接跳回原页面）"""
    if is_human_verified(request.session):
        return RedirectResponse(_safe_next(next), status_code=302)
    return templates.TemplateResponse(request, "captcha.html", {
        "next": _safe_next(next),
    })


@router.get("/verify/challenge")
async def new_challenge(request: Request):
    """生成一次拼图挑战：目标位置只保存在服务器，前端拿不到答案"""
    ip = _client_ip(request)
    if _rate_limited(ip):
        raise HTTPException(429, "操作过于频繁，请稍后再试")

    token = secrets.token_urlsafe(18)
    bg, piece, ty, tx = _make_challenge(random.SystemRandom())
    _prune_challenges()
    _CHALLENGES[token] = {"x": tx, "at": time.time()}

    return JSONResponse(
        {
            "token": token,
            "w": BG_W, "h": BG_H, "size": PUZZ,
            "y": ty,
            "bg": _png_bytes(bg.convert("RGB")),
            "piece": _png_bytes(piece),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/verify/check")
async def check_challenge(request: Request):
    """校验拖动结果：每次校验都会消耗该挑战（防暴力猜测）"""
    ip = _client_ip(request)
    if _rate_limited(ip):
        raise HTTPException(429, "操作过于频繁，请稍后再试")

    body = await request.json()
    token = (body.get("token") or "").strip()
    x = body.get("x")
    entry = _CHALLENGES.pop(token, None) if token else None
    if not entry or not isinstance(x, (int, float)):
        return {"ok": False, "msg": "验证已过期，请重试"}

    if abs(entry["x"] - float(x)) <= TOLERANCE:
        request.session[SESSION_VERIFIED_KEY] = time.time()
        logger.info("人机验证通过: IP=%s", ip)
        return {"ok": True}
    return {"ok": False, "msg": "没对准位置，再试一次"}
