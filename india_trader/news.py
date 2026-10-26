"""News, exchange announcements, results calendar and FII/DII flows.

Sources (all free, no keys):
  - NSE website APIs: corporate announcements, board meetings (results) calendar, FII/DII.
    NSE sometimes blocks automated requests; every function returns None/empty when it does,
    and the morning message says which sources were unavailable.
  - Google News RSS: recent headlines per company.

Headlines are tagged positive / negative with simple keyword rules. That is crude: it flags
items to read, it does not understand them. Always read the story before acting on it.

    python -m india_trader.news CYIENT TECHM   # print what each source returns (for testing)
"""
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

NEGATIVE = [
    r"fraud", r"raids?", r"probe", r"sebi (order|bans?|penalty|notice)", r"penalt(y|ies)",
    r"resign(s|ed|ation)?", r"downgrades?", r"defaults?", r"insolvency", r"bankrupt\w*",
    r"pledge[sd]?", r"plunges?", r"slumps?", r"crash(es)?", r"net loss", r"loss widens",
    r"misses (estimates|expectations)", r"profit (falls|drops|declines|slumps)", r"fire at",
    r"shut ?down", r"recalls?", r"lawsuit", r"tax demand", r"show cause", r"warning letter",
    r"import alert", r"promoters? sells?", r"stake sale", r"offer for sale",
]
POSITIVE = [
    r"(bags|wins|secures|receives|bagged|won|secured|received) (an? )?(\w+ )?(order|contract)s?",
    r"order (win|inflow)s?", r"buy ?back", r"bonus (issue|shares)", r"upgrades?",
    r"target (price )?(raised|hiked)", r"record (profit|revenue|high)",
    r"profit (jumps|rises|surges|soars|grows)", r"beats (estimates|expectations)",
    r"promoters? (buys|hikes stake|increases stake)", r"usfda approval", r"approval from usfda",
    r"stock split", r"special dividend",
]
_NEG = re.compile(r"\b(" + "|".join(NEGATIVE) + r")\b", re.I)
_POS = re.compile(r"\b(" + "|".join(POSITIVE) + r")\b", re.I)


def classify(text: str) -> str:
    """'negative', 'positive' or 'neutral'. Negative wins when both match."""
    if _NEG.search(text or ""):
        return "negative"
    if _POS.search(text or ""):
        return "positive"
    return "neutral"


def short_name(company: str) -> str:
    """'Cyient Ltd.' -> 'Cyient', 'Tata Consultancy Services Ltd.' -> 'Tata Consultancy Services'"""
    name = re.sub(r"\b(Ltd|Limited|Ltd\.)\.?$", "", company.strip(), flags=re.I).strip(" .,")
    return re.sub(r"\s*\(.*?\)\s*", " ", name).strip()


def mentions(title: str, company: str) -> bool:
    """True when the headline names the company itself, not just a similar word."""
    name = short_name(company).lower()
    name = re.sub(r"\b(india|limited|ltd|corporation|corp|company|co)\b\.?", "", name).strip(" .,&")
    return bool(name) and re.search(r"\b" + re.escape(name) + r"\b", title.lower()) is not None


# --- Google News -----------------------------------------------------------------

def parse_rss(xml_text: str, since: datetime) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if published.replace(tzinfo=None) < since:
            continue
        items.append({"title": title, "source": source or "Google News",
                      "link": (it.findtext("link") or "").strip(),
                      "date": published.date().isoformat(), "kind": "news",
                      "sentiment": classify(title)})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items


def google_news(company: str, days: int = 3, limit: int = 5) -> list[dict] | None:
    q = quote_plus(f'"{short_name(company)}" (share OR shares OR stock) when:{days}d')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}, timeout=20)
        if not r.ok:
            return None
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        return [i for i in parse_rss(r.text, since) if mentions(i["title"], company)][:limit]
    except (requests.RequestException, ET.ParseError):
        return None


# --- NSE -------------------------------------------------------------------------

