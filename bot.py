import os
import logging
import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

print(f"TELEGRAM VERSION: {telegram.__version__}")

TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is alive 🌲 Forest Signal Bot v1")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("BOT IS RUNNING AND POLLING!!!")
    app.run_polling()

if __name__ == "__main__":
    main()
