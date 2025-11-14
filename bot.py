# bot.py
import os
import re
import sqlite3
from urllib.parse import urlparse
from datetime import datetime, timedelta

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG (Environment variables) ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
# comma separated domains, e.g. "julfikar.me,example.com"
ALLOWED_DOMAINS = {d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS", "julfikar.me").split(",") if d.strip()}
# comma separated chat ids as numbers or strings, store as set of strings for safe compare
ALLOWED_CHAT_IDS = {x.strip() for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip()}
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")

LINK_REGEX = r"(https?://[^\s]+)"
OWNER_TIMEOUT_MIN = int(os.getenv("OWNER_TIMEOUT_MIN", "5"))

# ---------------- Owner online (simple) ----------------
OWNER_STATUS = {"online": False, "last_seen": None}
OWNER_TIMEOUT = timedelta(minutes=OWNER_TIMEOUT_MIN)


def mark_owner_online():
    OWNER_STATUS["online"] = True
    OWNER_STATUS["last_seen"] = datetime.now()


def owner_is_online():
    if not OWNER_STATUS["online"]:
        return False
    if OWNER_STATUS["last_seen"] and (datetime.now() - OWNER_STATUS["last_seen"]) > OWNER_TIMEOUT:
        OWNER_STATUS["online"] = False
        return False
    return True


# ---------------- Database helpers ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY, reason TEXT, banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()


def ban_user_db(user_id: int, reason: str = ""):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO banned_users (user_id, reason) VALUES (?, ?)", (user_id, reason))
    conn.commit()
    conn.close()


def unban_user_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def is_banned(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
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


def chat_allowed(chat_id: int) -> bool:
    # if ALLOWED_CHAT_IDS is empty => allow all groups
    if not ALLOWED_CHAT_IDS:
        return True
    return str(chat_id) in ALLOWED_CHAT_IDS


def owner_check(user_id: int) -> bool:
    return user_id == OWNER_ID and OWNER_ID != 0


async def resolve_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE, user_ref: str):
    """
    Accepts numeric id OR @username OR username (without @)
    Returns numeric user_id or None
    """
    if not user_ref:
        return None
    user_ref = user_ref.strip()
    if user_ref.isdigit():
        return int(user_ref)
    if user_ref.startswith("@"):
        user_ref = user_ref[1:]
    try:
        # try to fetch by username
        chat = await context.bot.get_chat(f"@{user_ref}")
        return chat.id
    except Exception:
        # last resort: if command used in group and user_ref is username, try chat_member
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, user_ref)
            return member.user.id
        except Exception:
            return None


# ---------------- Handlers ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # owner activity marks online
    if update.effective_user and owner_check(update.effective_user.id):
        mark_owner_online()
        await update.message.reply_text("✅ Owner activity noted. বট চালু আছে।")
    else:
        # do not reply in private (as requested) — but in group allow /start to confirm
        if update.effective_chat and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            await update.message.reply_text("আমি গ্রুপে লিংক মনিটর করছি ✅")


async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ignore non-group text (except we handle private -> owner online check)
    chat = update.effective_chat
    user = update.effective_user
    text = (update.message.text or update.message.caption or "").strip()

    # PRIVATE chat behavior: if owner offline, delete message and warn
    if chat.type == ChatType.PRIVATE:
        # do not respond to owner (owner control messages mark online)
        if owner_check(user.id):
            mark_owner_online()
            # don't reply to owner in private (silent)
            return
        if not owner_is_online():
            try:
                await update.message.reply_text("⚠️ Owner এখন অফলাইনে আছেন — আপনার মেসেজ পরে দেখানো হবে।")
                await update.message.delete()
            except Exception:
                pass
        return

    # only handle groups
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    # is this group allowed?
    if not chat_allowed(chat.id):
        return

    # if user is banned in DB, enforce immediate removal
    if is_banned(user.id):
        try:
            await context.bot.ban_chat_member(chat.id, user.id)  # ensure removed
            await context.bot.unban_chat_member(chat.id, user.id)  # allow rejoin only by owner/add
        except Exception:
            pass
        return

    if not text:
        return

    domains = extract_domains(text)
    if not domains:
        return

    for dom in domains:
        if dom not in ALLOWED_DOMAINS:
            # offending link found
            display = f"@{user.username}" if user.username else (user.full_name if hasattr(user, "full_name") else user.first_name or "User")
            group_title = chat.title or "এই গ্রুপ"
            admin_user = update.effective_user  # the person who sent the message (not necessarily admin)
            # find admin who triggered? we consider OWNER as admin for message
            try:
                bot_me = await context.bot.get_me()
                admin_name_for_msg = bot_me.username or "Admin"
            except:
                admin_name_for_msg = "Admin"

            try:
                # delete message
                await update.message.delete()
            except Exception:
                pass

            try:
                # ban (kick) user (ban then unban pattern to just kick)
                await context.bot.ban_chat_member(chat.id, user.id)
                await context.bot.unban_chat_member(chat.id, user.id)
                # insert to DB as banned record so rejoin blocked until owner unbans via command
                ban_user_db(user.id, reason=f"Shared disallowed domain: {dom}")
            except Exception:
                pass

            # group notification
            try:
                await context.bot.send_message(chat_id=chat.id,
                                               text=f"🚫 {display} কে অননুমোদিত লিংক শেয়ারের জন্য '{group_title}' থেকে সরানো হয়েছে।")
            except Exception:
                pass

            # personal inbox to the kicked user
            try:
                await context.bot.send_message(chat_id=user.id,
                                               text=(f"⚠️ আপনি '{group_title}' গ্রুপে অননুমোদিত লিংক দেওয়ার কারণে গ্রুপ থেকে সরানো হয়েছেন।\n"
                                                     f"👤 Admin: @{admin_name_for_msg}\n"
                                                     "🔹 আবার যোগ হতে গ্রুপের এডমিনের সাথে যোগাযোগ করুন।"))
            except Exception:
                # user might have privacy settings — ignore
                pass

            return  # stop after first offending domain


