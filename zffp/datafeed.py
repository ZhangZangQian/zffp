"""行情数据源：腾讯前复权日线（主）、新浪（备）、东方财富股票列表、CSV 导入。

只依赖 requests，失败自动降级，数据落本地 SQLite，之后所有复盘/统计都离线完成。
"""
from __future__ import annotations

import csv
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests

from . import db
from .strategy import Bar, board_of

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
TIMEOUT = 15

_tls = threading.local()


def _headers_for(url: str) -> dict:
    """不同行情站对 Referer 校验不同，按来源设置，否则会被静默拒绝。"""
    if "eastmoney" in url:
        return {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    if "gtimg" in url:
        return {"User-Agent": UA, "Referer": "https://gu.qq.com/"}
    return {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}


def _session() -> requests.Session:
    """每线程一个 Session：复用连接，绕开代理偶发断连。"""
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _tls.s = s
    return s


def _get(url: str, *, timeout: int = TIMEOUT, retries: int = 3) -> requests.Response:
    last: Exception | None = None
    for i in range(retries):
        try:
            return _session().get(url, headers=_headers_for(url), timeout=timeout)
        except Exception as e:          # 代理偶发 RemoteDisconnected
            last = e
            time.sleep(0.4 * (i + 1) + random.random() * 0.3)
    raise last  # type: ignore[misc]


def to_symbol(code: str) -> str:
    code = code[-6:]
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "2", "3")):
        return "sz" + code
    return "bj" + code


# --------------------------------------------------------------------------
# 单只股票日线
# --------------------------------------------------------------------------
def fetch_tencent(code: str, bars: int = 180) -> list[Bar]:
    """腾讯前复权日线。返回的数组是 [日期, 开, 收, 高, 低, 量(手)]。"""
    return fetch_tencent_symbol(to_symbol(code), bars)


def fetch_tencent_symbol(sym: str, bars: int = 180) -> list[Bar]:
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=max(bars * 2, 120))).strftime("%Y-%m-%d")
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,{start},{end},{bars},qfq")
    r = _get(url)
    payload = r.json()
    node = (payload.get("data") or {}).get(sym) or {}
    arr = node.get("qfqday") or node.get("day") or []
    out: list[Bar] = []
    for it in arr:
        if len(it) < 6:
            continue
        try:
            out.append(Bar(it[0], float(it[1]), float(it[3]), float(it[4]),
                           float(it[2]), float(it[5])))
        except (TypeError, ValueError):
            continue
    return out


def fetch_sina(code: str, bars: int = 180) -> list[Bar]:
    """新浪日线（不复权，备用）。成交量单位是股，统一换算成手。"""
    sym = to_symbol(code)
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen={bars}")
    r = _get(url)
    arr = r.json()
    out: list[Bar] = []
    for it in arr or []:
        try:
            out.append(Bar(it["day"][:10], float(it["open"]), float(it["high"]),
                           float(it["low"]), float(it["close"]),
                           float(it["volume"]) / 100.0))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_kline(code: str, bars: int = 180) -> tuple[list[Bar], str]:
    """主源失败自动降级，返回 (bars, source)。"""
    for fn, tag in ((fetch_tencent, "tencent"), (fetch_sina, "sina")):
        for attempt in range(2):
            try:
                data = fn(code, bars)
                if data:
                    return data, tag
                break
            except Exception:
                if attempt == 0:
                    time.sleep(0.4 + random.random() * 0.4)
    return [], "none"


def latest_market_date() -> str | None:
    """用上证指数判断最新交易日。"""
    try:
        bars = fetch_tencent_symbol("sh000001", 20)
        if bars:
            return bars[-1].date
    except Exception:
        pass
    return db.latest_trade_date()


# --------------------------------------------------------------------------
# 股票列表
# --------------------------------------------------------------------------
_FS = {
    "main": "m:0+t:6,m:1+t:2",
    "chinext": "m:0+t:80",
    "star": "m:1+t:23",
    "bj": "m:0+t:81+s:2048",
}


def fetch_universe(boards: list[str] | None = None) -> list[tuple[str, str]]:
    """全市场股票列表。新浪为主（稳定），东方财富为备。"""
    boards = boards or ["main", "chinext"]
    for fn in (_universe_sina, _universe_em):
        try:
            out = fn(boards)
        except Exception:
            continue
        if out:
            return out
    raise RuntimeError("股票列表拉取失败（网络或代理异常），请重试")


