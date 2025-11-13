import os
import re
import sqlite3
from urllib.parse import urlparse
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ---------------- Environment ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # @Over_sure numeric ID
ALLOWED_DOMAINS = [d.strip().lower() for d in os.environ.get("ALLOWED_DOMAINS", "julfikar.me").split(",") if d.strip()]
ALLOWED_CHAT_IDS = [int(x.strip()) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()]
DB_PATH = os.environ.get("DB_PATH", "bot_data.sqlite3")

LINK_REGEX = r"(https?://[^\s]+)"

# ---------------- Owner online detection ----------------
OWNER_STATUS = {"online": False, "last_seen": None}
OWNER_TIMEOUT = timedelta(minutes=5)

def update_owner_online():
    OWNER_STATUS["online"] = True
    OWNER_STATUS["last_seen"] = datetime.now()

def check_owner_online():
    if not OWNER_STATUS["online"]:
        return False
    if OWNER_STATUS["last_seen"] and datetime.now() - OWNER_STATUS["last_seen"] > OWNER_TIMEOUT:
        OWNER_STATUS["online"] = False
        return False
    return True

# ---------------- Database ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bans (
        user_id INTEGER PRIMARY KEY,
        reason TEXT,
        banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def ban_user_db(user_id: int, reason=""):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO bans(user_id, reason) VALUES (?, ?)", (user_id, reason))
    conn.commit()
    conn.close()

def unban_user_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_banned(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM bans WHERE user_id = ?", (user_id,))
    res = cur.fetchone()
    conn.close()
    return bool(res)

# ---------------- Helpers ----------------
def extract_domains(text: str):
    found = re.findall(LINK_REGEX, text or "")
    domains = []
    for link in found:
        try:
            p = urlparse(link)
            hostname = (p.hostname or "").lower()
            if hostname.startswith("www."):
                hostname = hostname[4:]
            domains.append(hostname)
        except Exception:
            continue
    return domains

def allowed_chat(chat_id: int) -> bool:
    if ALLOWED_CHAT_IDS:
        return chat_id in ALLOWED_CHAT_IDS
    return True

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

# ---------------- Handlers ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        return
    update_owner_online()
    await update.message.reply_text("✅ আমি গ্রুপে লিংক মনিটর করছি।")

async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        if not check_owner_online():
            try:
                await update.message.reply_text("⚠️ Owner এখন অফলাইনে আছেন। আপনার মেসেজ পরে দেখবেন।")
                await update.message.delete()
            except Exception:
                pass
        return

    chat_id = update.effective_chat.id
    if not allowed_chat(chat_id):
        return

    text = update.message.text or update.message.caption or ""
    if not text:
        return

    domains = extract_domains(text)
    for dom in domains:
        if dom not in ALLOWED_DOMAINS:
            user = update.effective_user
            display = user.username and f"@{user.username}" or f"{user.first_name or 'User'}"
            group_title = update.effective_chat.title or "এই গ্রুপ"

            try:
                # গ্রুপে মেসেজ
                await update.message.delete()
                await context.bot.ban_chat_member(chat_id, user.id)
                ban_user_db(user.id, reason=f"Shared disallowed link: {dom}")
                await context.bot.send_message(chat_id=chat_id,
                    text=f"🚫 {display} কে অননুমোদিত লিংক শেয়ারের জন্য '{group_title}' গ্রুপ থেকে সরানো হয়েছে।"
                )
                # ইউজারের ইনবক্সে মেসেজ
                await context.bot.send_message(chat_id=user.id,
                    text=f"⚠️ আপনি '{group_title}' গ্রুপে অননুমোদিত লিংক দেওয়ার কারণে গ্রুপ থেকে সরানো হয়েছে।\n🔹 গ্রুপে আবার যোগ হতে এডমিনের সাথে যোগাযোগ করুন।"
                )
            except Exception:
                pass
            return

async def new_member_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not allowed_chat(chat_id):
        return
    for member in update.message.new_chat_members or []:
        if is_banned(member.id):
            try:
                await update.message.delete()
                await context.bot.ban_chat_member(chat_id, member.id)
                display = member.username and f"@{member.username}" or f"{member.first_name or 'User'}"
                await context.bot.send_message(chat_id=chat_id,
                    text=f"🚫 {display} কে আগেই ব্যান করা আছে।"
                )
            except: pass

# ---------------- Owner Commands ----------------
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("তুমি মালিক নও।")
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /ban <user_id>")
        return
    try: target = int(context.args[0])
    except: await update.message.reply_text("User ID numeric হতে হবে."); return
    ban_user_db(target, reason="manual by owner")
    await update.message.reply_text(f"User {target} কে ব্যান করা হলো।")

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("তুমি মালিক নও।")
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /unban <user_id>")
        return
    try: target = int(context.args[0])
    except: await update.message.reply_text("User ID numeric হতে হবে."); return
    unban_user_db(target)
    try: await context.bot.unban_chat_member(update.effective_chat.id, target)
    except: pass
    await update.message.reply_text(f"User {target} কে আনব্যান করা হলো।")

async def add_domain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not context.args: return
    new = context.args[0].lower().strip()
    if new.startswith("www."): new = new[4:]
    if new not in ALLOWED_DOMAINS: ALLOWED_DOMAINS.append(new)
    await update.message.reply_text(f"{new} added.\nCurrent: {', '.join(ALLOWED_DOMAINS)}")

async def remove_domain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not context.args: return
    rem = context.args[0].lower().strip()
    if rem.startswith("www."): rem = rem[4:]
    if rem in ALLOWED_DOMAINS: ALLOWED_DOMAINS.remove(rem)
    await update.message.reply_text(f"{rem} removed.\nCurrent: {', '.join(ALLOWED_DOMAINS)}")

async def list_domains_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text("Allowed domains:\n" + "\n".join(ALLOWED_DOMAINS))

# ---------------- Get Chat ID Command ----------------
async def get_chat_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"এই গ্রুপের Chat ID:\n{chat_id}")

# ---------------- Main ----------------
def main():
    if not BOT_TOKEN or not OWNER_ID:
        print("BOT_TOKEN বা OWNER_ID নেই")
        return

    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("add_domain", add_domain_cmd))
    app.add_handler(CommandHandler("remove_domain", remove_domain_cmd))
    app.add_handler(CommandHandler("list_domains", list_domains_cmd))
    app.add_handler(CommandHandler("get_chat_id", get_chat_id_cmd))

    # Handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_check))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND) & filters.ChatType.GROUPS, check_links))

    print("🚀 Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
