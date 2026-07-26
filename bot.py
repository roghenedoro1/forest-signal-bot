import os, logging, asyncio, telegram, pandas as pd, ta, requests, json
from datetime import datetime, timedelta, time as datetime_time
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
from aiohttp import web

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

TOKEN, CHAT_ID, FH_KEY = os.getenv("BOT_TOKEN"), os.getenv("CHAT_ID"), os.getenv("FINNHUB_KEY")
DB_FILE = "trade_database.json"
MAJOR_PAIRS = {
    "EURUSD": "OANDA:EUR_USD", "GBPUSD": "OANDA:GBP_USD", 
    "XAUUSD": "OANDA:XAU_USD", "USDJPY": "OANDA:USD_JPY", "AUDUSD": "OANDA:AUD_USD"
}

def load_database():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except Exception as e: logging.error(f"DB read error: {e}")
    return {pair: {"wins": 0, "losses": 0, "active_trades": []} for pair in MAJOR_PAIRS}

def save_database():
    try:
        with open(DB_FILE, "w") as f: json.dump(trade_database, f, indent=4)
    except Exception as e: logging.error(f"DB write error: {e}")

trade_database = load_database()

def get_market_status_wat():
    now = datetime.utcnow() + timedelta(hours=1)
    weekday, now_time = now.weekday(), now.time()
    m_open, m_close = datetime_time(22, 0), datetime_time(22, 0)
    if weekday == 5:
        target = datetime.combine(now.date() + timedelta(days=1), m_open)
        diff = target - now
        return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
    elif weekday == 6:
        if now_time < m_open:
            target = datetime.combine(now.date(), m_open)
            diff = target - now
            return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
        diff = datetime.combine(now.date() + timedelta(days=5 - weekday), m_close) - now
        return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)"
    elif weekday == 4:
        if now_time >= m_close:
            diff = datetime.combine(now.date() + timedelta(days=2), m_open) - now
            return f"🔴 Closed (Opens in {diff.days}d {diff.seconds // 3600}h)"
        diff = datetime.combine(now.date(), m_close) - now
        return f"🟢 Open (Closes in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
    diff = datetime.combine(now.date() + timedelta(days=(4 - weekday)), m_close) - now
    return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)"

def get_5m_data(symbol):
    if not FH_KEY: return None
    try:
        end = int(datetime.utcnow().timestamp())
        url = f"https://finnhub.io{symbol}&resolution=5&from={end - 432000}&to={end}&token={FH_KEY}"
        res = requests.get(url).json()
        if res.get('s') != 'ok': return None
        df = pd.DataFrame({'Open': res['o'], 'High': res['h'], 'Low': res['l'], 'Close': res['c']}, index=pd.to_datetime(res['t'], unit='s'))
        return df.sort_index(ascending=True)
    except Exception as e:
        logging.error(f"Fetch error: {e}")
        return None

def check_forest_signal(pair_name, symbol):
    df = get_5m_data(symbol)
    if df is None or len(df) < 50: return None
    try:
        close_series = df['Close'].squeeze()
        last, prev = df.iloc[-1], df.iloc[-2]
        price = round(float(last['Close']), 5 if "JPY" not in pair_name else 3)
        if pair_name in ["EURUSD", "GBPUSD", "AUDUSD"]:
            df['ema50'] = ta.trend.ema_indicator(close_series, window=50)
            df['ema200'] = ta.trend.ema_indicator(close_series, window=200)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            m = ta.trend.MACD(close_series)
            df['macd'], df['macd_sig'] = m.macd(), m.macd_signal()
            buy = (last['ema50'] > last['ema200'] and last['rsi'] > 52 and prev['macd'] < prev['macd_sig'] and last['macd'] > last['macd_sig'])
            sell = (last['ema50'] < last['ema200'] and last['rsi'] < 48 and prev['macd'] > prev['macd_sig'] and last['macd'] < last['macd_sig'])
            p = 0.0012
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - p, 5), "tp1": round(price + (p*2), 5), "tp2": round(price + (p*3), 5)}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + p, 5), "tp1": round(price - (p*2), 5), "tp2": round(price - (p*3), 5)}
        elif pair_name == "XAUUSD":
            df['ema9'] = ta.trend.ema_indicator(close_series, window=9)
            df['ema21'] = ta.trend.ema_indicator(close_series, window=21)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            buy = (prev['ema9'] <= prev['ema21'] and last['ema9'] > last['ema21'] and last['rsi'] > 55)
            sell = (prev['ema9'] >= prev['ema21'] and last['ema9'] < last['ema21'] and last['rsi'] < 45)
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - 2.5, 2), "tp1": round(price + 4.0, 2), "tp2": round(price + 7.5, 2)}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + 2.5, 2), "tp1": round(price - 4.0, 2), "tp2": round(price - 7.5, 2)}
        elif pair_name == "USDJPY":
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            b = ta.volatility.BollingerBands(close_series, window=20, window_dev=2)
            df['bb_h'], df['bb_l'] = b.bollinger_hband(), b.bollinger_lband()
            buy = (last['Close'] > last['bb_h'] and last['rsi'] > 60)
            sell = (last['Close'] < last['bb_l'] and last['rsi'] < 40)
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - 0.15, 3), "tp1": round(price + 0.3, 3), "tp2": round(price + 0.5, 3)}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + 0.15, 3), "tp1": round(price - 0.3, 3), "tp2": round(price - 0.5, 3)}
    except Exception as e: logging.error(f"Strategy error for {pair_name}: {e}")
    return None

