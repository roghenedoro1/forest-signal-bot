import os
import logging
import asyncio
import telegram
import yfinance as yf
import pandas as pd
import ta
import requests
import json
from datetime import datetime, timedelta, time as datetime_time
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# Configure structured production logs
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DB_FILE = "trade_database.json"

MAJOR_PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X", 
    "XAUUSD": "XAUUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X"
}

def load_database():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error reading database file: {str(e)}")
    
    return {pair: {"wins": 0, "losses": 0, "active_trades": []} for pair in MAJOR_PAIRS}

def save_database():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(trade_database, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to write state tracking modifications to JSON storage: {str(e)}")

trade_database = load_database()

custom_session = requests.Session()
custom_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
})

def get_market_status_wat():
    now = datetime.utcnow() + timedelta(hours=1)
    weekday = now.weekday()
    now_time = now.time()

    market_open_wat = datetime_time(22, 0)  
    market_close_wat = datetime_time(22, 0) 

    if weekday == 5:
        target = datetime.combine(now.date() + timedelta(days=1), market_open_wat)
        diff = target - now
        return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
    elif weekday == 6:
        if now_time < market_open_wat:
            target = datetime.combine(now.date(), market_open_wat)
            diff = target - now
            return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
        else:
            target = datetime.combine(now.date() + timedelta(days=5 - weekday), market_close_wat)
            diff = target - now
            return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)"
    elif weekday == 4:
        if now_time >= market_close_wat:
            target = datetime.combine(now.date() + timedelta(days=2), market_open_wat)
            diff = target - now
            return f"🔴 Closed (Opens in {diff.days}d {diff.seconds // 3600}h)"
        else:
            target = datetime.combine(now.date(), market_close_wat)
            diff = target - now
            return f"🟢 Open (Closes in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
    else:
        days_until_friday = (4 - weekday)
        target = datetime.combine(now.date() + timedelta(days=days_until_friday), market_close_wat)
        diff = target - now
        return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)"

def get_5m_data(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="5m", progress=False, session=custom_session)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logging.error(f"Error downloading data for {symbol}: {str(e)}")
        return None

def check_forest_signal(pair_name, symbol):
    df = get_5m_data(symbol)
    if df is None or len(df) < 50: return None
    try:
        close_series = df['Close'].squeeze()
        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = round(float(last['Close']), 5 if "JPY" not in pair_name else 3)

        if pair_name in ["EURUSD", "GBPUSD", "AUDUSD"]:
            df['ema50'] = ta.trend.ema_indicator(close_series, window=50)
            df['ema200'] = ta.trend.ema_indicator(close_series, window=200)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            macd_calc = ta.trend.MACD(close_series)
            df['macd'] = macd_calc.macd()
            df['macd_signal'] = macd_calc.macd_signal()
            buy = (last['ema50'] > last['ema200'] and last['rsi'] > 52 and prev['macd'] < prev['macd_signal'] and last['macd'] > last['macd_signal'])
            sell = (last['ema50'] < last['ema200'] and last['rsi'] < 48 and prev['macd'] > prev['macd_signal'] and last['macd'] < last['macd_signal'])
            sl_pips, tp_pips = 0.0012, 0.0024
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - sl_pips, 5), "tp1": round(price + tp_pips, 5), "tp2": round(price + (tp_pips*1.5), 5), "timeframe": "5M"}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + sl_pips, 5), "tp1": round(price - tp_pips, 5), "tp2": round(price - (tp_pips*1.5), 5), "timeframe": "5M"}

        elif pair_name == "XAUUSD":
            df['ema9'] = ta.trend.ema_indicator(close_series, window=9)
            df['ema21'] = ta.trend.ema_indicator(close_series, window=21)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            buy = (prev['ema9'] <= prev['ema21'] and last['ema9'] > last['ema21'] and last['rsi'] > 55)
            sell = (prev['ema9'] >= prev['ema21'] and last['ema9'] < last['ema21'] and last['rsi'] < 45)
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - 2.5, 2), "tp1": round(price + 4.0, 2), "tp2": round(price + 7.5, 2), "timeframe": "5M"}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + 2.5, 2), "tp1": round(price - 4.0, 2), "tp2": round(price - 7.5, 2), "timeframe": "5M"}

        elif pair_name == "USDJPY":
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            bollinger = ta.volatility.BollingerBands(close_series, window=20, window_dev=2)
            df['bb_high'] = bollinger.bollinger_hband()
            df['bb_low'] = bollinger.bollinger_lband()
            buy = (last['Close'] > last['bb_high'] and last['rsi'] > 60)
            sell = (last['Close'] < last['bb_low'] and last['rsi'] < 40)
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - 0.150, 3), "tp1": round(price + 0.300, 3), "tp2": round(price + 0.500, 3), "timeframe": "5M"}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + 0.150, 3), "tp1": round(price - 0.300, 3), "tp2": round(price - 0.500, 3), "timeframe": "5M"}
    except Exception as e:
        logging.error(f"Strategy computation error for {pair_name}: {str(e)}")
    return None

