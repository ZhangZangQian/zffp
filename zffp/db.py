"""SQLite 存储层。

单文件数据库，免安装。行情、信号、计划、交易、复盘全部落盘，
每一次"该做没做 / 不该做却做"都有痕迹。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_RISK, DEFAULT_STRATEGY, merge
from .strategy import Bar

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("ZFFP_DB", DATA_DIR / "zffp.db"))

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    code       TEXT PRIMARY KEY,
    name       TEXT,
    board      TEXT,
    is_st      INTEGER DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS bars (
    code   TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_bars_date ON bars(date);

CREATE TABLE IF NOT EXISTS signals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    code               TEXT NOT NULL,
    name               TEXT,
    board              TEXT,
    t_date             TEXT NOT NULL,
    t_open             REAL, t_close REAL,
    t1_date            TEXT,
    t1_open            REAL, t1_high REAL, t1_low REAL, t1_close REAL,
    t1_gap             REAL, t1_body REAL,
    breakout_date      TEXT, breakout_close REAL, breakout_vol_ratio REAL,
    buy_date           TEXT, ref_price REAL,
    state              TEXT,
    days_left          INTEGER DEFAULT 0,
    updated_at         TEXT,
    UNIQUE (code, t_date)
);
CREATE INDEX IF NOT EXISTS idx_signals_state ON signals(state);
CREATE INDEX IF NOT EXISTS idx_signals_buy ON signals(buy_date);

CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER,
    code            TEXT NOT NULL,
    name            TEXT,
    plan_date       TEXT,
    buy_date        TEXT,
    buy_price_limit REAL,
    ref_price       REAL,
    shares          INTEGER,
    amount          REAL,
    position_pct    REAL,
    stop_price      REAL,
    target_price    REAL,
    risk_amount     REAL,
    rr              REAL,
    thesis          TEXT,
    emotion         TEXT,
    status          TEXT DEFAULT 'locked',
    gate_json       TEXT,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
CREATE INDEX IF NOT EXISTS idx_plans_buy ON plans(buy_date);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER,
    signal_id     INTEGER,
    code          TEXT NOT NULL,
    name          TEXT,
    buy_date      TEXT,
    buy_time      TEXT,
    buy_price     REAL,
    shares        INTEGER,
    amount        REAL,
    stop_price    REAL,
    target_price  REAL,
    sell_date     TEXT,
    sell_price    REAL,
    pnl           REAL,
    pnl_pct       REAL,
    exit_reason   TEXT,
    hold_days     INTEGER,
    status        TEXT DEFAULT 'open',
    compliance    TEXT,
    violations    TEXT,
    note          TEXT,
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_buy ON trades(buy_date);

CREATE TABLE IF NOT EXISTS reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id       INTEGER,
    review_date    TEXT,
    followed_plan  TEXT,
    exit_quality   INTEGER,
    emotion        TEXT,
    mistake_tags   TEXT,
    lesson         TEXT,
    score          INTEGER,
    created_at     TEXT,
    UNIQUE (trade_id)
);

CREATE TABLE IF NOT EXISTS daily_log (
    date             TEXT PRIMARY KEY,
    checklist        TEXT,
    mood             TEXT,
    discipline_score INTEGER,
    note             TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    return c


def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    c.commit()


def q(sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()


def q1(sql: str, args: tuple | list = ()):
    return conn().execute(sql, args).fetchone()


def x(sql: str, args: tuple | list = ()) -> int:
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid or 0


def xm(sql: str, rows: list[tuple]) -> None:
    c = conn()
    c.executemany(sql, rows)
    c.commit()


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------
def get_setting(key: str, default=None):
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_setting(key: str, value) -> None:
    x("INSERT INTO settings(key,value) VALUES(?,?) "
      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value, ensure_ascii=False)))


def load_params() -> tuple[dict, dict]:
    strategy = merge(DEFAULT_STRATEGY, get_setting("strategy", {}))
    risk = merge(DEFAULT_RISK, get_setting("risk", {}))
    return strategy, risk


def save_params(strategy: dict, risk: dict) -> None:
    set_setting("strategy", merge(DEFAULT_STRATEGY, strategy))
    set_setting("risk", merge(DEFAULT_RISK, risk))


# --------------------------------------------------------------------------
# 行情
# --------------------------------------------------------------------------
BARS_UPSERT = (
    "INSERT INTO bars(code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?) "
    "ON CONFLICT(code,date) DO UPDATE SET open=excluded.open, high=excluded.high, "
    "low=excluded.low, close=excluded.close, volume=excluded.volume"
)


def save_bars(code: str, rows: list[Bar]) -> int:
    if not rows:
        return 0
    xm(BARS_UPSERT, [(code, b.date, b.open, b.high, b.low, b.close, b.volume) for b in rows])
    return len(rows)


def get_bars(code: str, limit: int = 200) -> list[Bar]:
    rows = q("SELECT date,open,high,low,close,volume FROM bars WHERE code=? "
             "ORDER BY date DESC LIMIT ?", (code, limit))
    return [Bar(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"])
            for r in reversed(rows)]


def latest_trade_date() -> str | None:
    row = q1("SELECT MAX(date) AS d FROM bars")
    return row["d"] if row and row["d"] else None


def is_intraday() -> bool:
    """盘中（未到 15:00）时，今日 K 线是不完整的。"""
    d = latest_trade_date()
    if not d:
        return False
    now = datetime.now()
    return d == now.strftime("%Y-%m-%d") and now.hour < 15


def completed_trade_date() -> str | None:
    """最近一个"已收盘"的交易日。盘中会自动回退到上一交易日。

    战法规则全部基于日线收盘价，用未收盘的 K 线判形态会自欺欺人。
    """
    d = latest_trade_date()
    if not d:
        return None
    if is_intraday():
        row = q1("SELECT MAX(date) AS d FROM bars WHERE date < ?", (d,))
        return (row["d"] if row else None) or d
    return d


def bar_coverage() -> tuple[int, int]:
    """(有行情的股票数, 总股票数)"""
    a = q1("SELECT COUNT(DISTINCT code) AS n FROM bars")["n"]
    b = q1("SELECT COUNT(*) AS n FROM stocks")["n"]
    return a, b


def upsert_stock(code: str, name: str, board: str, now: str) -> None:
    x("INSERT INTO stocks(code,name,board,is_st,updated_at) VALUES(?,?,?,?,?) "
      "ON CONFLICT(code) DO UPDATE SET name=excluded.name, board=excluded.board, "
      "is_st=excluded.is_st, updated_at=excluded.updated_at",
      (code, name, board, 1 if "ST" in (name or "").upper() else 0, now))


def code_name_map() -> dict[str, str]:
    return {r["code"]: r["name"] for r in q("SELECT code,name FROM stocks")}