def update_and_get_winrate(pair_name, current_price):
    db = trade_database[pair_name]
    retained, has_changed = [], False
    for t in db["active_trades"]:
        res = False
        if t["direction"] == "BUY":
            if current_price >= t["tp1"]: db["wins"] += 1; res = has_changed = True
            elif current_price <= t["sl"]: db["losses"] += 1; res = has_changed = True
        elif t["direction"] == "SELL":
            if current_price <= t["tp1"]: db["wins"] += 1; res = has_changed = True
            elif current_price >= t["sl"]: db["losses"] += 1; res = has_changed = True
        if not res: retained.append(t)
    db["active_trades"] = retained
    if has_changed: save_database()
    total = db["wins"] + db["losses"]
    if total == 0: return "100% (No trades resolved)"
    return f"{round((db['wins'] / total) * 100, 1)}% ({db['wins']}W - {db['losses']}L)"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🌲 Bot Pro Active (Finnhub Engine)\n📊 Market: {get_market_status_wat()}\nCommands:\n/start - Status\n/signal - Scan")

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Running real-time market analysis via unlimited Finnhub network streams...")
    await run_scan(context.application)

async def run_scan(app: Application) -> int:
    if not CHAT_ID: return 0
    signals_count = 0
    m_info = get_market_status_wat()
    for pair_name, fh_symbol in MAJOR_PAIRS.items():
        df = get_5m_data(fh_symbol)
        if df is None or df.empty: continue
        curr_p = round(float(df['Close'].iloc[-1]), 5 if "JPY" not in pair_name else 3)
        win_str = update_and_get_winrate(pair_name, curr_p)
        sig = check_forest_signal(pair_name, fh_symbol)
        if sig:
            trade_database[pair_name]["active_trades"].append({"direction": sig["direction"], "entry": sig["entry"], "sl": sig["sl"], "tp1": sig["tp1"]})
            save_database()
            msg = f"🚨 **SIGNAL: {sig['pair']}** 🚨\n🎯 **Action:** {sig['direction']}\n💵 **Entry:** {sig['entry']}\n🛑 **SL:** {sig['sl']}\n✅ **TP1:** {sig['tp1']}\n🚀 **TP2:** {sig['tp2']}\n⏱ **Market:** {m_info}\n📈 **Winrate:** {win_str}"
            try:
                await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                signals_count += 1
                await asyncio.sleep(1)
            except Exception as e: logging.error(f"Telegram error: {e}")
    return signals_count

async def auto_scanner(app: Application):
    await asyncio.sleep(5)
    while True:
        try: await run_scan(app)
        except Exception as e: logging.critical(f"Loop error: {e}")
        await asyncio.sleep(600)

async def post_init_hook(app: Application) -> None:
    asyncio.create_task(auto_scanner(app))

async def handle_health(request):
    return web.Response(text="Bot is alive", status=200)

def main():
    if not TOKEN: raise ValueError("CRITICAL: BOT_TOKEN is missing.")
    app = ApplicationBuilder().token(TOKEN).post_init(post_init_hook).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    web_app = web.Application()
    web_app.router.add_get('/', handle_health)
    port = int(os.getenv("PORT", 10000))
    loop = asyncio.get_event_loop()
    loop.create_task(app.initialize())
    loop.create_task(app.start())
    loop.create_task(app.updater.start_polling())
    web.run_app(web_app, host='0.0.0.0', port=port, loop=loop)

if __name__ == "__main__":
    main()