def update_and_get_winrate(pair_name, current_price):
    db = trade_database[pair_name]
    retained_active = []
    has_changed = False
    
    for trade in db["active_trades"]:
        is_resolved = False
        if trade["direction"] == "BUY":
            if current_price >= trade["tp1"]:
                db["wins"] += 1
                is_resolved, has_changed = True, True
            elif current_price <= trade["sl"]:
                db["losses"] += 1
                is_resolved, has_changed = True, True
        elif trade["direction"] == "SELL":
            if current_price <= trade["tp1"]:
                db["wins"] += 1
                is_resolved, has_changed = True, True
            elif current_price >= trade["sl"]:
                db["losses"] += 1
                is_resolved, has_changed = True, True
                
        if not is_resolved:
            retained_active.append(trade)
            
    db["active_trades"] = retained_active
    
    if has_changed:
        save_database()
            
    total = db["wins"] + db["losses"]
    if total == 0: return "100% (No completed trades recorded)"
    return f"{round((db['wins'] / total) * 100, 1)}% ({db['wins']}W - {db['losses']}L)"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = get_market_status_wat()
    await update.message.reply_text(
        "🌲 Forest Signal Bot Pro is LIVE\n\n"
        f"📊 Market Status (WAT): {status}\n"
        f"🔍 Pairs: {', '.join(MAJOR_PAIRS.keys())}\n\n"
        "Commands:\n/start - Check Status\n/signal - Force Instant Scan"
    )

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning market conditions across currency-specific algorithmic modules...")
    await run_scan(context.application)

async def run_scan(app: Application) -> int:
    if not CHAT_ID: return 0
    signals_count = 0
    market_time_info = get_market_status_wat()
    
    for pair_name, symbol in MAJOR_PAIRS.items():
        df = get_5m_data(symbol)
        if df is None or df.empty: continue
            
        current_price = round(float(df['Close'].iloc[-1]), 5 if "JPY" not in pair_name else 3)
        win_rate_string = update_and_get_winrate(pair_name, current_price)
        
        sig = check_forest_signal(pair_name, symbol)
        if sig:
            trade_database[pair_name]["active_trades"].append({
                "direction": sig["direction"], "entry": sig["entry"], "sl": sig["sl"], "tp1": sig["tp1"]
            })
            save_database()
            
            msg = (
                f"🚨 **FOREST SIGNAL: {sig['pair']} ({sig['timeframe']})** 🚨\n"
                f"🎯 **Action:** {sig['direction']}\n\n"
                f"💵 **Entry Price:** {sig['entry']}\n"
                f"🛑 **Stop Loss:** {sig['sl']}\n"
                f"✅ **Take Profit 1:** {sig['tp1']}\n"import os
import logging
import asyncio
import telegram
import yfinance as yf
import pandas as pd
import ta
import requests
import json
from datetime import datetime, timedelta, time as datetime_time
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# Configure structured production logs
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DB_FILE = "trade_database.json"

MAJOR_PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X", 
    "XAUUSD": "XAUUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X"
}

