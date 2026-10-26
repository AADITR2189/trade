# Trade Helper: NSE swing-trade scanner

Every trading morning at about **7:45 AM India time**, this app:

1. Downloads the latest daily prices for the **Nifty 500** stocks.
2. Checks how the market is doing (Nifty 50 trend: Bullish / Neutral / Bearish).
3. Follows up on earlier picks: entry triggered, target hit, stop-loss hit.
4. Scans with fixed rules and sends two lists (at most 2 per sector, no stock in both):
   - **Top 5 at any price**, from the Nifty 500
   - **Top 10 with a buy price up to ₹500**, from **all ~2,000 NSE main-board stocks**

   Change the lists under `lists` in `config.json` (`top_n`, `max_entry_price`, and `universe`:
   `nifty500` or `all_nse`).
5. For each one gives **Buy above**, **Stop-loss**, **Target 1**, **Target 2** and how many
   shares fit your capital and risk.
6. Sends it all to your **Telegram**, and saves every pick in `data/picks.csv` so the track
   record builds up.

It runs on GitHub for free. No server, no database, nothing to install on your computer.
It **never places orders**. You decide and you place every trade yourself.

> ⚠️ These are computer-generated signals from price rules, for learning and paper trading.
> They are not investment advice and not a promise of profit. Watch the track record for
> 3–4 weeks before risking real money, and only use money you can afford to lose.

### Live updates for the stocks you choose (Telegram commands)

Send these to your bot in Telegram (capitals don't matter):

| you send | what happens |
| --- | --- |
| `IRFC start` | live updates for IRFC; if it was in the morning list, its buy/stop/targets are used |
| `IRFC start 140 132 155` | live updates with your own buy price, stop-loss and target |
| `IRFC stop` | stop updates for IRFC |
| `stop all` | stop all updates |
| `watch it` | current price of every stock you're watching |
| `price IRFC` | current price of any NSE stock |
| `help` | the list of commands |

Watch as many stocks as you like, for as long as you like (days or months) until you send `stop`.
While the market is open the bot checks prices **every minute** and messages you when a watched stock:
🟢 crosses the buy price, 🛑 hits the stop-loss, 🎯 reaches Target 1, 🏆 Target 2, ↩️ falls back to
the buy price after Target 1, or 📈📉 moves 2% since the last update (`move_alert_pct`). After 3:30 PM
you get one closing line per watched stock.

The bot answers within seconds during market hours (9:05 AM – 3:45 PM on trading days) and within
30 minutes at other times. Prices come from Yahoo Finance and can lag the exchange by a few minutes:
confirm in Kite before acting. Only your Telegram chat can control the bot.

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
| `capital_inr` | money set aside for these trades | 10,000 |
| `risk_per_trade_pct` | most you lose on one trade if the stop-loss hits, as % of capital | 2 |
| `max_position_pct` | most money in one stock, as % of capital | 50 |
| `lists` | the morning lists: title, `universe` (`nifty500` / `all_nse`), `top_n`, `max_entry_price` | 5 any price + 10 up to ₹500 |
| `min_score` | leave out setups scoring below this (out of 100) | 60 |
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
| `move_alert_pct` | live update when a watched stock moves this % | 2 |
| `entry_valid_days` | days a pick stays valid if price doesn't cross "Buy above" | 3 |
| `max_hold_days` | trading days before an open trade is closed | 15 |
| `skip_new_picks_when_market_bearish` | send no new picks when Nifty is falling | false |
| `avoid_results_within_days` | skip stocks with results / a board meeting within this many days | 5 |
| `news_days` | how many days of news and filings to check | 3 |
| `news_bonus` | points added when recent news looks positive | 5 |
| `news_penalty` | points taken off when recent news looks negative | 15 |

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

## News, results and market pulse

For the 15 best technical candidates each morning the bot also checks:

- **Results calendar (NSE):** a stock with results or a board meeting in the next 5 days is skipped,
  because prices can gap sharply either way on results day. You're also warned when a stock you hold
  or are waiting to buy has results coming.
- **Exchange filings (NSE)** and **headlines (Google News)** from the last 3 days. Simple keyword rules
  tag them: negative (fraud, SEBI action, resignations, downgrades, pledges...) costs 15 points,
  positive (order wins, buybacks, bonus issues, upgrades...) adds 5. Up to two items are shown with links.
  Keyword rules are crude: read the story before acting on it.
- **Market pulse:** India VIX (below 13 calm, 13–18 normal, 18–24 nervous, 24+ fearful) and the
  previous day's FII and DII net buying or selling in the cash market.

If NSE blocks the bot on some day, the message says which source was unavailable and still sends
the price-based picks. To test the sources yourself: Actions → **Check data sources** → Run workflow.

## How results are counted (`data/picks.csv`)

Each pick is followed on daily prices: bought when the price crosses "Buy above" (cancelled after
3 days if it doesn't), half booked at Target 1 with the stop moved to the buy price, the rest at
Target 2, and the stop-loss always respected. When the stop and a target are both touched on the
same day, it counts as a stop-loss. These are estimates, not your real fills, and they exclude
brokerage and taxes.

## Data sources

- Nifty 500 list: niftyindices.com (a saved copy is in `data/nifty500.csv`).
- All NSE stocks: NSE's EQUITY_L.csv, main-board "EQ" series only (saved in `data/nse_equity.csv`);
  sectors from the Nifty Total Market list where known (`data/sectors.csv`). Stocks with an unknown
  sector are not limited by `max_per_sector`. The ₹10 Cr/day trading and ₹50 minimum price rules
  still apply, which keeps out illiquid and penny stocks.
- Prices: Yahoo Finance's free daily data (end of day, unofficial). If it stops working, the
  app sends a warning instead of picks; the fix is to switch to a broker's data feed.
- NSE holidays 2026: in `india_trader/market_calendar.py`. Add 2027 when NSE publishes it.

## For developers

```bash
pip install -r requirements.txt
python -m pytest -q                       # tests
python -m india_trader.main --demo        # made-up prices, offline
python -m india_trader.main --dry-run     # real prices, nothing sent or saved
python -m india_trader.bot --once         # answer waiting Telegram commands once
```

## Roadmap

- ~~**Step 2:** live alerts~~ done: now only for stocks you `start` in Telegram
- ~~**Step 3:** company announcements, results calendar, news, FII/DII and India VIX~~ done
- Step 4 (watchlist, holdings, dashboard): not planned; use Zerodha Kite for these. The bot never places orders.
