import os
import logging
import asyncio
import pandas as pd
import ta
import json
from datetime import datetime, timedelta, time as datetime_time, timezone
import aiohttp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TD_KEY = os.getenv("TWELVEDATA_KEY") # Make sure this matches Render
DB_FILE = "./data/trade_database.json"

MAJOR_PAIRS = {
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "XAU/USD": "XAU/USD",
    "USD/JPY": "USD/JPY",
    "AUD/USD": "AUD/USD"
}

TP_PIPS = 15
SL_PIPS = 15

def load_database():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"DB read error: {e}")
    return {pair: {"wins": 0, "losses": 0, "active_trades": []} for pair in MAJOR_PAIRS}

def save_database():
    """Saves updated trading records to disk."""
    try:
        #Create the /var/data folder automatically if it does not exist yet
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
                    
        with open(DB_FILE, "w") as f:
            json.dump(trade_database, f, indent=4)
    except Exception as e:
        logging.error(f"DB write error: {e}")

trade_database = load_database()

def get_market_status_wat():
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    weekday, now_time = now.weekday(), now.time()
    m_open, m_close = datetime_time(22, 0), datetime_time(22, 0)

    if weekday == 5:
        target = datetime.combine(now.date() + timedelta(days=1), m_open)
        diff = target - now.replace(tzinfo=None)
        return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)", False
    elif weekday == 6:
        if now_time < m_open:
            target = datetime.combine(now.date(), m_open)
            diff = target - now.replace(tzinfo=None)
            return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)", False
        diff = datetime.combine(now.date() + timedelta(days=5 - weekday), m_close) - now.replace(tzinfo=None)
        return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)", True
    elif weekday == 4:
        if now_time >= m_close:
            diff = datetime.combine(now.date() + timedelta(days=2), m_open) - now.replace(tzinfo=None)
            return f"🔴 Closed (Opens in {diff.days}d {diff.seconds // 3600}h)", False
        diff = datetime.combine(now.date(), m_close) - now.replace(tzinfo=None)
        return f"🟢 Open (Closes in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)", True

    diff = datetime.combine(now.date() + timedelta(days=(4 - weekday)), m_close) - now.replace(tzinfo=None)
    return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)", True

def calculate_targets(pair_name, direction, entry_price):
    if "JPY" in pair_name:
        pip_value = 0.01
    elif "XAU" in pair_name:
        pip_value = 0.1
    else:
        pip_value = 0.0001

    tp_dist = TP_PIPS * pip_value
    sl_dist = SL_PIPS * pip_value

    if direction == "BUY":
        tp = entry_price + tp_dist
        sl = entry_price - sl_dist
    else:
        tp = entry_price - tp_dist
        sl = entry_price + sl_dist

    decimals = 3 if ("JPY" in pair_name or "XAU" in pair_name) else 5
    return round(tp, decimals), round(sl, decimals)

async def get_5m_data(symbol):
    if not TD_KEY:
        logging.error("Missing TWELVEDATA_KEY environment configuration.")
        return None

    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=5min&outputsize=300&apikey={TD_KEY}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                res = await response.json()

        # Check for API errors
        if res.get("status") == "error":
            logging.warning(f"Twelve Data: {res.get('message')}")
            return None

        candles = res.get("values")
        if not candles:
            logging.warning(f"No candle data returned for {symbol}")
            return None

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df.set_index("datetime", inplace=True)

        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col])

        df.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
            },
            inplace=True,
        )

        return df.sort_index()

    except Exception as e:
        logging.error(f"Twelve Data Fetch error for {symbol}: {e}")
        return None

def check_forest_signal(pair_name, df):
    if df is None or len(df) < 50:
        return None
    try:
        rsi = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
        macd = ta.trend.MACD(close=df['Close'])
        macd_line = macd.macd()
        macd_signal = macd.macd_signal()
        ema50 = ta.trend.EMAIndicator(close=df["Close"], window=50).ema_indicator()
        ema200 = ta.trend.EMAIndicator(close=df["Close"], window=200).ema_indicator()

        trend_up = ema50.iloc[-1] > ema200.iloc[-1]
        trend_down = ema50.iloc[-1] < ema200.iloc[-1]
        
        last_rsi = rsi.iloc[-1]
        last_macd_line = macd_line.iloc[-1]
        last_macd_signal = macd_signal.iloc[-1]

        last_row = df.iloc[-1]
        price = round(float(last_row['Close']), 5 if "JPY" not in pair_name and "XAU" not in pair_name else 3)

        existing_dirs = [t['direction'] for t in trade_database[pair_name]['active_trades']]

        if trend_up and last_rsi < 30 and last_macd_line > last_macd_signal:
            if "BUY" not in existing_dirs:
                return {"direction": "BUY", "price": price, "rsi": round(last_rsi, 2)}

        elif trend_down and last_rsi < 70 and last_macd_line < last_macd_signal:
            if "SELL" not in existing_dirs:
                return {"direction": "SELL", "price": price, "rsi": round(last_rsi, 2)}

        return None
    except Exception as e:
        logging.error(f"Signal calculations error on {pair_name}: {e}")
        return None

