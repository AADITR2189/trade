"""Adds the news and events layer on top of the price scan.

For each candidate (best technical scores first):
  - results or a board meeting within avoid_results_within_days -> skipped (gap risk)
  - a negative headline or filing -> score minus news_penalty, and flagged
  - otherwise a positive one -> score plus news_bonus
Plus the market pulse: India VIX and FII/DII flows.
"""
from datetime import date

from . import news

# Exchange filings worth showing; routine ones (con-call links, trading window, newspaper
# copies, postal ballots...) are left out unless the keyword rules flag them.
IMPORTANT_FILINGS = ("outcome of board meeting", "financial result", "press release", "acquisition",
                     "credit rating", "resignation", "change in management", "buy back", "buyback",
                     "bonus", "split", "order", "contract", "award", "fund raising", "allotment",
                     "litigation", "dispute", "default", "fraud", "insolvency", "pledge",
                     "investor presentation", "agreement", "joint venture", "amalgamation")
MAX_CANDIDATES = 15


def _important(item: dict) -> bool:
    if item["kind"] != "filing" or item["sentiment"] != "neutral":
        return True
    t = item["title"].lower()
    return any(k in t for k in IMPORTANT_FILINGS)


def research(candidates: list[dict], cfg: dict, today: date, nse: "news.NSE",
             meetings: dict | None) -> tuple[list[dict], list[dict], list[str]]:
    """Returns (checked candidates with adjusted scores, skipped [{symbol, reason}], sources down)."""
    down = set()
    if meetings is None:
        down.add("NSE results calendar")
    checked, skipped = [], []
    for c in candidates[:MAX_CANDIDATES]:
        c = dict(c)
        soon = news.upcoming(meetings, c["symbol"], cfg["avoid_results_within_days"], today)
        if soon:
            m = soon[0]
            skipped.append({"symbol": c["symbol"],
                            "reason": f"{m['purpose'] or 'board meeting'} on {_d(m['date'])}"})
            continue
        later = news.upcoming(meetings, c["symbol"], 30, today)
        c["next_event"] = later[0] if later else None

        filings = nse.announcements(c["symbol"], days=cfg["news_days"])
        if filings is None:
            down.add("NSE filings")
        headlines = news.google_news(c["name"], days=cfg["news_days"])
        if headlines is None:
            down.add("Google News")
        items = [i for i in (filings or []) if _important(i)] + (headlines or [])
        sentiments = {i["sentiment"] for i in items}

        c["tech_score"] = c["score"]
        if "negative" in sentiments:
            c["news_adj"] = -cfg["news_penalty"]
        elif "positive" in sentiments:
            c["news_adj"] = cfg["news_bonus"]
        else:
            c["news_adj"] = 0
        c["score"] = round(max(0, min(100, c["score"] + c["news_adj"])), 1)
        order = {"negative": 0, "positive": 1, "neutral": 2}
        c["news"] = sorted(items, key=lambda i: (order[i["sentiment"]], i["kind"] != "filing"))[:2]
        checked.append(c)
    return checked, skipped, sorted(down)


def holding_warnings(active_rows: list[dict], meetings: dict | None, cfg: dict, today: date) -> list[str]:
    """Results coming up for picks you may already hold or are waiting to enter."""
    out = []
    for r in active_rows:
        soon = news.upcoming(meetings, r["symbol"], cfg["avoid_results_within_days"], today)
        if soon:
            m = soon[0]
            what = "you may hold it" if r["status"] in ("OPEN", "T1_HIT") else "entry still pending"
            out.append(f"📅 {r['symbol']}: {m['purpose'] or 'board meeting'} on {_d(m['date'])} ({what}). "
                       f"Prices can jump either way on results day; consider booking profit or skipping entry.")
    return out


def market_pulse(nse: "news.NSE", vix_df) -> dict:
    pulse = {"vix": None, "fii_dii": None}
    if vix_df is not None and len(vix_df) >= 2:
        v, prev = float(vix_df["close"].iloc[-1]), float(vix_df["close"].iloc[-2])
        pulse["vix"] = {"value": round(v, 2), "change_pct": round((v / prev - 1) * 100, 1),
                        "label": news.vix_label(v)}
    pulse["fii_dii"] = nse.fii_dii()
    return pulse


def _d(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d %b")
