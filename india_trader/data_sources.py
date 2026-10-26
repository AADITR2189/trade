"""Where the data comes from: the Nifty 500 list and daily prices.

Prices: Yahoo Finance's free chart endpoint (end-of-day, NSE symbols end in ".NS").
It is unofficial and can change; if it breaks, swap fetch_daily() for a broker API.
"""
import csv
import io
import time
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
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) trade-helper/1.0"}
NIFTY50 = "^NSEI"


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


def fetch_many(symbols: list[str], pause: float = 0.25, log=print) -> dict[str, pd.DataFrame]:
    out = {}
    with requests.Session() as session:
        for i, sym in enumerate(symbols, 1):
            df = fetch_daily(sym, session=session)
            if df is not None:
                out[sym] = df
            if i % 50 == 0:
                log(f"  fetched {i}/{len(symbols)} ({len(out)} ok)")
            time.sleep(pause)
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