async def manage_active_trades(pair_id, pair_label, current_price, context):
    active_trades = trade_database[pair_id].get('active_trades', [])
    remaining_trades = []

    for trade in active_trades:
        direction = trade['direction']
        tp = trade['tp']
        sl = trade['sl']
        entry = trade['entry_price']
        closed = False
        outcome = ""

        if direction == "BUY":
            if current_price >= tp:
                closed, outcome = True, "✅ WIN (TP Hit)"
            elif current_price <= sl:
                closed, outcome = True, "❌ LOSS (SL Hit)"
        elif direction == "SELL":
            if current_price <= tp:
                closed, outcome = True, "✅ WIN (TP Hit)"
            elif current_price >= sl:
                closed, outcome = True, "❌ LOSS (SL Hit)"

        if closed:
            if "WIN" in outcome:
                trade_database[pair_id]['wins'] += 1
            else:
                trade_database[pair_id]['losses'] += 1

            total_wins = trade_database[pair_id]['wins']
            total_losses = trade_database[pair_id]['losses']
            win_rate = (total_wins / (total_wins + total_losses)) * 100 if (total_wins + total_losses) > 0 else 0

            msg = f"🏁 <b>Trade Closed: {pair_label}</b> 🏁\n\n" \
                  f"Result: <b>{outcome}</b>\n" \
                  f"Direction: <b>{direction}</b>\n" \
                  f"Entry Price: <code>{entry}</code>\n" \
                  f"Exit Price: <code>{current_price}</code>\n\n" \
                  f"📊 <b>Pair Stats:</b>\n" \
                  f"Wins: {total_wins} | Losses: {total_losses}\n" \
                  f"Current Win Rate: <code>{win_rate:.1f}%</code>"

            try:
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Failed sending trade outcome: {e}")
        else:
            remaining_trades.append(trade)

    trade_database[pair_id]['active_trades'] = remaining_trades
    save_database()
    
async def trade_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    status_str, is_open = get_market_status_wat()

    if not is_open:
        return

    for pair_id, pair_label in MAJOR_PAIRS.items():

        df = await get_5m_data(pair_id)

        if df is None:
            continue

        current_price = round(
            float(df.iloc[-1]["Close"]),
            5 if "JPY" not in pair_id and "XAU" not in pair_id else 3
        )

        await manage_active_trades(
            pair_id,
            pair_label,
            current_price,
            context
        )

        await asyncio.sleep(2)

