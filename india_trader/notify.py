"""Builds the daily message and sends it to Telegram."""
import html
import os

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


def build_message(run_date: str, data_date: str, mood: dict, picks: list[dict],
                  events: list[str], stats: dict, cfg: dict, note: str = "") -> str:
    e = html.escape
    lines = [f"<b>📈 NSE swing setups for {e(run_date)}</b>",
             f"Prices up to the close of {e(data_date)}"]
    icon = {"Bullish": "🟢", "Neutral": "🟡", "Bearish": "🔴"}.get(mood["mood"], "⚪")
    lines.append(f"Market: {icon} <b>{e(mood['mood'])}</b>. {e(mood['detail'])}")
    if mood["mood"] == "Bearish":
        lines.append("<i>Market is weak: breakouts fail more often. Consider skipping or using smaller sizes.</i>")
    if note:
        lines.append(f"<i>{e(note)}</i>")
    lines.append("")

    if events:
        lines.append("<b>Updates on earlier picks</b>")
        lines += [e(x) for x in events]
        lines.append("")

    if picks:
        lines.append(f"<b>Top {len(picks)} setups</b> (capital {_inr(cfg['capital_inr'], 0)}, "
                     f"risk {cfg['risk_per_trade_pct']}% per trade)")
        for n, p in enumerate(picks, 1):
            qty = (f"{p['qty']} shares ≈ {_inr(p['qty'] * p['entry'], 0)}, "
                   f"max loss ≈ {_inr(p['qty'] * (p['entry'] - p['stop']), 0)}"
                   if p["qty"] > 0 else "1 share costs more than your per-stock limit")
            lines += [
                "",
                f"<b>{n}. {e(p['symbol'])}</b> · {e(p['name'])} · score {p['score']:.0f}/100",
                f"   Buy above: <b>{_inr(p['entry'])}</b> (last close {_inr(p['close'])})",
                f"   Stop-loss: <b>{_inr(p['stop'])}</b> (−{p['risk_pct']:.1f}%)",
                f"   Target 1: {_inr(p['target1'])} · Target 2: {_inr(p['target2'])}",
                f"   Size: {qty}",
                f"   Why: {e(p['why'])}",
            ]
        lines += ["",
                  f"<i>Rules: buy only if the price trades above 'Buy above' within "
                  f"{cfg['entry_valid_days']} trading days. Exit at the stop-loss, no exceptions. "
                  f"At Target 1 book half and move the stop to your buy price.</i>"]
    else:
        lines.append("No stock passed all the rules today. No trade is also a decision.")

    lines.append("")
    if stats["closed"]:
        lines.append(f"<b>Track record</b>: {stats['closed']} closed trades, {stats['wins']} wins, "
                     f"{stats['losses']} losses, win rate {stats['win_rate']}%, "
                     f"average {stats['avg_result_pct']:+.2f}% per trade. {stats['open']} still open.")
    else:
        lines.append(f"<b>Track record</b>: not enough closed trades yet ({stats['open']} open). "
                     f"Paper-trade for 3–4 weeks before using real money.")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


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
