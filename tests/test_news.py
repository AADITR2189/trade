from datetime import date, datetime

from india_trader import context, news
from india_trader.main import load_config
from india_trader.notify import build_message

CFG = load_config()
TODAY = date(2026, 10, 26)


def test_classify():
    assert news.classify("BEL bags order worth Rs 1,250 crore from Army") == "positive"
    assert news.classify("Tech Mahindra announces 1:1 bonus issue") == "positive"
    assert news.classify("SEBI order against promoters; CFO resigns") == "negative"
    assert news.classify("Order win, but CFO resigns") == "negative"
    assert news.classify("Bank stocks rise in early trade") == "neutral"  # 'ban' must not match 'bank'


def test_short_name():
    assert news.short_name("Cyient Ltd.") == "Cyient"
    assert news.short_name("Info Edge (India) Ltd.") == "Info Edge"


def test_parse_real_nse_shapes():
    fii = news.parse_fii_dii([
        {"buyValue": "19232.31", "category": "DII", "date": "23-Oct-2026", "netValue": "3235.36"},
        {"buyValue": "15000", "category": "FII/FPI", "date": "23-Oct-2026", "netValue": "887.65"}])
    assert fii == {"dii": 3235.36, "fii": 887.65, "date": "2026-10-23"}

    cal = news.parse_event_calendar([{"symbol": "ABDL", "purpose": "Financial Results",
                                      "bm_desc": "...", "date": "27-Oct-2026"}])
    assert cal == {"ABDL": [{"date": "2026-10-27", "purpose": "Financial Results", "results": True}]}

    a = news.parse_announcement({
        "an_dt": "23-Oct-2026 08:58:15", "desc": "Analysts/Institutional Investor Meet/Con. Call Updates",
        "attchmntText": "Cyient Limited has informed the Exchange about Link of Recording",
        "attchmntFile": "https://nsearchives.nseindia.com/x.pdf"})
    assert a["date"] == "2026-10-23" and a["kind"] == "filing" and a["sentiment"] == "neutral"
    assert not context._important(a)


def test_parse_google_rss():
    xml = """<rss><channel>
      <item><title>Cyient bags order from Airbus - The Economic Times</title><link>https://x/1</link>
        <pubDate>Mon, 26 Oct 2026 05:00:00 GMT</pubDate><source url="https://et">The Economic Times</source></item>
      <item><title>Old story - Mint</title><link>https://x/2</link>
        <pubDate>Mon, 05 Oct 2026 05:00:00 GMT</pubDate><source>Mint</source></item>
    </channel></rss>"""
    items = news.parse_rss(xml, since=datetime(2026, 10, 23))
    assert len(items) == 1
    assert items[0]["title"] == "Cyient bags order from Airbus" and items[0]["sentiment"] == "positive"


def test_upcoming_window():
    meetings = {"X": [{"date": "2026-10-28", "purpose": "Financial Results", "results": True}]}
    assert news.upcoming(meetings, "X", 5, TODAY)
    assert not news.upcoming(meetings, "X", 1, TODAY)
    assert not news.upcoming(None, "X", 5, TODAY)


class FakeNSE:
    def __init__(self, filings):
        self.filings = filings

    def announcements(self, symbol, days):
        return self.filings.get(symbol, [])

    def fii_dii(self):
        return {"fii": -1234.5, "dii": 2345.6, "date": "2026-10-23"}


def _cand(sym, score):
    return {"symbol": sym, "name": f"{sym} Ltd.", "sector": sym, "score": score}


def test_research_skips_results_and_adjusts_scores(monkeypatch):
    monkeypatch.setattr(news, "google_news", lambda name, days: [])
    bad = {"title": "Resignation: CFO resigns", "source": "NSE filing", "link": "", "date": "2026-10-24",
           "kind": "filing", "sentiment": "negative"}
    good = {"title": "Award of Order: bags order", "source": "NSE filing", "link": "", "date": "2026-10-24",
            "kind": "filing", "sentiment": "positive"}
    meetings = {"RES": [{"date": "2026-10-28", "purpose": "Financial Results", "results": True}]}
    checked, skipped, down = context.research(
        [_cand("RES", 90), _cand("BAD", 85), _cand("GOOD", 70)], CFG, TODAY,
        FakeNSE({"BAD": [bad], "GOOD": [good]}), meetings)
    assert [s["symbol"] for s in skipped] == ["RES"]
    by = {c["symbol"]: c for c in checked}
    assert by["BAD"]["score"] == 85 - CFG["news_penalty"] and by["BAD"]["news"][0] == bad
    assert by["GOOD"]["score"] == 70 + CFG["news_bonus"]
    assert down == []


def test_holding_warning():
    meetings = {"X": [{"date": "2026-10-28", "purpose": "Financial Results", "results": True}]}
    w = context.holding_warnings([{"symbol": "X", "status": "OPEN"}], meetings, CFG, TODAY)
    assert len(w) == 1 and "28 Oct" in w[0]


def test_message_shows_pulse_news_and_skips():
    pick = {"symbol": "ABC", "name": "Abc Ltd.", "sector": "X", "score": 80.0, "news_adj": 5,
            "entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 115.0, "close": 99.0,
            "risk_pct": 5.0, "qty": 20, "why": "test",
            "news": [{"title": "Abc bags order <big>", "source": "Mint", "link": "https://x/?a=1&b=2",
                      "date": "2026-10-25", "kind": "news", "sentiment": "positive"}],
            "next_event": {"date": "2026-11-12", "purpose": "Financial Results", "results": True}}
    extra = {"pulse": {"vix": {"value": 17.3, "change_pct": 2.8, "label": "normal"},
                       "fii_dii": {"fii": 887.65, "dii": -12.0, "date": "2026-10-23"}},
             "warnings": [], "down": ["Google News"]}
    sections = [{"title": "Top 5 at any price", "top_n": 5, "picks": [pick],
                 "skipped": [{"symbol": "RES", "reason": "Financial Results on 28 Oct"}]},
                {"title": "Top 10 up to ₹500", "top_n": 10, "picks": [], "skipped": []}]
    stats = {"closed": 0, "open": 0, "wins": 0, "losses": 0, "win_rate": None, "avg_result_pct": None}
    text = build_message("Tue", "2026-10-23", {"mood": "Neutral", "detail": "d"}, sections, [], stats,
                         CFG, "", extra)
    assert "India VIX: 17.3" in text and "FII +₹888 Cr" in text and "DII −₹12 Cr" in text
    assert "(news +5)" in text and "&lt;big&gt;" in text and 'href="https://x/?a=1&amp;b=2"' in text
    assert "RES (Financial Results on 28 Oct)" in text and "12 Nov" in text
    assert "Not available today: Google News" in text
    assert "━━ Top 5 at any price ━━" in text and "━━ Top 10 up to ₹500 ━━" in text
    assert "Only 1 stock(s)" in text and "No stock passed" in text and "ABC start" in text


def test_headline_must_mention_the_company():
    assert news.mentions("Oriental Hotels shares jump 8% on strong bookings", "Oriental Hotels Limited")
    assert not news.mentions("Demand-supply mismatch to drive gains for Indian Hotels", "Oriental Hotels Limited")
    assert news.mentions("Nazara Technologies posts record revenue", "Nazara Technologies Limited")
    assert not news.mentions("Top stocks to watch today: Paytm, Infosys | Full list", "Nazara Technologies Limited")
    assert news.mentions("Cyient bags order from Airbus", "Cyient Ltd.")
    assert not news.mentions("Gokaldas eyes Europe to cut US reliance", "Reliance Industries Ltd.")