def load_database():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error reading database file: {str(e)}")
    
    return {pair: {"wins": 0, "losses": 0, "active_trades": []} for pair in MAJOR_PAIRS}

def save_database():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(trade_database, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to write state tracking modifications to JSON storage: {str(e)}")

trade_database = load_database()

custom_session = requests.Session()
custom_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
})

def get_market_status_wat():
    now = datetime.utcnow() + timedelta(hours=1)
    weekday = now.weekday()
    now_time = now.time()

    market_open_wat = datetime_time(22, 0)  
    market_close_wat = datetime_time(22, 0) 

    if weekday == 5:
        target = datetime.combine(now.date() + timedelta(days=1), market_open_wat)
        diff = target - now
        return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
    elif weekday == 6:
        if now_time < market_open_wat:
            target = datetime.combine(now.date(), market_open_wat)
            diff = target - now
            return f"🔴 Closed (Opens in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
        else:
            target = datetime.combine(now.date() + timedelta(days=5 - weekday), market_close_wat)
            diff = target - now
            return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)"
    elif weekday == 4:
        if now_time >= market_close_wat:
            target = datetime.combine(now.date() + timedelta(days=2), market_open_wat)
            diff = target - now
            return f"🔴 Closed (Opens in {diff.days}d {diff.seconds // 3600}h)"
        else:
            target = datetime.combine(now.date(), market_close_wat)
            diff = target - now
            return f"🟢 Open (Closes in {diff.seconds // 3600}h {(diff.seconds % 3600) // 60}m)"
    else:
        days_until_friday = (4 - weekday)
        target = datetime.combine(now.date() + timedelta(days=days_until_friday), market_close_wat)
        diff = target - now
        return f"🟢 Open (Closes in {diff.days}d {diff.seconds // 3600}h)"

def get_5m_data(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="5m", progress=False, session=custom_session)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logging.error(f"Error downloading data for {symbol}: {str(e)}")
        return None

def check_forest_signal(pair_name, symbol):
    df = get_5m_data(symbol)
    if df is None or len(df) < 50: return None
    try:
        close_series = df['Close'].squeeze()
        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = round(float(last['Close']), 5 if "JPY" not in pair_name else 3)

        if pair_name in ["EURUSD", "GBPUSD", "AUDUSD"]:
            df['ema50'] = ta.trend.ema_indicator(close_series, window=50)
            df['ema200'] = ta.trend.ema_indicator(close_series, window=200)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            macd_calc = ta.trend.MACD(close_series)
            df['macd'] = macd_calc.macd()
            df['macd_signal'] = macd_calc.macd_signal()
            buy = (last['ema50'] > last['ema200'] and last['rsi'] > 52 and prev['macd'] < prev['macd_signal'] and last['macd'] > last['macd_signal'])
            sell = (last['ema50'] < last['ema200'] and last['rsi'] < 48 and prev['macd'] > prev['macd_signal'] and last['macd'] < last['macd_signal'])
            sl_pips, tp_pips = 0.0012, 0.0024
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - sl_pips, 5), "tp1": round(price + tp_pips, 5), "tp2": round(price + (tp_pips*1.5), 5), "timeframe": "5M"}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + sl_pips, 5), "tp1": round(price - tp_pips, 5), "tp2": round(price - (tp_pips*1.5), 5), "timeframe": "5M"}

        elif pair_name == "XAUUSD":
            df['ema9'] = ta.trend.ema_indicator(close_series, window=9)
            df['ema21'] = ta.trend.ema_indicator(close_series, window=21)
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            buy = (prev['ema9'] <= prev['ema21'] and last['ema9'] > last['ema21'] and last['rsi'] > 55)
            sell = (prev['ema9'] >= prev['ema21'] and last['ema9'] < last['ema21'] and last['rsi'] < 45)
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - 2.5, 2), "tp1": round(price + 4.0, 2), "tp2": round(price + 7.5, 2), "timeframe": "5M"}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + 2.5, 2), "tp1": round(price - 4.0, 2), "tp2": round(price - 7.5, 2), "timeframe": "5M"}

        elif pair_name == "USDJPY":
            df['rsi'] = ta.momentum.rsi(close_series, window=14)
            bollinger = ta.volatility.BollingerBands(close_series, window=20, window_dev=2)
            df['bb_high'] = bollinger.bollinger_hband()
            df['bb_low'] = bollinger.bollinger_lband()
            buy = (last['Close'] > last['bb_high'] and last['rsi'] > 60)
            sell = (last['Close'] < last['bb_low'] and last['rsi'] < 40)
            if buy: return {"pair": pair_name, "direction": "BUY", "entry": price, "sl": round(price - 0.150, 3), "tp1": round(price + 0.300, 3), "tp2": round(price + 0.500, 3), "timeframe": "5M"}
            if sell: return {"pair": pair_name, "direction": "SELL", "entry": price, "sl": round(price + 0.150, 3), "tp1": round(price - 0.300, 3), "tp2": round(price - 0.500, 3), "timeframe": "5M"}
    except Exception as e:
        logging.error(f"Strategy computation error for {pair_name}: {str(e)}")
    return None

