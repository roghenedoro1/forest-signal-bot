import os, logging, asyncio, pandas as pd, ta, json
from datetime import datetime, timedelta, time as datetime_time
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
import aiohttp 

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

async def get_5m_data(symbol):
    if not FH_KEY: return None
    try:
        end = int(datetime.utcnow().timestamp())
        # FIX 1: Corrected Finnhub URL
        url = f"https://finnhub.io/api/v1/forex/candle?symbol={symbol}&resolution=5&from={end - 432000}&to={end}&token={FH_KEY}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                res = await response.json()
        
        if res.get('s') != 'ok': 
            logging.warning(f"Finnhub status not OK for {symbol}: {res.get('msg', 'No market data')}")
            return None
            
        required_keys = ['o', 'h', 'l', 'c', 't']
        if not all(k in res for k in required_keys):
            logging.warning(f"Incomplete data for {symbol}")
            return None
            
        df = pd.DataFrame({'Open': res['o'], 'High': res['h'], 'Low': res['l'], 'Close': res['c']}, index=pd.to_datetime(res['t'], unit='s'))
        return df.sort_index(ascending=True)
    except Exception as e:
        logging.error(f"Fetch error for {symbol}: {e}")
        return None

def check_forest_signal(pair_name, df):
    if df is None or len(df) < 50: return None
    try:
        close_series = df['Close'].squeeze()
        last, prev = df.iloc[-1], df.iloc[-2]
        price = round(float(last['Close']), 5 if "JPY" not in pair_name else 3)
        
        # FIX 3: Prevent duplicate signals - don't signal if same direction already active
        existing_dirs = [t['direction'] for t in trade_database[pair_name]['active_trades']]
        
        if pair_name in ["EURUSD", "GBPUSD", "AUDUSD"]:
            df['ema50'] = ta.trend.ema_indicator(close_series, window=50)
            df['ema200'] = ta.trend.ema_indicator(close_series, window=200)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            m = ta.trend.MACD(close_series)
            df['macd'], df['macd_sig'] = m.macd(), m.macd_signal()
            buy = (last['ema50'] > last['ema200'] and last['rsi'] > 52 and prev['macd'] < prev['macd_sig'] and last['macd'] > last['macd_sig'] and "BUY" not in existing_dirs)
            sell = (last['ema50'] < last['ema200'] and last['rsi'] < 48 and prev['macd'] > prev['macd_sig'] and last['macd'] < last['macd_sig'] and "SELL" not in existing_dirs)
            p = 0.0012
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - p, 5), "tp1": round(price + (p*2), 5), "tp2": round(price + (p*3), 5)}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + p, 5), "tp1": round(price - (p*2), 5), "tp2": round(price - (p*3), 5)}
        elif pair_name == "XAUUSD":
            df['ema9'] = ta.trend.ema_indicator(close_series, window=9)
            df['ema21'] = ta.trend.ema_indicator(close_series, window=21)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            buy = (prev['ema9'] <= prev['ema21'] and last['ema9'] > last['ema21'] and last['rsi'] > 55 and "BUY" not in existing_dirs)
            sell = (prev['ema9'] >= prev['ema21'] and last['ema9'] < last['ema21'] and last['rsi'] < 45 and "SELL" not in existing_dirs)
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - 2.5, 2), "tp1": round(price + 4.0, 2), "tp2": round(price + 7.5, 2)}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + 2.5, 2), "tp1": round(price - 4.0, 2), "tp2": round(price - 7.5, 2)}
        elif pair_name == "USDJPY":
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            b = ta.volatility.BollingerBands(close_series, window=20, window_dev=2)
            df['bb_h'], df['bb_l'] = b.bollinger_hband(), b.bollinger_lband()
            buy = (last['Close'] > last['bb_h'] and last['rsi'] > 60 and "BUY" not in existing_dirs)
            sell = (last['Close'] < last['bb_l'] and last['rsi'] < 40 and "SELL" not in existing_dirs)
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
    await update.message.reply_text("🔍 Running real-time market analysis via Finnhub...")
    await run_scan(context.application)

async def run_scan(app: Application) -> int:
    if not CHAT_ID: 
        logging.warning("CHAT_ID is not configured.")
        return 0
    
    # Skip if market closed to save API calls
    if "Closed" in get_market_status_wat():
        logging.info("Market closed. Skipping scan.")
        return 0
        
    signals_count = 0
    for pair_name, fh_symbol in MAJOR_PAIRS.items():
        df = await get_5m_data(fh_symbol)
        if df is None or df.empty:
            await asyncio.sleep(1) # Rate limit protection
            continue
            
        current_price = float(df['Close'].iloc[-1])
        win_rate_str = update_and_get_winrate(pair_name, current_price)
        
        signal_data = check_forest_signal(pair_name, df)
        if signal_data:
            signals_count += 1
            trade_database[pair_name]["active_trades"].append(signal_data)
            save_database()
            
            message = (
                f"🌲 <b>NEW FOREX SIGNAL: {pair_name}</b> 🌲\n\n"
                f"<b>Action:</b> {signal_data['direction']}\n"
                f"<b>Entry Price:</b> {signal_data['entry']}\n"
                f"<b>Stop Loss:</b> {signal_data['sl']}\n"
                f"<b>Take Profit 1:</b> {signal_data['tp1']}\n"
                f"<b>Take Profit 2:</b> {signal_data['tp2']}\n\n"
                f"📈 <b>Historical Win Rate:</b> {win_rate_str}"
            )
            try:
                await app.bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Failed to send alert for {pair_name}: {e}")
        await asyncio.sleep(1) # Rate limit protection
    return signals_count

# FIX 2: JobQueue for safe background scanning
async def run_background_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await run_scan(context.application)
    except Exception as e:
        logging.critical(f"Critical exception in background job: {e}")

# FIX 2: Completed main() function
def main():
    if not TOKEN:
        raise ValueError("CRITICAL: BOT_TOKEN environment variable not set!")
    
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    
    # Run scan every 10 minutes, first run in 5 seconds
    app.job_queue.run_repeating(run_background_job, interval=600, first=5)
    
    logging.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
