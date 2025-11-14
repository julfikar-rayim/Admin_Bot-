import os
import re
import sqlite3
from telegram import Update, ChatPermissions
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# -------------------------
# ENVIRONMENT VARIABLES
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
ALLOWED_DOMAINS = set(os.getenv("ALLOWED_DOMAINS", "").split(","))
ALLOWED_CHAT_IDS = set(os.getenv("ALLOWED_CHAT_IDS", "").split(","))
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")


# -------------------------
# DATABASE INIT
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


def ban_user_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO banned_users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def unban_user_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def is_banned(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


# -------------------------
# OWNER CHECK HELP
# -------------------------
def is_owner(user_id):
    return user_id == OWNER_ID


# -------------------------
# Resolve username to ID
# -------------------------
async def resolve_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_ref):
    if user_ref.isdigit():
        return int(user_ref)

    if user_ref.startswith("@"):
        username = user_ref[1:]
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, username)
            return member.user.id
        except:
            return None

    return None


# -------------------------
# COMMAND: /set_owner
# -------------------------
async def set_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_ID

    if not is_owner(update.effective_user.id):
        return

    if len(context.args) != 1:
        return await update.message.reply_text("ব্যবহার: /set_owner <USER_ID>")

    new_owner = int(context.args[0])
    OWNER_ID = new_owner
    await update.message.reply_text(f"✅ নতুন Owner সেট করা হয়েছে: {new_owner}")


# -------------------------
# COMMAND: /ban
# -------------------------
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    
    if not context.args:
        return await update.message.reply_text("ব্যবহার: /ban <user_id বা @username>")

    user_ref = context.args[0]
    target_id = await resolve_user(update, context, user_ref)

    if not target_id:
        return await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")

    ban_user_db(target_id)
    await update.message.reply_text(f"🚫 User {target_id} ban করা হলো।")


# -------------------------
# COMMAND: /unban
# -------------------------
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    if not context.args:
        return await update.message.reply_text("ব্যবহার: /unban <user_id>")

    user_ref = context.args[0]
    target_id = await resolve_user(update, context, user_ref)

    if not target_id:
        return await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")

    unban_user_db(target_id)
    await update.message.reply_text(f"✅ User {target_id} unban করা হলো।")


# -------------------------
# COMMAND: /kick
# -------------------------
async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    if not context.args:
        return await update.message.reply_text("ব্যবহার: /kick <user_id বা @username>")

    user_ref = context.args[0]
    target_id = await resolve_user(update, context, user_ref)

    if not target_id:
        return await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")

    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target_id)
        await context.bot.unban_chat_member(update.effective_chat.id, target_id)

        admin_username = update.effective_user.username or "UnknownAdmin"
        group_name = update.effective_chat.title or "এই গ্রুপ"

        # Send inbox message
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"⚠️ আপনি '{group_name}' গ্রুপ থেকে সরিয়ে দেওয়া হয়েছে।\n"
                     f"👤 Admin: @{admin_username}\n"
                     f"🔹 সমস্যার জন্য এডমিনের সাথে যোগাযোগ করুন।"
            )
        except:
            pass

        await update.message.reply_text(f"🚫 {target_id} গ্রুপ থেকে কিক করা হলো।")

    except:
        await update.message.reply_text("❌ কিক করা সম্ভব হয়নি।")


# -------------------------
# COMMAND: /add (re add user)
# -------------------------
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    if not context.args:
        return await update.message.reply_text("ব্যবহার: /add <user_id বা @username>")

    user_ref = context.args[0]
    target_id = await resolve_user(update, context, user_ref)

    if not target_id:
        return await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")

    unban_user_db(target_id)

    await update.message.reply_text(f"✅ {target_id} এখন গ্রুপে add করা যাবে।")


# -------------------------
# DOMAIN CHECK — auto kick
# -------------------------
async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "supergroup":
        return

    if str(update.effective_chat.id) not in ALLOWED_CHAT_IDS:
        return

    user = update.effective_user
    text = update.message.text or ""

    # banned user = auto kick
    if is_banned(user.id):
        try:
            await context.bot.ban_chat_member(update.effective_chat.id, user.id)
            await context.bot.unban_chat_member(update.effective_chat.id, user.id)
        except:
            pass
        return

    # detect URLs
    urls = re.findall(r'https?://[^\s]+', text)
    if not urls:
        return

    for url in urls:
        valid = False
        for domain in ALLOWED_DOMAINS:
            if domain in url:
                valid = True
                break

        if valid:
            continue

        # Auto kick
        try:
            await context.bot.ban_chat_member(update.effective_chat.id, user.id)
            await context.bot.unban_chat_member(update.effective_chat.id, user.id)

            admin_username = context.bot.username
            group_title = update.effective_chat.title or "এই গ্রুপ"

            await update.message.reply_text(f"🚫 @{user.username} কে অননুমোদিত লিংক শেয়ারের জন্য কিক করা হয়েছে।")

            # inbox msg
            try:
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"⚠️ আপনি '{group_title}' গ্রুপে ভুল লিংক দেওয়ার কারণে কিক হয়েছেন।\n"
                         f"👤 Admin: @{admin_username}\n"
                         f"🔹 আবার যোগ হতে এডমিনের সাথে যোগাযোগ করুন।"
                )
            except:
                pass

        except:
            pass


# -------------------------
# MAIN
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update.effective_user.id):
        await update.message.reply_text("🤖 বট চলছে! আপনি মালিক।")
    else:
        pass


def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("set_owner", set_owner))

    app.add_handler(MessageHandler(filters.TEXT, check_links))

    app.run_polling()


if __name__ == "__main__":
    main()