def update_and_get_winrate(pair_name, current_price):
    db = trade_database[pair_name]
    retained_active = []
    has_changed = False
    
    for trade in db["active_trades"]:
        is_resolved = False
        if trade["direction"] == "BUY":
            if current_price >= trade["tp1"]:
                db["wins"] += 1
                is_resolved, has_changed = True, True
            elif current_price <= trade["sl"]:
                db["losses"] += 1
                is_resolved, has_changed = True, True
        elif trade["direction"] == "SELL":
            if current_price <= trade["tp1"]:
                db["wins"] += 1
                is_resolved, has_changed = True, True
            elif current_price >= trade["sl"]:
                db["losses"] += 1
                is_resolved, has_changed = True, True
                
        if not is_resolved:
            retained_active.append(trade)
            
    db["active_trades"] = retained_active
    
    if has_changed:
        save_database()
            
    total = db["wins"] + db["losses"]
    if total == 0: return "100% (No completed trades recorded)"
    return f"{round((db['wins'] / total) * 100, 1)}% ({db['wins']}W - {db['losses']}L)"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = get_market_status_wat()
    await update.message.reply_text(
        "🌲 Forest Signal Bot Pro is LIVE\n\n"
        f"📊 Market Status (WAT): {status}\n"
        f"🔍 Pairs: {', '.join(MAJOR_PAIRS.keys())}\n\n"
        "Commands:\n/start - Check Status\n/signal - Force Instant Scan"
    )

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning market conditions across currency-specific algorithmic modules...")
    await run_scan(context.application)

async def run_scan(app: Application) -> int:
    if not CHAT_ID: return 0
    signals_count = 0
    market_time_info = get_market_status_wat()
    
    for pair_name, symbol in MAJOR_PAIRS.items():
        df = get_5m_data(symbol)
        if df is None or df.empty: continue
            
        current_price = round(float(df['Close'].iloc[-1]), 5 if "JPY" not in pair_name else 3)
        win_rate_string = update_and_get_winrate(pair_name, current_price)
        
        sig = check_forest_signal(pair_name, symbol)
        if sig:
            trade_database[pair_name]["active_trades"].append({
                "direction": sig["direction"], "entry": sig["entry"], "sl": sig["sl"], "tp1": sig["tp1"]
            })
            save_database()
            
            msg = (
                f"🚨 **FOREST SIGNAL: {sig['pair']} ({sig['timeframe']})** 🚨\n"
                f"🎯 **Action:** {sig['direction']}\n\n"
                f"💵 **Entry Price:** {sig['entry']}\n"
                f"🛑 **Stop Loss:** {sig['sl']}\n"
                f"✅ **Take Profit 1:** {sig['tp1']}\n"
