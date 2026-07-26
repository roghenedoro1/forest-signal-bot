import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import yfinance as yf

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is live! Forest Signal Bot 🌲")

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN not found. Add it in Render Environment Variables")
    
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("BOT IS RUNNING AND POLLING!!!")
    app.run_polling()

if __name__ == '__main__':
    main()
