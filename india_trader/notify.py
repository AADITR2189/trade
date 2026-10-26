"""Builds the daily message and sends it to Telegram."""
import html
import os
from datetime import date

import requests

DISCLAIMER = ("⚠️ Computer-generated signals from price rules, for learning and paper trading. "
              "Not investment advice. Past patterns do not guarantee future moves.")


def _inr(x: float, decimals: int = 2) -> str:
    """Indian digit grouping: 1234567.5 -> ₹12,34,567.50"""
    sign = "-" if x < 0 else ""
    whole, _, frac = f"{abs(x):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return f"{sign}₹{whole}" + (f".{frac}" if decimals else "")


def _crore(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}₹{abs(x):,.0f} Cr"


def _news_line(item: dict) -> str:
    icon = {"negative": "⚠️", "positive": "✅"}.get(item["sentiment"], "📰")
    title = html.escape(item["title"])
    if item.get("link"):
        title = f'<a href="{html.escape(item["link"], quote=True)}">{title}</a>'
    when = date.fromisoformat(item["date"]).strftime("%d %b") if item.get("date") else ""
    return f"   {icon} {title} ({html.escape(item['source'])}{', ' + when if when else ''})"


def build_message(run_date: str, data_date: str, mood: dict, picks: list[dict],
                  events: list[str], stats: dict, cfg: dict, note: str = "",
                  extra: dict | None = None) -> str:
    extra = extra or {}
    e = html.escape
    lines = [f"<b>📈 NSE swing setups for {e(run_date)}</b>",
             f"Prices up to the close of {e(data_date)}"]
    icon = {"Bullish": "🟢", "Neutral": "🟡", "Bearish": "🔴"}.get(mood["mood"], "⚪")
    lines.append(f"Market: {icon} <b>{e(mood['mood'])}</b>. {e(mood['detail'])}")
    pulse = extra.get("pulse") or {}
    if pulse.get("vix"):
        v = pulse["vix"]
        lines.append(f"India VIX: {v['value']:.1f} ({v['change_pct']:+.1f}%), {v['label']}")
    if pulse.get("fii_dii"):
        f = pulse["fii_dii"]
        when = date.fromisoformat(f["date"]).strftime("%d %b") if f.get("date") else ""
        parts = [f"{k.upper()} {_crore(f[k])}" for k in ("fii", "dii") if k in f]
        lines.append(f"Cash market flows {when}: " + " · ".join(parts))
    if mood["mood"] == "Bearish":
        lines.append("<i>Market is weak: breakouts fail more often. Consider skipping or using smaller sizes.</i>")
    if pulse.get("vix") and pulse["vix"]["value"] >= 24:
        lines.append("<i>VIX is high: expect big swings. Smaller sizes are safer.</i>")
    if extra.get("down"):
        lines.append(f"<i>Not available today: {e(', '.join(extra['down']))}. News checks are incomplete.</i>")
    if note:
        lines.append(f"<i>{e(note)}</i>")
    lines.append("")

    if events or extra.get("warnings"):
        lines.append("<b>Updates on earlier picks</b>")
        lines += [e(x) for x in events]
        lines += [e(x) for x in extra.get("warnings", [])]
        lines.append("")

    if picks:
        lines.append(f"<b>Top {len(picks)} setups</b> (capital {_inr(cfg['capital_inr'], 0)}, "
                     f"risk {cfg['risk_per_trade_pct']}% per trade)")
        for n, p in enumerate(picks, 1):
            qty = (f"{p['qty']} shares ≈ {_inr(p['qty'] * p['entry'], 0)}, "
                   f"max loss ≈ {_inr(p['qty'] * (p['entry'] - p['stop']), 0)}"
                   if p["qty"] > 0 else "1 share costs more than your per-stock limit")
            adj = p.get("news_adj", 0)
            adj_txt = f" (news {adj:+d})" if adj else ""
            lines += [
                "",
                f"<b>{n}. {e(p['symbol'])}</b> · {e(p['name'])} · score {p['score']:.0f}/100{adj_txt}",
                f"   Buy above: <b>{_inr(p['entry'])}</b> (last close {_inr(p['close'])})",
                f"   Stop-loss: <b>{_inr(p['stop'])}</b> (−{p['risk_pct']:.1f}%)",
                f"   Target 1: {_inr(p['target1'])} · Target 2: {_inr(p['target2'])}",
                f"   Size: {qty}",
                f"   Why: {e(p['why'])}",
            ]
            lines += [_news_line(i) for i in p.get("news", [])]
            ev = p.get("next_event")
            if ev:
                lines.append(f"   📅 Next: {e(ev['purpose'] or 'board meeting')} on "
                             f"{date.fromisoformat(ev['date']).strftime('%d %b')}")
        if extra.get("skipped"):
            sk = "; ".join(f"{x['symbol']} ({x['reason']})" for x in extra["skipped"])
            lines += ["", f"<i>Skipped because results/board meeting are too close: {e(sk)}</i>"]
        if len(picks) < cfg["top_n"]:
            lines += ["", f"<i>Only {len(picks)} stock(s) scored {cfg.get('min_score', 0)}+ today, "
                          "so fewer picks than usual.</i>"]
        lines += ["",
                  f"<i>Rules: buy only if the price trades above 'Buy above' within "
                  f"{cfg['entry_valid_days']} trading days. Exit at the stop-loss, no exceptions. "
                  f"At Target 1 book half and move the stop to your buy price.</i>",
                  "",
                  "👉 For live updates on a stock, reply e.g. <code>"
                  f"{e(picks[0]['symbol'])} start</code>. Send <code>help</code> for all commands."]
    else:
        lines.append(f"No stock passed all the rules with a score of {cfg.get('min_score', 0)}+ today. "
                     "No trade is also a decision.")

    lines.append("")
    if stats["closed"]:
        lines.append(f"<b>Track record</b>: {stats['closed']} closed trades, {stats['wins']} wins, "
                     f"{stats['losses']} losses, win rate {stats['win_rate']}%, "
                     f"average {stats['avg_result_pct']:+.2f}% per trade. {stats['open']} in trade, "
                     f"{stats.get('waiting', 0)} waiting for entry.")
    else:
        lines.append(f"<b>Track record</b>: no closed trades yet ({stats['open']} in trade, "
                     f"{stats.get('waiting', 0)} waiting for entry). "
                     f"Paper-trade for 3–4 weeks before using real money.")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def get_updates(offset: int | None, wait_seconds: int = 0) -> list[dict] | None:
    """Messages sent to the bot since `offset` (Telegram keeps them for 24 hours)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    params = {"timeout": wait_seconds, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params,
                         timeout=wait_seconds + 15)
        return r.json().get("result", []) if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing); message not sent.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram allows 4096 characters per message; split on blank lines if longer.
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > 4000:
            chunks.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    chunks.append(cur)
    ok = True
    for chunk in chunks:
        r = requests.post(url, data={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                                     "disable_web_page_preview": "true"}, timeout=20)
        if not r.ok:
            print(f"Telegram error {r.status_code}: {r.text[:300]}")
            ok = False
    return ok
