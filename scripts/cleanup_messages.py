#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
留言批量清理脚本（服务器端/本地直接运行）

用于清理已被垃圾留言淹没的 messages 表，支持：
  - 删除指定 IP 的全部留言（--ip）
  - 按状态 / 关键词 / 日期批量删除
  - 清空全部留言
  - 可选：删除时把留言集中的 IP 自动加入黑名单（--block-ips）

用法示例（在项目根目录执行）:
    python scripts/cleanup_messages.py --dry-run --ip 1.2.3.4
    python scripts/cleanup_messages.py --ip 1.2.3.4 --block-ips          # 删除指定IP留言并拉黑
    python scripts/cleanup_messages.py --status rejected                 # 删除全部已拒绝留言
    python scripts/cleanup_messages.py --keyword 加微信                  # 删除含关键词的留言
    python scripts/cleanup_messages.py --days 30                         # 删除30天前的留言
    python scripts/cleanup_messages.py --all --block-ips                 # 清空全部并拉黑高发IP

提示：生产环境先备份数据库再执行：
    cp data/database.db data/database.db.bak.$(date +%Y%m%d)
"""
import argparse
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def build_where(args):
    conds, params = [], []
    if args.status:
        conds.append("status=?")
        params.append(args.status)
    if args.keyword:
        conds.append("(content LIKE ? OR author LIKE ?)")
        params += [f"%{args.keyword}%", f"%{args.keyword}%"]
    if args.ip:
        conds.append("ip_address LIKE ?")
        params.append(f"{args.ip}%")
    if args.days:
        conds.append("date(created_at) < date('now','localtime', ?)")
        params.append(f"-{args.days} days")
    if args.before:
        conds.append("date(created_at) < ?")
        params.append(args.before)
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    return where, params


def main():
    ap = argparse.ArgumentParser(description="清理 messages 表中的垃圾留言")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent / "data" / "database.db"),
                    help="SQLite 数据库路径（默认 data/database.db）")
    ap.add_argument("--ip", help="只处理该 IP（支持前缀匹配）的全部留言")
    ap.add_argument("--status", choices=["pending", "approved", "rejected"], help="只处理该状态的留言")
    ap.add_argument("--keyword", help="只处理内容或昵称包含该关键词的留言")
    ap.add_argument("--days", type=int, help="只处理 N 天前的留言")
    ap.add_argument("--before", help="只处理该日期(YYYY-MM-DD)之前的留言")
    ap.add_argument("--all", action="store_true", help="处理全部留言（与其它条件叠加）")
    ap.add_argument("--block-ips", action="store_true", help="删除时把留言数达到阈值的 IP 加入黑名单")
    ap.add_argument("--block-min", type=int, default=5, help="拉黑阈值：同一 IP 留言数达到该值才拉黑（默认5）")
    ap.add_argument("--dry-run", action="store_true", help="只预览将删除的数量，不实际执行")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[错误] 数据库不存在: {db_path}")
        sys.exit(1)

    where, params = build_where(args)
    if not where and not args.all:
        print("[错误] 请至少指定一个条件（--ip / --status / --keyword / --days / --before / --all）")
        print("       使用 --dry-run 可先预览。")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    total = conn.execute(f"SELECT COUNT(*) FROM messages {where}", params).fetchone()[0]
    print(f"[预览] 符合条件的留言: {total} 条")
    if not total:
        print("[提示] 没有需要处理的留言")
        conn.close()
        return

    # 按 IP 统计（供拉黑与展示）
    if where:
        ip_rows = conn.execute(
            f"""SELECT ip_address, COUNT(*) c FROM messages {where}
                GROUP BY ip_address ORDER BY c DESC LIMIT 20""", params
        ).fetchall()
    else:
        ip_rows = conn.execute(
            """SELECT ip_address, COUNT(*) c FROM messages
               GROUP BY ip_address ORDER BY c DESC LIMIT 20"""
        ).fetchall()
    print("[预览] 留言集中度 Top10：")
    for r in ip_rows[:10]:
        print(f"       {r['ip_address'] or '(空IP)'}: {r['c']} 条")

    if args.dry_run:
        conn.close()
        print("[dry-run] 未执行任何修改")
        return

    if not args.block_ips:
        sure = input(f"确认永久删除这 {total} 条留言？输入 yes 继续: ").strip().lower()
        if sure != "yes":
            print("已取消")
            conn.close()
            return

    blocked = []
    if args.block_ips:
        rows = conn.execute(
            f"""SELECT ip_address, COUNT(*) c FROM messages {where}
                GROUP BY ip_address HAVING c >= ?""", params + [args.block_min]
        ).fetchall()
        for r in rows:
            if not r["ip_address"]:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO blocked_ips (ip_address, reason) VALUES (?, ?)",
                (r["ip_address"], f"批量清理拉黑（{r['c']}条留言）"),
            )
            blocked.append((r["ip_address"], r["c"]))

    conn.execute(f"DELETE FROM messages {where}", params)
    conn.commit()
    conn.close()

    print(f"[完成] 已删除 {total} 条留言")
    if blocked:
        print(f"[完成] 已拉黑 {len(blocked)} 个 IP: " + ", ".join(f"{ip}({c}条)" for ip, c in blocked))
    print("[提示] 如果还有机器人持续刷留言，请部署最新代码（新留言默认待审核 + 蜜罐 + 频率限制 + 关键词过滤）")


if __name__ == "__main__":
    main()
