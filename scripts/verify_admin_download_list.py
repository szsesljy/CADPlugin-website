#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管理后台「下载明细列表」功能验证脚本
====================================
验证内容（对应 更新日志 2026-08-06 的改动）：
  1. 页面加载默认在列表显示最新一天的下载明细
  2. 悬停下载量折线图数据点 → 下方列表切换到该日期的完整明细（不截断）
  3. 鼠标移开图表 → 列表保留最近查看的日期
  4. 悬停另一天 → 列表正确切换

自动完成：启动服务（未运行时）→ 插入测试下载记录 → 浏览器验证 → 清理测试数据 → 停止服务。

依赖：pip install selenium，本机安装 Chrome 或 Edge。
用法：
  python verify_admin_download_list.py                    # 完整流程
  python verify_admin_download_list.py --no-seed          # 服务已在运行、不种数据（用真实数据验证）
  python verify_admin_download_list.py --browser chrome   # 指定浏览器
  python verify_admin_download_list.py --password xxx     # 管理员密码（默认 admin123）

测试数据使用文档保留网段 203.0.113.0/24（RFC 5737），不会与真实访问 IP 混淆，
脚本退出时自动删除。
"""

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()  # scripts/ 的上一级 = 项目根目录
DB_PATH = ROOT / "data" / "database.db"
BASE_URL = "http://127.0.0.1:8001"

TEST_IP_PREFIX = "203.0.113."   # RFC 5737 文档网段，绝无真实流量
SEED_DAYS = 7
BIG_IPS_DAY = SEED_DAYS - 2     # 倒数第 2 天，10 个 IP（旧实现最多显示 7 个，用于验证不截断）


def log(msg):
    print(f"[verify] {msg}")


# ── 服务管理 ──

def server_up():
    import urllib.request
    try:
        with urllib.request.urlopen(BASE_URL + "/", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def stop_process_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.terminate()


# ── 测试数据 ──

def seed_data():
    """插入 7 天下载记录，返回 [(date_str, [ip, ...]), ...]（按日期升序）"""
    import random
    conn = sqlite3.connect(DB_PATH)
    try:
        random.seed(42)
        today = date.today()
        result = []
        for i in range(SEED_DAYS):
            day = (today - timedelta(days=SEED_DAYS - 1 - i)).isoformat()
            n_ips = 10 if i == BIG_IPS_DAY else (3 if i % 2 else 1)
            ips = [f"{TEST_IP_PREFIX}{k}" for k in range(1, n_ips + 1)]
            rows = []
            for ip in ips:
                cnt = random.randint(1, 4) if i >= 2 else 1
                rows += [(ip, cnt)] * cnt
            for ip, cnt in rows:
                conn.execute(
                    "INSERT INTO click_log (event_type, target_id, target_type, ip_address, created_at)"
                    " VALUES ('download', 1, 'plugin', ?, ?)",
                    (ip, day + f" 10:{random.randint(0, 59):02d}:00"),
                )
            result.append((day, ips))
        conn.commit()
        total = conn.execute(
            "SELECT COUNT(*) FROM click_log WHERE ip_address LIKE ?",
            (TEST_IP_PREFIX + "%",),
        ).fetchone()[0]
        log(f"已插入 {total} 条测试下载记录（{len(result)} 天，IP 前缀 {TEST_IP_PREFIX}）")
        return result
    finally:
        conn.close()


def cleanup_data():
    conn = sqlite3.connect(DB_PATH)
    try:
        n = conn.execute(
            "DELETE FROM click_log WHERE ip_address LIKE ?",
            (TEST_IP_PREFIX + "%",),
        ).rowcount
        conn.commit()
        log(f"已清理 {n} 条测试数据")
    finally:
        conn.close()


# ── 浏览器验证 ──

def make_driver(browser):
    from selenium import webdriver
    opts = [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--window-size=1600,1200",
    ]
    if browser == "chrome":
        from selenium.webdriver.chrome.options import Options as O
        o = O()
    else:
        from selenium.webdriver.edge.options import Options as O
        o = O()
    for a in opts:
        o.add_argument(a)
    if browser == "chrome":
        return webdriver.Chrome(options=o)
    return webdriver.Edge(options=o)


def login(drv, password):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    drv.get(BASE_URL + "/admin/login")
    drv.find_element(By.NAME, "password").send_keys(password)
    drv.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
    WebDriverWait(drv, 10).until(
        lambda d: "dl-detail" in d.find_element(By.ID, "downloadDetail").get_attribute("class")
    )


def point_coords(drv, idx):
    """计算图表第 idx 个数据点的视口坐标（与页面 drawMultiLineChart 公式一致）"""
    return drv.execute_script("""
        const c = document.getElementById('downloadChart');
        c.scrollIntoView({block:'center', inline:'center'});
        const r = c.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 2;
        const n = dlData.length;
        const padL = 60, padR = 24, padT = 32, padB = 48;
        const xStep = (r.width * dpr - padL - padR) / (n - 1);
        const vals = dlData.map(d => d.count || 0);
        const max = Math.max(...vals, 1);
        const h = r.height * dpr - padT - padB;
        const px = padL + arguments[0] * xStep;
        const py = padT + h - (dlData[arguments[0]].count / max) * h;
        return { clientX: r.x + px / dpr, clientY: r.y + py / dpr };
    """, idx)


def hover_point(drv, idx):
    """把鼠标移到图表第 idx 个数据点上（精确坐标派发，headless 下 ActionChains 不可靠）"""
    pt = point_coords(drv, idx)
    drv.execute_script("""
        const c = document.getElementById('downloadChart');
        c.dispatchEvent(new MouseEvent('mousemove', {
            clientX: arguments[0], clientY: arguments[1], bubbles: true
        }));
    """, pt["clientX"], pt["clientY"])


def leave_chart(drv):
    """鼠标移出图表（远离所有数据点）"""
    drv.execute_script("""
        const c = document.getElementById('downloadChart');
        const r = c.getBoundingClientRect();
        c.dispatchEvent(new MouseEvent('mousemove', {
            clientX: r.x - 300, clientY: r.y - 300, bubbles: true
        }));
    """)


def list_state(drv):
    return drv.execute_script("""
        const el = document.getElementById('downloadDetail');
        return {
            title: (el.querySelector('.dl-detail-title') || {}).textContent || '',
            total: (el.querySelector('.dl-detail-total') || {}).textContent || '',
            rows: [...el.querySelectorAll('.dl-detail-row')].map(r => r.textContent),
        };
    """)


def page_dates(drv):
    """页面上 dlData 各点对应的日期（按图表横轴顺序）"""
    return drv.execute_script("return dlData.map(d => d.date);")


def run_checks(drv, seed, shots_dir):
    from selenium.webdriver.common.by import By
    checks = []

    def check(name, cond, detail):
        checks.append((name, cond, detail))
        log(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))
        if not cond:
            raise AssertionError(name + ": " + detail)

    dates = page_dates(drv)
    log("图表日期顺序: " + ", ".join(dates))
    today = date.today().isoformat()

    # 1. 默认显示最新一天
    s = list_state(drv)
    check("1 默认显示最新一天", s["title"].startswith(dates[-1]),
          f"期望 {dates[-1]}, 实际 {s['title']}")
    drv.save_screenshot(str(Path(shots_dir) / "1_default.png"))

    # 2. 悬停数据点 → 列表切换并完整展示（种子数据那天 10 个 IP > 旧上限 7）
    target = dates[BIG_IPS_DAY]
    expected_ips = [ip for d, ips in seed if d == target for ip in ips]
    hover_point(drv, BIG_IPS_DAY)
    time.sleep(0.5)
    s = list_state(drv)
    check("2 悬停切换日期", s["title"].startswith(target), f"实际 {s['title']}")
    check("2 完整列出全部 IP", len(s["rows"]) == len(expected_ips),
          f"期望 {len(expected_ips)} 行, 实际 {len(s['rows'])}")
    check("2 无截断文字", "共" not in s["total"], s["total"])
    drv.save_screenshot(str(Path(shots_dir) / "2_hover_full_list.png"))

    # 3. 移开鼠标 → 列表保留
    leave_chart(drv)
    time.sleep(0.4)
    s = list_state(drv)
    check("3 移开后保留日期", s["title"].startswith(target), f"实际 {s['title']}")
    drv.save_screenshot(str(Path(shots_dir) / "3_after_leave.png"))

    # 4. 悬停另一天 → 正确切换（种子数据第 1 天只有 1 个 IP）
    first = dates[0]
    hover_point(drv, 0)
    time.sleep(0.5)
    s = list_state(drv)
    check("4 切换到另一天", s["title"].startswith(first), f"实际 {s['title']}")
    check("4 行数正确", len(s["rows"]) == 1, f"实际 {len(s['rows'])} 行")
    drv.save_screenshot(str(Path(shots_dir) / "4_hover_another.png"))

    return all(cond for _, cond, _ in checks)


def main():
    global BASE_URL
    ap = argparse.ArgumentParser(description="验证管理后台下载明细列表")
    ap.add_argument("--browser", choices=["edge", "chrome"], default="edge")
    ap.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", "admin123"))
    ap.add_argument("--no-seed", action="store_true", help="不插入测试数据（要求服务已在运行）")
    ap.add_argument("--base-url", default=BASE_URL)
    args = ap.parse_args()
    BASE_URL = args.base_url

    shots_dir = Path(tempfile.mkdtemp(prefix="cad_verify_"))
    log(f"截图目录: {shots_dir}")

    proc = None
    seed = None
    drv = None
    try:
        if not server_up():
            log("服务未运行，启动 python main.py ...")
            proc = subprocess.Popen([sys.executable, "main.py"], cwd=str(ROOT),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(60):
                if server_up():
                    break
                time.sleep(1)
            if not server_up():
                raise RuntimeError("服务启动失败（60s 超时）")
            log("服务已就绪")
        else:
            log("使用已运行的服务")

        if args.no_seed:
            log("跳过测试数据插入")
        else:
            seed = seed_data()

        drv = make_driver(args.browser)
        log(f"浏览器已启动: {args.browser}")
        login(drv, args.password)
        log("登录成功")
        run_checks(drv, seed, shots_dir)
        log("ALL CHECKS PASSED")
    except AssertionError as e:
        log("FAILED: " + str(e))
        sys.exit(1)
    except Exception as e:
        log("ERROR: " + repr(e))
        sys.exit(2)
    finally:
        if drv:
            drv.quit()
        if seed is not None:
            cleanup_data()
        if proc:
            stop_process_tree(proc)


if __name__ == "__main__":
    main()
