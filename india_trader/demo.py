"""Made-up prices for trying the app without internet. Never use these for trading."""
from datetime import date, timedelta

import numpy as np
import pandas as pd


def trading_days(end: date, n: int) -> list[date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[::-1]


def fake_series(seed: int, end: date, n: int = 300, trend: float = 0.0,
                breakout: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 200 + rng.random() * 800
    rets = rng.normal(trend, 0.015, n)
    close = price * np.exp(np.cumsum(rets))
    vol = rng.integers(400_000, 1_500_000, n).astype(float)
    if breakout:
        close[-1] = close[-21:-1].max() * 1.02
        vol[-1] = vol[-21:-1].mean() * 2.5
    high = close * (1 + rng.random(n) * 0.012)
    low = close * (1 - rng.random(n) * 0.012)
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.003, n))
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol},
                        index=trading_days(end, n))


def fake_prices(symbols: list[str], end: date) -> dict[str, pd.DataFrame]:
    out = {}
    for i, sym in enumerate(symbols):
        strong = i % 9 == 0
        out[sym] = fake_series(i, end, trend=0.0025 if strong else 0.0003, breakout=strong)
    out["^NSEI"] = fake_series(99999, end, trend=0.0008)
    return out