async def new_member_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # when new member joins, if banned in DB => remove immediately
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not chat_allowed(chat.id):
        return

    new_members = update.message.new_chat_members or []
    for m in new_members:
        if is_banned(m.id):
            try:
                await update.message.delete()
            except:
                pass
            try:
                await context.bot.ban_chat_member(chat.id, m.id)
                await context.bot.unban_chat_member(chat.id, m.id)
            except:
                pass
            try:
                name = f"@{m.username}" if m.username else (m.full_name if hasattr(m, "full_name") else m.first_name or "User")
                await context.bot.send_message(chat.id, text=f"🚫 {name} কে আগেই ব্যান করা আছে — যোগ দেওয়া যাবে না।")
            except:
                pass


# ---------------- Commands (owner-only) ----------------
async def cmd_get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        await update.message.reply_text("তুমি মালিক নও।")
        return
    await update.message.reply_text(f"এই গ্রুপের Chat ID:\n{update.effective_chat.id}")


async def cmd_add_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /add_domain example.com")
        return
    d = context.args[0].lower().strip()
    if d.startswith("www."):
        d = d[4:]
    ALLOWED_DOMAINS.add(d)
    await update.message.reply_text(f"✅ {d} added. Current: {', '.join(sorted(ALLOWED_DOMAINS))}")


async def cmd_remove_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /remove_domain example.com")
        return
    d = context.args[0].lower().strip()
    if d.startswith("www."):
        d = d[4:]
    if d in ALLOWED_DOMAINS:
        ALLOWED_DOMAINS.remove(d)
        await update.message.reply_text(f"✅ {d} removed. Current: {', '.join(sorted(ALLOWED_DOMAINS))}")
    else:
        await update.message.reply_text(f"{d} তালিকায় নেই।")


async def cmd_list_domains(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        return
    await update.message.reply_text("Allowed domains:\n" + "\n".join(sorted(ALLOWED_DOMAINS)))


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /ban <user_id বা @username>")
        return
    target_ref = context.args[0]
    target_id = await resolve_user_id(update, context, target_ref)
    if not target_id:
        await update.message.reply_text("User খুঁজে পাওয়া যায়নি।")
        return
    ban_user_db(target_id, reason="manual by owner")
    try:
        # also try to remove from chat where command was executed
        await context.bot.ban_chat_member(update.effective_chat.id, target_id)
        await context.bot.unban_chat_member(update.effective_chat.id, target_id)
    except:
        pass
    await update.message.reply_text(f"✅ User {target_id} কে ban করা হয়েছে।")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /unban <user_id বা @username>")
        return
    target_ref = context.args[0]
    target_id = await resolve_user_id(update, context, target_ref)
    if not target_id:
        await update.message.reply_text("User খুঁজে পাওয়া যায়নি।")
        return
    unban_user_db(target_id)
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, target_id)
    except:
        pass
    await update.message.reply_text(f"✅ User {target_id} কে unban করা হয়েছে।")


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /kick <user_id বা @username>")
        return
    target_ref = context.args[0]
    target_id = await resolve_user_id(update, context, target_ref)
    if not target_id:
        await update.message.reply_text("User খুঁজে পাওয়া যায়নি।")
        return

    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target_id)
        await context.bot.unban_chat_member(update.effective_chat.id, target_id)
        ban_user_db(target_id, reason="kicked by owner")
    except:
        pass

    admin_username = update.effective_user.username or "Admin"
    group_title = update.effective_chat.title or "এই গ্রুপ"

    # notify kicked user
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(f"⚠️ আপনি '{group_title}' গ্রুপ থেকে কিক করা হয়েছেন।\n"
                  f"👤 Admin: @{admin_username}\n"
                  "🔹 আবার যোগ হতে এডমিনের সাথে যোগাযোগ করুন।")
        )
    except:
        pass

    await update.message.reply_text(f"✅ User {target_id} কে কিক করা হয়েছে।")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_check(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /add <user_id বা @username>")
        return
    target_ref = context.args[0]
    target_id = await resolve_user_id(update, context, target_ref)
    if not target_id:
        await update.message.reply_text("User খুঁজে পাওয়া যায়নি।")
        return
    # remove from banned DB so owner can add manually
    unban_user_db(target_id)
    await update.message.reply_text(f"✅ User {target_id} কে এখন গ্রুপে যোগ করা যাবে (owner מחדש যোগ করুন)।")


async def cmd_set_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_ID
    if not owner_check(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: /set_owner <numeric_user_id>")
        return
    try:
        new_owner = int(context.args[0])
        OWNER_ID = new_owner
        await update.message.reply_text(f"✅ নতুন Owner সেট করা হয়েছে: {OWNER_ID}")
    except:
        await update.message.reply_text("numeric user id দিন অনুগ্রহ করে।")


# ---------------- Main runner ----------------
def main():
    if not BOT_TOKEN or OWNER_ID == 0:
        print("Error: BOT_TOKEN অথবা OWNER_ID সেট করা নেই। Environment variables চেক করুন।")
        return

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("get_chat_id", cmd_get_chat_id))
    app.add_handler(CommandHandler("add_domain", cmd_add_domain))
    app.add_handler(CommandHandler("remove_domain", cmd_remove_domain))
    app.add_handler(CommandHandler("list_domains", cmd_list_domains))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("kick", cmd_kick))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("set_owner", cmd_set_owner))

    # message handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_check))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_links))

    print("🚀 Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
