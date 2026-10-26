"""Where the data comes from: the Nifty 500 list, the list of all NSE stocks, and daily prices.

Prices: Yahoo Finance's free chart endpoint (end-of-day, NSE symbols end in ".NS").
It is unofficial and can change; if it breaks, swap fetch_daily() for a broker API.
"""
import csv
import io
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "data" / "nifty500.csv"
UNIVERSE_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
]
ALL_NSE_FILE = ROOT / "data" / "nse_equity.csv"
ALL_NSE_URLS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
]
SECTOR_FILE = ROOT / "data" / "sectors.csv"
SECTOR_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv",
]
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) trade-helper/1.0"}
NIFTY50 = "^NSEI"
INDIA_VIX = "^INDIAVIX"


def _parse_universe(text: str) -> list[dict]:
    stocks = []
    for row in csv.DictReader(io.StringIO(text)):
        symbol = (row.get("Symbol") or "").strip()
        series = (row.get("Series") or "").strip()
        # Skip REITs (series RR) and NSE's placeholder "DUMMY" entries.
        if not symbol or series == "RR" or symbol.startswith("DUMMY"):
            continue
        stocks.append({
            "symbol": symbol,
            "name": (row.get("Company Name") or symbol).strip(),
            "sector": (row.get("Industry") or "").strip(),
        })
    return stocks


def refresh_universe() -> bool:
    """Try to download the latest Nifty 500 list; keep the saved copy if that fails."""
    for url in UNIVERSE_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.ok and "Symbol" in r.text[:200] and len(_parse_universe(r.text)) > 400:
                keep = ["Company Name", "Industry", "Symbol", "Series"]
                rows = list(csv.DictReader(io.StringIO(r.text)))
                with UNIVERSE_FILE.open("w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=keep, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rows)
                return True
        except requests.RequestException:
            continue
    return False


def load_universe() -> list[dict]:
    return _parse_universe(UNIVERSE_FILE.read_text())


def _download(urls: list[str], must_contain: str) -> str | None:
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30)
            if r.ok and must_contain in r.text[:300]:
                return r.text
        except requests.RequestException:
            continue
    return None


def parse_all_nse(text: str) -> list[dict]:
    """NSE's EQUITY_L.csv -> main-board stocks in the normal 'EQ' series."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip().upper(): (v or "").strip() for k, v in row.items() if k}
        if row.get("SERIES") == "EQ" and row.get("SYMBOL"):
            out.append({"symbol": row["SYMBOL"], "name": row.get("NAME OF COMPANY") or row["SYMBOL"]})
    return out


def refresh_all_nse() -> bool:
    """Download the latest lists of all NSE stocks and their sectors; keep saved copies if that fails."""
    ok = False
    text = _download(ALL_NSE_URLS, "SYMBOL")
    if text and len(parse_all_nse(text)) > 1000:
        with ALL_NSE_FILE.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["symbol", "name"])
            w.writeheader()
            w.writerows(parse_all_nse(text))
        ok = True
    text = _download(SECTOR_URLS, "Symbol")
    if text and len(_parse_universe(text)) > 600:
        with SECTOR_FILE.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Symbol", "Industry"])
            w.writerows((x["symbol"], x["sector"]) for x in _parse_universe(text))
    return ok


def load_all_nse() -> list[dict] | None:
    """All NSE main-board stocks, with sectors where known (else ''). None if never downloaded."""
    if not ALL_NSE_FILE.exists():
        return None
    sectors = {s["symbol"]: s["sector"] for s in load_universe()}
    if SECTOR_FILE.exists():
        for row in csv.DictReader(SECTOR_FILE.open()):
            sectors.setdefault(row["Symbol"], row["Industry"])
    rows = list(csv.DictReader(ALL_NSE_FILE.open()))
    return [{"symbol": r["symbol"], "name": r["name"], "sector": sectors.get(r["symbol"], "")}
            for r in rows if not r["symbol"].startswith("DUMMY")]


def yahoo_symbol(nse_symbol: str) -> str:
    return nse_symbol if nse_symbol.startswith("^") else f"{nse_symbol}.NS"


def fetch_daily(nse_symbol: str, range_: str = "2y", session: requests.Session | None = None,
                retries: int = 2) -> pd.DataFrame | None:
    """Daily candles for one NSE symbol, oldest first, indexed by date. None if unavailable."""
    url = YAHOO_URL.format(symbol=quote(yahoo_symbol(nse_symbol)), range=range_)
    s = session or requests
    for attempt in range(retries + 1):
        try:
            r = s.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            result = r.json()["chart"]["result"][0]
            q = result["indicators"]["quote"][0]
            df = pd.DataFrame({
                "open": q["open"], "high": q["high"], "low": q["low"],
                "close": q["close"], "volume": q["volume"],
            }, index=pd.to_datetime(result["timestamp"], unit="s", utc=True)
                .tz_convert("Asia/Kolkata").date)
            df = df.dropna()
            df = df[~df.index.duplicated(keep="last")]
            return df if len(df) else None
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
            time.sleep(1 + attempt)
    return None


def fetch_many(symbols: list[str], pause: float = 0.25, workers: int = 4, log=print) -> dict[str, pd.DataFrame]:
    """Daily candles for many symbols, a few at a time to stay polite to the server."""
    out = {}

    def one(sym):
        df = fetch_daily(sym)
        time.sleep(pause)
        return sym, df

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (sym, df) in enumerate(pool.map(one, symbols), 1):
            if df is not None:
                out[sym] = df
            if i % 250 == 0:
                log(f"  fetched {i}/{len(symbols)} ({len(out)} ok)")
    return out


def fetch_quote(nse_symbol: str, session: requests.Session | None = None) -> dict | None:
    """Latest price plus today's high and low so far (Yahoo; may lag the exchange by a few minutes)."""
    url = YAHOO_URL.format(symbol=quote(yahoo_symbol(nse_symbol)), range="1d").replace("interval=1d", "interval=5m")
    s = session or requests
    for attempt in range(3):
        try:
            r = s.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            meta = r.json()["chart"]["result"][0]["meta"]
            return {
                "last": float(meta["regularMarketPrice"]),
                "high": float(meta["regularMarketDayHigh"]),
                "low": float(meta["regularMarketDayLow"]),
                "prev_close": float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0),
                "time": pd.Timestamp(meta["regularMarketTime"], unit="s", tz="UTC").tz_convert("Asia/Kolkata"),
            }
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
            time.sleep(1 + attempt)
    return None