def _universe_sina(boards: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    base = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=hs_a")
    for page in range(1, 70):
        r = _get(base.format(page=page))
        txt = (r.text or "").strip()
        if not txt.startswith("["):
            break
        rows = r.json()
        if not rows:
            break
        for d in rows:
            code = str(d.get("code", "")).strip()
            name = str(d.get("name", "")).strip()
            if len(code) != 6 or code in seen:
                continue
            if board_of(code) not in boards:
                continue
            seen.add(code)
            out.append((code, name))
        if len(rows) < 100:
            break
        time.sleep(0.15)
    return out


def _universe_em(boards: list[str]) -> list[tuple[str, str]]:
    fs = ",".join(_FS[b] for b in boards if b in _FS)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pn in range(1, 80):
        url = ("https://push2.eastmoney.com/api/qt/clist/get"
               f"?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&fs={fs}"
               "&fields=f12,f13,f14")
        payload = _get(url).json()
        data = payload.get("data") or {}
        diff = data.get("diff") or []
        if not diff:
            break
        for d in diff:
            code = str(d.get("f12", "")).strip()
            name = str(d.get("f14", "")).strip()
            if len(code) != 6 or code in seen:
                continue
            seen.add(code)
            out.append((code, name))
        if len(diff) < 100:
            break
        time.sleep(0.12)
    return out


# --------------------------------------------------------------------------
# 增量更新
# --------------------------------------------------------------------------
def update_market(
    progress: dict,
    *,
    mode: str = "incremental",
    limit: int | None = None,
    workers: int = 8,
    boards: list[str] | None = None,
) -> dict:
    """拉取/增量更新全市场日线。progress 是共享字典，供前端轮询。"""

    def note(msg: str, **kw):
        progress.update({"message": msg, **kw})

    started = time.time()
    note("同步股票列表…")
    have = db.q1("SELECT COUNT(*) AS n FROM stocks")["n"]
    if have == 0 or mode == "full":
        uni = fetch_universe(boards)
        now = datetime.now().isoformat(timespec="seconds")
        for code, name in uni:
            db.upsert_stock(code, name, board_of(code), now)
        note(f"股票列表 {len(uni)} 只")
    else:
        uni = [(r["code"], r["name"]) for r in db.q("SELECT code,name FROM stocks")]

    latest = latest_market_date()
    progress["trade_date"] = latest
    note(f"最新交易日 {latest}，准备行情…")

    last_map = {r["code"]: r["d"] for r in
                db.q("SELECT code, MAX(date) AS d FROM bars GROUP BY code")}
    todo = [c for c, _ in uni]
    if mode != "full" and latest:
        todo = [c for c in todo if (last_map.get(c) or "") < latest]
    if limit:
        todo = todo[:limit]

    progress.update({"total": len(todo), "done": 0, "ok": 0, "fail": 0})
    note(f"需更新 {len(todo)} 只")

    if not todo:
        progress.update({"elapsed": round(time.time() - started, 1),
                         "message": "行情已是最新"})
        return progress

    lock_errors: list[str] = []

    def work(code: str):
        bars, src = fetch_kline(code, 260)
        return code, bars, src

    # 工作线程只负责抓取；落盘在主线程批量提交，避免 SQLite 写锁互相阻塞
    buffer: list[tuple] = []
    FLUSH = 4000

    def flush():
        if buffer:
            db.xm(db.BARS_UPSERT, buffer)
            buffer.clear()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(work, c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                code, bars, src = fut.result()
            except Exception as e:  # pragma: no cover - 网络异常兜底
                progress["fail"] += 1
                if len(lock_errors) < 5:
                    lock_errors.append(f"{futs[fut]}: {e}")
            else:
                if bars:
                    progress["ok"] += 1
                    buffer.extend([(code, b.date, b.open, b.high, b.low, b.close, b.volume)
                                   for b in bars])
                    if len(buffer) >= FLUSH:
                        flush()
                else:
                    progress["fail"] += 1
                    if len(lock_errors) < 5:
                        lock_errors.append(f"{code}: no data")
            progress["done"] = i
            if i % 20 == 0 or i == len(todo):
                note(f"行情 {i}/{len(todo)}")
    flush()

    progress["elapsed"] = round(time.time() - started, 1)
    progress["errors"] = lock_errors
    note(f"完成：成功 {progress['ok']}，失败 {progress['fail']}，"
         f"用时 {progress['elapsed']}s")
    db.set_setting("last_update", {"at": datetime.now().isoformat(timespec="seconds"),
                                   "trade_date": latest, "ok": progress["ok"],
                                   "fail": progress["fail"]})
    return progress


# --------------------------------------------------------------------------
# CSV 导入（自定义数据 / 通达信导出 / 其他源）
# --------------------------------------------------------------------------
_ALIASES = {
    "date": ("date", "日期", "trade_date", "时间"),
    "open": ("open", "开盘", "开盘价"),
    "high": ("high", "最高", "最高价"),
    "low": ("low", "最低", "最低价"),
    "close": ("close", "收盘", "收盘价"),
    "volume": ("volume", "vol", "成交量", "量"),
}


def _pick(header: list[str], key: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for alias in _ALIASES[key]:
        if alias.lower() in low:
            return low.index(alias.lower())
    return None


def import_csv(path: str | Path, code: str | None = None) -> int:
    """导入单个 CSV：列名支持中英文；文件名形如 600000.csv 时自动取代码。"""
    path = Path(path)
    code = code or path.stem[-6:]
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return 0
    header = rows[0]
    idx = {k: _pick(header, k) for k in _ALIASES}
    if any(v is None for v in idx.values()):
        # 无表头：按 date,open,high,low,close,volume 顺序
        body = rows
        idx = {"date": 0, "open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
    else:
        body = rows[1:]

    out: list[Bar] = []
    for r in body:
        try:
            d = str(r[idx["date"]]).strip().replace("/", "-")
            if len(d) == 8 and d.isdigit():
                d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            out.append(Bar(d[:10], float(r[idx["open"]]), float(r[idx["high"]]),
                           float(r[idx["low"]]), float(r[idx["close"]]),
                           float(r[idx["volume"]])))
        except (IndexError, TypeError, ValueError):
            continue
    out.sort(key=lambda b: b.date)
    db.save_bars(code, out)
    if db.q1("SELECT 1 FROM stocks WHERE code=?", (code,)) is None:
        db.upsert_stock(code, code, board_of(code), datetime.now().isoformat(timespec="seconds"))
    return len(out)
