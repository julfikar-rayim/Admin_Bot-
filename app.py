from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
import re
import os

# ⚙️ তোমার বটের টোকেন (ফাঁকা রাখো, নিচে সেট করবে)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ✅ তোমার অনুমোদিত ডোমেইন
ALLOWED_DOMAIN = "julfikar.me"

# 🔍 লিংক খোঁজার প্যাটার্ন
LINK_REGEX = r"(https?://[^\s]+)"

async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    message_text = update.message.text
    user = update.effective_user

    # লিংক খোঁজো
    links = re.findall(LINK_REGEX, message_text)
    if not links:
        return

    for link in links:
        if ALLOWED_DOMAIN not in link:
            try:
                await update.message.delete()
            except Exception:
                pass

            try:
                await context.bot.ban_chat_member(update.message.chat_id, user.id)
            except Exception:
                pass

            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="🚫 এই গ্রুপে কেউ লিংক শেয়ার দিবেন না!"
            )
            return

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ আমি এখন গ্রুপে লিংক মনিটর করছি!")

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN সেট করা হয়নি!")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.COMMAND & filters.ChatType.GROUPS, start))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, check_links))

    print("🚀 বট চালু হয়েছে!")
    app.run_polling()

if __name__ == "__main__":
    main()
