# Trade Helper: NSE swing-trade scanner

Every trading morning at about **7:45 AM India time**, this app:

1. Downloads the latest daily prices for the **Nifty 500** stocks.
2. Checks how the market is doing (Nifty 50 trend: Bullish / Neutral / Bearish).
3. Follows up on earlier picks: entry triggered, target hit, stop-loss hit.
4. Scans every stock with fixed rules and picks the **top 5 setups** (at most 2 per sector).
5. For each one gives **Buy above**, **Stop-loss**, **Target 1**, **Target 2** and how many
   shares fit your capital and risk.
6. Sends it all to your **Telegram**, and saves every pick in `data/picks.csv` so the track
   record builds up.

It runs on GitHub for free. No server, no database, nothing to install on your computer.
It **never places orders**. You decide and you place every trade yourself.

> ⚠️ These are computer-generated signals from price rules, for learning and paper trading.
> They are not investment advice and not a promise of profit. Watch the track record for
> 3–4 weeks before risking real money, and only use money you can afford to lose.

---

## One-time setup (about 10 minutes)

### 1. Create your Telegram bot
1. In Telegram, open **@BotFather** and send `/newbot`.
2. Give it a name (e.g. "My Trade Helper") and a username ending in `bot`.
3. BotFather replies with a **token** (looks like `123456789:ABC-xyz...`). Keep it private.
4. Open your new bot and press **Start** (a bot can only message you after you do this).

### 2. Find your chat ID
1. In Telegram, open **@userinfobot** and press **Start**.
2. It replies with your **Id** (a number like `987654321`).

### 3. Give both to GitHub (kept secret)
1. On GitHub open this repository → **Settings** → **Secrets and variables** → **Actions**.
2. **New repository secret**: name `TELEGRAM_BOT_TOKEN`, value = the token from step 1.
3. **New repository secret**: name `TELEGRAM_CHAT_ID`, value = the number from step 2.

Never paste the token in a chat, an email, or a file in the repository.

### 4. Test it
1. Open the **Actions** tab → **Daily NSE scan** → **Run workflow**.
2. Tick **Run even if NSE is closed today** if it's a weekend or holiday, and press **Run workflow**.
3. After 3–5 minutes the message should arrive on Telegram.

From then on it runs by itself every trading morning (weekends and NSE holidays are skipped).

---

## Changing the rules: `config.json`

| setting | meaning | default |
| --- | --- | --- |
| `capital_inr` | money set aside for these trades | 1,00,000 |
| `risk_per_trade_pct` | most you lose on one trade if the stop-loss hits, as % of capital | 1 |
| `max_position_pct` | most money in one stock, as % of capital | 20 |
| `top_n` | how many picks per day | 5 |
| `max_per_sector` | picks allowed from the same sector | 2 |
| `min_price` | ignore stocks below this price (₹) | 50 |
| `min_avg_traded_value_cr` | ignore stocks trading less than this per day (₹ crore) | 10 |
| `rsi_min` / `rsi_max` | momentum range allowed | 50–75 |
| `adx_min` | minimum trend strength | 20 |
| `max_extension_pct` | skip stocks already this % above their 20-day average | 10 |
| `max_atr_pct` | skip stocks that swing more than this % a day | 6 |
| `entry_buffer_pct` | "Buy above" = yesterday's high + this % | 0.2 |
| `stop_atr_multiple` | stop-loss distance, in average daily ranges (ATR) | 1.5 |
| `target1_r` / `target2_r` | targets as multiples of the risk | 2 / 3 |
| `entry_valid_days` | days a pick stays valid if price doesn't cross "Buy above" | 3 |
| `max_hold_days` | trading days before an open trade is closed | 15 |
| `skip_new_picks_when_market_bearish` | send no new picks when Nifty is falling | false |

## How a stock is chosen

**Must pass all of these:** price above the 20, 50 and 200-day averages (in that order), RSI between
50 and 75, ADX at least 20, enough daily trading, not stretched too far above its 20-day average, and
not too jumpy.

**Then scored out of 100:**

| part | points | what earns them |
| --- | --- | --- |
| Trend | 25 | averages lined up, 50-day average rising |
| Momentum | 20 | RSI 55–68, stronger ADX |
| Volume | 25 | yesterday's volume vs 20-day average (2x = full marks) |
| Breakout | 30 | closing at a 20-day high, close to the 52-week high |

The score shows how well a stock matches the rules. It is **not** a probability of profit.

## How results are counted (`data/picks.csv`)

Each pick is followed on daily prices: bought when the price crosses "Buy above" (cancelled after
3 days if it doesn't), half booked at Target 1 with the stop moved to the buy price, the rest at
Target 2, and the stop-loss always respected. When the stop and a target are both touched on the
same day, it counts as a stop-loss. These are estimates, not your real fills, and they exclude
brokerage and taxes.

## Data sources

- Nifty 500 list: niftyindices.com (a saved copy is in `data/nifty500.csv`).
- Prices: Yahoo Finance's free daily data (end of day, unofficial). If it stops working, the
  app sends a warning instead of picks; the fix is to switch to a broker's data feed.
- NSE holidays 2026: in `india_trader/market_calendar.py`. Add 2027 when NSE publishes it.

## For developers

```bash
pip install -r requirements.txt
python -m pytest -q                       # tests
python -m india_trader.main --demo        # made-up prices, offline
python -m india_trader.main --dry-run     # real prices, nothing sent or saved
```

## Roadmap

- **Step 2:** alerts during market hours (entry triggered, target, stop-loss)
- **Step 3:** NSE/BSE company announcements and results in the score, plus FII/DII and India VIX
- **Step 4:** watchlist, simple web dashboard, read-only link to Zerodha holdings