class NSE:
    """Small client for nseindia.com's JSON APIs (needs the site's cookies first)."""

    BASE = "https://www.nseindia.com"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(BROWSER_HEADERS)
        self.ok = False
        try:
            r = self.s.get(self.BASE, timeout=15, headers={"Accept": "text/html"})
            self.ok = r.ok
        except requests.RequestException:
            self.ok = False

    def get(self, path: str, params: dict | None = None):
        if not self.ok:
            return None
        for attempt in range(2):
            try:
                r = self.s.get(self.BASE + path, params=params, timeout=20)
                if r.ok and r.headers.get("content-type", "").startswith("application/json"):
                    return r.json()
                if r.status_code in (401, 403):
                    # Cookies expired: fetch the home page again, then retry once.
                    self.s.get(self.BASE, timeout=15, headers={"Accept": "text/html"})
            except (requests.RequestException, ValueError):
                pass
            time.sleep(1 + attempt)
        return None

    def announcements(self, symbol: str, days: int = 3) -> list[dict] | None:
        to = date.today()
        frm = to - timedelta(days=days)
        data = self.get("/api/corporate-announcements", {
            "index": "equities", "symbol": symbol,
            "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")})
        if data is None:
            return None
        rows = data.get("data", data) if isinstance(data, dict) else data
        return [parse_announcement(a) for a in rows if isinstance(a, dict)]

    def board_meetings(self) -> dict[str, list[dict]] | None:
        """Upcoming board meetings (results etc.) for all companies: {symbol: [{date, purpose}]}."""
        data = self.get("/api/event-calendar", {"index": "equities"})
        if data is None:
            return None
        return parse_event_calendar(data)

    def fii_dii(self) -> dict | None:
        data = self.get("/api/fiidiiTradeReact")
        return parse_fii_dii(data) if data else None


def _nse_date(text: str) -> str | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_announcement(a: dict) -> dict:
    subject = (a.get("desc") or "").strip()
    detail = re.sub(r"\s+", " ", (a.get("attchmntText") or "")).strip()
    title = f"{subject}: {detail}" if detail and detail.lower() != subject.lower() else subject
    if len(title) > 160:
        title = title[:157].rsplit(" ", 1)[0] + "…"
    return {"title": title, "source": "NSE filing", "link": a.get("attchmntFile") or "",
            "date": _nse_date(a.get("an_dt") or a.get("sort_date") or "") or "",
            "kind": "filing", "sentiment": classify(f"{subject} {detail}")}


def parse_event_calendar(data) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    rows = data.get("data", []) if isinstance(data, dict) else data
    for e in rows or []:
        sym = (e.get("symbol") or "").strip()
        d = _nse_date(e.get("date") or e.get("bm_date") or "")
        if not sym or not d:
            continue
        purpose = (e.get("purpose") or e.get("bm_purpose") or "").strip()
        out.setdefault(sym, []).append({"date": d, "purpose": purpose,
                                        "results": "result" in purpose.lower()})
    return out


def parse_fii_dii(data) -> dict | None:
    out = {}
    for row in data if isinstance(data, list) else []:
        cat = (row.get("category") or "").upper()
        try:
            net = float(str(row.get("netValue")).replace(",", ""))
        except ValueError:
            continue
        key = "fii" if "FII" in cat or "FPI" in cat else "dii" if "DII" in cat else None
        if key:
            out[key] = net
            out["date"] = _nse_date(row.get("date") or "") or row.get("date")
    return out if "fii" in out or "dii" in out else None


def upcoming(meetings: dict | None, symbol: str, within_days: int, today: date) -> list[dict]:
    if not meetings:
        return []
    end = today + timedelta(days=within_days)
    return [m for m in meetings.get(symbol, [])
            if today.isoformat() <= m["date"] <= end.isoformat()]


def vix_label(v: float) -> str:
    if v < 13:
        return "calm"
    if v < 18:
        return "normal"
    if v < 24:
        return "nervous"
    return "fearful"


if __name__ == "__main__":
    import json
    nse = NSE()
    print(f"NSE site reachable: {nse.ok}")
    for path, params in [("/api/fiidiiTradeReact", None), ("/api/event-calendar", {"index": "equities"}),
                         ("/api/corporate-announcements", {"index": "equities", "symbol": (sys.argv[1:] or ["TCS"])[0]})]:
        raw = nse.get(path, params)
        first = (raw.get("data") or [None])[0] if isinstance(raw, dict) else (raw or [None])[0]
        print(f"RAW {path}: type={type(raw).__name__} first={json.dumps(first)[:600]}")
    fd = nse.fii_dii()
    print(f"FII/DII: {fd}")
    bm = nse.board_meetings()
    print(f"Board meetings calendar: {'unavailable' if bm is None else f'{len(bm)} companies'}")
    for sym in sys.argv[1:]:
        print(f"\n== {sym}")
        print("  upcoming:", (bm or {}).get(sym))
        ann = nse.announcements(sym, days=7)
        print(f"  NSE filings: {'unavailable' if ann is None else len(ann)}")
        for a in (ann or [])[:3]:
            print("   ", a["date"], a["sentiment"], a["title"][:110])
        gn = google_news(sym)
        print(f"  Google News: {'unavailable' if gn is None else len(gn)}")
        for n in (gn or [])[:3]:
            print("   ", n["date"], n["sentiment"], n["title"][:110], "|", n["source"])
