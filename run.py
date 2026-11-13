"""启动入口。

    python run.py            启动网页（默认 http://127.0.0.1:8777）
    python run.py update     更新行情（增量）
    python run.py update -f  全量重拉
    python run.py scan       只跑扫描
    python run.py doctor     自检
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from zffp import db  # noqa: E402


def cmd_update(args):
    from zffp.datafeed import update_market

    progress: dict = {}
    print("开始更新行情…")
    update_market(progress, mode=args.mode, limit=args.limit, workers=args.workers)
    print(progress.get("message", "完成"))
    return 0


def cmd_scan(args):
    from zffp import scanner

    res = scanner.scan(lookback=args.lookback)
    print(f"扫描 {res['scanned']} 只，命中 {res['signals']} 个形态")
    print(f"状态分布：{res['counts']}")
    print(f"最新交易日：{res['trade_date']}，可下手信号：{res['today_actionable']}")
    return 0


def cmd_doctor(args):
    from zffp import analytics, discipline

    db.init()
    s, r = db.load_params()
    have, total = db.bar_coverage()
    print(f"数据库：{db.DB_PATH}")
    print(f"行情覆盖：{have}/{total} 只")
    print(f"最新交易日：{db.latest_trade_date()}")
    print(f"信号数：{db.q1('SELECT COUNT(*) AS n FROM signals')['n']}")
    print(f"计划：{db.q1('SELECT COUNT(*) AS n FROM plans')['n']}，"
          f"交易：{db.q1('SELECT COUNT(*) AS n FROM trades')['n']}，"
          f"复盘：{db.q1('SELECT COUNT(*) AS n FROM reviews')['n']}")
    snap = discipline.snapshot()
    print(f"权益：{snap['equity']:.0f}，可用：{snap['available']:.0f}，"
          f"持仓：{snap['open_count']}，连亏：{snap['loss_streak']}")
    print(f"战法体检：{analytics.strategy_check()}")
    return 0


def cmd_serve(args):
    import threading
    import webbrowser

    import uvicorn

    db.init()
    s, r = db.load_params()
    db.save_params(s, r)
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  巨阴突破战法 · 复盘纪律系统")
    print(f"  {url}\n")
    if not args.no_browser:
        try:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    uvicorn.run("zffp.api:app", host=args.host, port=args.port, log_level="warning")
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("command", nargs="?", default="serve",
                    choices=["serve", "update", "scan", "doctor"])
    ap.add_argument("-f", "--full", action="store_true", help="全量重拉行情")
    ap.add_argument("--limit", type=int, default=None, help="只更新前 N 只（调试用）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = ap.parse_args()
    args.mode = "full" if args.full else "incremental"

    db.init()
    handler = {"serve": cmd_serve, "update": cmd_update,
               "scan": cmd_scan, "doctor": cmd_doctor}[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
