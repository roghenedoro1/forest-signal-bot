import os
import logging
import asyncio
import telegram
import yfinance as yf
import pandas as pd
import ta
import requests  # FIXED: Added to handle browser sessions
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application

# Configure structured production logs
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
print(f"TELEGRAM VERSION: {telegram.__version__}")

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MAJOR_PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X", 
    "XAUUSD": "XAUUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X"
}

# FIXED: Create a custom request session to bypass Yahoo's anti-scraping blocks
custom_session = requests.Session()
custom_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
})

def get_5m_data(symbol):
    try:
        # FIXED: Injected session to spoof a modern web browser request
        df = yf.download(symbol, period="5d", interval="5m", progress=False, session=custom_session)
        if df.empty: 
            logging.warning(f"No data returned for symbol: {symbol}")
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logging.error(f"Error downloading data for {symbol}: {str(e)}")
        return None

def check_forest_signal(pair_name, symbol):
    df = get_5m_data(symbol)
    if df is None or len(df) < 50: 
        return None
    try:
        close_series = df['Close'].squeeze()
        df['ema50'] = ta.trend.ema_indicator(close_series, window=50)
        df['ema200'] = ta.trend.ema_indicator(close_series, window=200)
        df['rsi'] = ta.momentum.rsi(close_series, window=14)
        macd_calc = ta.trend.MACD(close_series)
        df['macd'] = macd_calc.macd()
        df['macd_signal'] = macd_calc.macd_signal()
        df.dropna(inplace=True)
        if len(df) < 2: return None
        last = df.iloc[-1]
        prev = df.iloc[-2]
        buy = last['ema50'] > last['ema200'] and last['rsi'] > 50 and prev['macd'] < prev['macd_signal'] and last['macd'] > last['macd_signal']
        sell = last['ema50'] < last['ema200'] and last['rsi'] < 50 and prev['macd'] > prev['macd_signal'] and last['macd'] < last['macd_signal']
        price = round(float(last['Close']), 5 if "JPY" not in pair_name else 3)
        if buy:
            return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price * 0.998, 4), "tp1": round(price * 1.004, 4), "tp2": round(price * 1.008, 4), "timeframe": "5M"}
        if sell:
            return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price * 1.002, 4), "tp1": round(price * 0.996, 4), "tp2": round(price * 0.992, 4), "timeframe": "5M"}
    except Exception as e:
        logging.error(f"Strategy computation error for {pair_name}: {str(e)}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌲 Forest Signal Bot v2 is LIVE\n"
        f"Scanning: {', '.join(MAJOR_PAIRS.keys())} on 5M\n"
        "Frequency: Every 10 minutes\n"
        "Commands:\n/start - Status\n/signal - Manual scan"
    )

# FIXED: Modified to provide immediate, transparent user feedback
async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🔍 Scanning major pairs now...")
    
    # Run the market scan and capture the count of successfully transmitted signals
    signals_sent = await run_scan(context.application)
    
    # Send conclusion updates so user knows the bot did not get stuck
    if signals_sent > 0:
        await update.message.reply_text(f"✅ Scan finished. Sent {signals_sent} active trading signal(s).")
    else:
        await update.message.reply_text(
            "⚠️ Scan finished. No valid trade setups found at this time.\n\n"
            "Note: Markets are completely closed on weekends, meaning Yahoo Finance will return empty datasets."
        )

# FIXED: Returns integer counter for UI tracking feedback loops
async def run_scan(app: Application) -> int:
    if not CHAT_ID:
        logging.error("Cannot run scan: CHAT_ID environment variable is missing!")
        return 0
        
    signals_count = 0
    for pair_name, symbol in MAJOR_PAIRS.items():
        logging.info(f"Checking {pair_name}")
        sig = check_forest_signal(pair_name, symbol)
        if sig:
            msg = (
                f"🚨 **FOREST SIGNAL - {sig['timeframe']}** 🚨\n\n"
                f"**Pair:** {sig['pair']}\n"
                f"**Direction:** {sig['direction']}\n"
                f"**Entry:** {sig['entry']}\n"
                f"**SL:** {sig['sl']}\n"
                f"**TP1:** {sig['tp1']}\n"
                f"**TP2:** {sig['tp2']}\n\n"
                f"Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC\n"
                f"Risk 1-2% per trade. Not financial advice."
            )
            try:
                await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                signals_count += 1
                await asyncio.sleep(1)
            except Exception as e:
                logging.error(f"Failed to transmit Telegram message: {str(e)}")
                
    return signals_count

async def auto_scanner(app: Application):
    await asyncio.sleep(15)
    while True:
        logging.info("Initiating automated 10-minute multi-market loop scan...")
        try:
            await run_scan(app)
        except Exception as e:
            logging.critical(f"Loop runtime exception occurred within scanner execution: {str(e)}")
        await asyncio.sleep(600)

async def post_init_hook(app: Application) -> None:
    asyncio.create_task(auto_scanner(app))

def main():
    if not TOKEN:
        raise ValueError("CRITICAL: BOT_TOKEN is absent from host environment variables.")
    if not CHAT_ID:
        logging.warning("Warning: CHAT_ID config is absent. Automated broadcast warnings will fail.")
    
    app = ApplicationBuilder().token(TOKEN).post_init(post_init_hook).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    logging.info("Establishing persistent long polling stack connection...")
    app.run_polling()

if __name__ == "__main__":
    main()