async def signal_scan_job(context: ContextTypes.DEFAULT_TYPE):
    status_str, is_open = get_market_status_wat()
    if not is_open:
        logging.info(f"Market Closed: {status_str}")
        return

    for pair_id, pair_label in MAJOR_PAIRS.items():
        df = await get_5m_data(pair_id)
        if df is None:
            await asyncio.sleep(10) # 10s sleep to respect 8 calls/min limit
            continue

        last_row = df.iloc[-1]
        current_price = round(float(last_row['Close']), 5 if "JPY" not in pair_id and "XAU" not in pair_id else 3)

        signal = check_forest_signal(pair_id, df)

        if signal:
            tp, sl = calculate_targets(pair_id, signal['direction'], signal['price'])

            msg = f"🚨 <b>{pair_label} Signal Alert</b> 🚨\n\n" \
                  f"Direction: <b>{signal['direction']}</b>\n" \
                  f"Execution Price: <code>{signal['price']}</code>\n" \
                  f"RSI Value: <code>{signal['rsi']}</code>\n\n" \
                  f"🎯 Target TP: <code>{tp}</code>\n" \
                  f"🛡️ Target SL: <code>{sl}</code>" # FIXED: was missing </code> and parse_mode

            trade_database[pair_id]['active_trades'].append({
                "direction": signal['direction'],
                "entry_price": signal['price'],
                "tp": tp,
                "sl": sl,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            save_database()

            try:
                await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Failed sending alert telegram update: {e}")

        await asyncio.sleep(10) # Important: 5 pairs * 10s = 50s. Stays under 8 calls/min

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str, _ = get_market_status_wat()
    stats_msg = f"<b>Market Status:</b> {status_str}\n\n📊 <b>Performance Leaderboard:</b>\n"
    for pair_id, pair_label in MAJOR_PAIRS.items():
        w = trade_database[pair_id].get('wins', 0)
        l = trade_database[pair_id].get('losses', 0)
        active_count = len(trade_database[pair_id].get('active_trades', []))
        wr = (w / (w + l)) * 100 if (w + l) > 0 else 0
        stats_msg += f"• {pair_label}: {w}W - {l}L ({wr:.1f}% WR) | [{active_count} Active]\n"

    await update.message.reply_text(stats_msg, parse_mode="HTML")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 <b>FOREST AI Forex Signal Bot</b>\n\n"
        "Welcome!\n\n"
        "<b>Available Commands:</b>\n"
        "📊 /status - Market status\n"
        "📈 /signal - Scan for live signals\n"
        "🏆 /stats - Trading statistics\n"
        "❓ /help - Show commands\n\n"
        "Signals are monitored automatically every 10 minutes."
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>Available Commands</b>\n\n"
        "📊 /status - Market status & leaderboard\n"
        "📈 /signal - Scan all pairs immediately\n"
        "🏆 /stats - Performance statistics\n"
        "❓ /help - Show this help message"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_wins = 0
    total_losses = 0
    total_active = 0

    text = "🏆 <b>Trading Statistics</b>\n\n"

    for pair_id, pair_name in MAJOR_PAIRS.items():
        wins = trade_database[pair_id]["wins"]
        losses = trade_database[pair_id]["losses"]
        active = len(trade_database[pair_id]["active_trades"])

        total_wins += wins
        total_losses += losses
        total_active += active

        wr = (wins/(wins+losses)*100) if (wins+losses) else 0

        text += (
            f"<b>{pair_name}</b>\n"
            f"✅ Wins: {wins}\n"
            f"❌ Losses: {losses}\n"
            f"📈 Win Rate: {wr:.1f}%\n"
            f"📌 Active Trades: {active}\n\n"
        )

    overall = (total_wins/(total_wins+total_losses)*100) if (total_wins+total_losses) else 0

    text += (
        "──────────────\n"
        f"✅ Total Wins: {total_wins}\n"
        f"❌ Total Losses: {total_losses}\n"
        f"📊 Overall Win Rate: {overall:.1f}%\n"
        f"📌 Active Trades: {total_active}"
    )

    await update.message.reply_text(text, parse_mode="HTML")


async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    status, market_open = get_market_status_wat()

    if not market_open:
        await update.message.reply_text(f"❌ Market Closed\n\n{status}")
        return

    found = False

    for pair_id, pair_name in MAJOR_PAIRS.items():

        df = await get_5m_data(pair_id)

        if df is None:
            continue

        signal = check_forest_signal(pair_id, df)

        if signal:

            tp, sl = calculate_targets(
                pair_id,
                signal["direction"],
                signal["price"]
            )

            msg = (
                f"🚨 <b>{pair_name}</b>\n\n"
                f"Direction: <b>{signal['direction']}</b>\n"
                f"Entry: <code>{signal['price']}</code>\n"
                f"TP: <code>{tp}</code>\n"
                f"SL: <code>{sl}</code>\n"
                f"RSI: <code>{signal['rsi']}</code>"
            )

            await update.message.reply_text(msg, parse_mode="HTML")
            found = True

    if not found:
        await update.message.reply_text(
            "✅ No valid FOREST AI signals found right now."
        )

def main():
    if not TOKEN or not CHAT_ID or not TD_KEY:
        logging.error("Missing critical BOT_TOKEN, CHAT_ID or TWELVEDATA_KEY variables.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("signal", signal_command))

    if app.job_queue:

        app.job_queue.run_repeating(
            signal_scan_job,
            interval=600,
            first=10
        )

        app.job_queue.run_repeating(
            trade_monitor_job,
            interval=60,
            first=20
        )

        logging.info("Production Twelve Data tracking pipeline initialized on JobQueue.")

    else:
        logging.error("JobQueue initialization failed.")
        return

    app.run_polling()


if __name__ == "__main__":
    main()
