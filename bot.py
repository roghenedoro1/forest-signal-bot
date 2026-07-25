import time
import datetime
import pytz
import yfinance as yf
import ta
import json
import os
import requests
import threading
import asyncio
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

LAGOS = pytz.timezone("Africa/Lagos")
BOT_TOKEN = os.getenv("BOT_TOKEN")

app = Flask(__name__)
@app.route('/')
def home():
    return "Forest Bot is Alive"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

DATA_FILE = "forest_data.json"
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, 'r') as f: data = json.load(f)
else: data = {"users": [], "wins": {}, "loss": {}, "sent_signals": [], "news_cache": []}

PAIRS = {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X"}

def save_data():
    with open(DATA_FILE, 'w') as f: json.dump(data, f)

def fetch_news():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=10).json()
        news_times = []
        for event in r:
            if event['impact'] == "High" and event['country'] in ["USD", "EUR", "GBP"]:
                event_time = datetime.datetime.strptime(event['date'] + " + event['time'], "%Y-%m-%d %H:%M:%S")                news_times.append(event_time.strftime("%Y-%m-%d %H:%M"))
        data["news_cache"] = news_times
        save_data()
    except: pass

def is_news_time():
    now = datetime.datetime.now(LAGOS)
    for news in data["news_cache"]:
        news_time = LAGOS.localize(datetime.datetime.strptime(news, "%Y-%m-%d %H:%M"))
        if abs((now - news_time).total_seconds()) < 900:
            return True
    return False

def get_signal_data(symbol):
    try:
        df = yf.Ticker(symbol).history(period="5d", interval="5m")
        if len(df) < 200: return None
        close, high, low = df['Close'], df['High'], df['Low']
        ema50 = ta.trend.EMAIndicator(close, 50).ema_indicator().iloc[-1]
        ema200 = ta.trend.EMAIndicator(close, 200).ema_indicator().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]
        macd = ta.trend.MACD(close).macd().iloc[-1]
        macd_sig = ta.trend.MACD(close).macd_signal().iloc[-1]
        price = close.iloc[-1]
        atr = ta.volatility.AverageTrueRange(high, low, close).average_true_range().iloc[-1]

        if ema50 > ema200 and rsi < 35 and macd > macd_sig: signal = "BUY"
        elif ema50 < ema200 and rsi > 65 and macd < macd_sig: signal = "SELL"
        else: return None

        sl_dist = atr * 2
        if signal == "BUY": sl, tp = round(price - sl_dist, 4), round(price + sl_dist * 2, 4)
        else: sl, tp = round(price + sl_dist, 4), round(price - sl_dist * 2, 4)
        return {"price": price, "rsi": rsi, "signal": signal, "sl": sl, "tp": tp}
    except: return None

async def send_signal_to_user(bot, user_id, name, s):
    signal_id = f"{name}_{user_id}_{int(time.time())}"
    keyboard = [[InlineKeyboardButton("✅ WIN", callback_data=f'win_{signal_id}'), InlineKeyboardButton("❌ LOSS", callback_data=f'loss_{signal_id}')]]
    msg = f"""🌲 <b>FOREST SNIPE SIGNAL</b> 🌲
<b>Pair:</b> {name}
<b>Signal:</b> {'🟢 BUY' if s['signal']=='BUY' else '🔴 SELL'}
<b>Entry:</b> {s['price']:.4f}
<b>SL:</b> {s['sl']:.4f}
<b>TP:</b> {s['tp']:.4f} | 1:2 RR
<b>RSI:</b> {s['rsi']:.1f}
<b>Time:</b> {datetime.datetime.now(LAGOS).strftime('%H:%M WAT')}"""
    try: await bot.send_message(chat_id=user_id, text=msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except: pass

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = str(q.from_user.id)
    action = q.data.split('_')[0]
    if user_id not in data["wins"]: data["wins"][user_id] = 0
    if user_id not in data["loss"]: data["loss"][user_id] = 0
    if action == 'win': data["wins"][user_id] += 1
    else: data["loss"][user_id] += 1
    save_data()
    total = data["wins"][user_id] + data["loss"][user_id]
    rate = round(data["wins"][user_id]/total*100, 1) if total > 0 else 0
    await q.edit_message_text(text=q.message.text + f"\n\n<b>Recorded!</b>\n📊 Your Winrate: {rate}% | W:{data['wins'][user_id]} L:{data['loss'][user_id]}", parse_mode="HTML")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.chat_id)
    if user_id not in data["users"]:
        data["users"].append(user_id)
        save_data()
    await update.message.reply_text("🌲 <b>Welcome to Forest Snipe Bot</b>\n\nYou go receive signals directly here.\nUse /winrate to check your stats.", parse_mode="HTML")

async def winrate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.chat_id)
    wins = data["wins"].get(user_id, 0)
    loss = data["loss"].get(user_id, 0)
    total = wins + loss
    rate = round(wins/total*100, 1) if total > 0 else 0
    msg = f"""📊 <b>YOUR STATS</b>\n<b>Total:</b> {total}\n<b>Wins:</b> {wins} ✅\n<b>Loss:</b> {loss} ❌\n<b>Winrate:</b> {rate}%"""
    await update.message.reply_text(msg, parse_mode="HTML")

async def main_loop(application):
    fetch_news()
    last_news_fetch = datetime.datetime.now()
    while True:
        now = datetime.datetime.now(LAGOS)
        if (now - last_news_fetch).total_seconds() > 21600: fetch_news(); last_news_fetch = now
        market_open = now.weekday() < 5 and 8 <= now.hour < 22
        if market_open and not is_news_time():
            if now.minute % 5 == 3:
                time_key = now.strftime("%Y%m%d_%H:%M")
                if time_key not in data["sent_signals"]:
                    for name, symbol in PAIRS.items():
                        s = get_signal_data(symbol)
                        if s:
                            for user_id in data["users"]: await send_signal_to_user(application.bot, user_id, name, s)
                    data["sent_signals"].append(time_key)
                    save_data()
        await asyncio.sleep(30)

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("winrate", winrate_command))
    application.add_handler(CallbackQueryHandler(button))

    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.create_task(main_loop(application))
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
