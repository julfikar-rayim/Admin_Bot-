import os
import re
import sqlite3
import telebot

# ================================
# Environment Variables
# ================================
TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
ALLOWED_LINKS = os.environ.get("ALLOWED_LINKS", "").split(",")  # comma separated
ALLOWED_CHAT_IDS = [int(cid) for cid in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")]
DB_PATH = os.environ.get("DB_PATH", "bot_data.sqlite3")

bot = telebot.TeleBot(TOKEN)

# ================================
# DATABASE SETUP
# ================================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()

# Users table: store username → user_id
c.execute("""
CREATE TABLE IF NOT EXISTS users(
    username TEXT PRIMARY KEY,
    user_id INTEGER
)
""")
conn.commit()

# ================================
# /start COMMAND - Save user info
# ================================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    username = message.from_user.username
    user_id = message.from_user.id
    if username:
        c.execute("INSERT OR REPLACE INTO users (username, user_id) VALUES (?,?)",
                  (username.lower(), user_id))
        conn.commit()
    bot.reply_to(message, "✅ আপনার তথ্য সেভ করা হয়েছে!")

# ================================
# /setlink COMMAND (Owner Only)
# ================================
@bot.message_handler(commands=['setlink'])
def set_link(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "❌ আপনি এই কমান্ড ব্যবহার করতে পারবেন না!")
    try:
        new_link = message.text.split()[1]
    except IndexError:
        return bot.reply_to(message, "ব্যবহার:\n/setlink https://example.com")
    global ALLOWED_LINKS
    ALLOWED_LINKS = [new_link.strip()]
    bot.reply_to(message, f"✔ অনুমোদিত লিংক আপডেট হয়েছে:\n{new_link}")

# ================================
# /adduser COMMAND - Add user by username
# ================================
@bot.message_handler(commands=['adduser'])
def add_user(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "❌ শুধু Owner ব্যবহার করতে পারবেন!")
    try:
        username = message.text.split()[1].replace("@", "").lower()
    except IndexError:
        return bot.reply_to(message, "ব্যবহার:\n/adduser @username")
    c.execute("SELECT user_id FROM users WHERE username=?", (username,))
    result = c.fetchone()
    if not result:
        return bot.reply_to(message, "❌ ইউজার পাওয়া যায়নি! /start করতে বলুন।")
    user_id = result[0]
    try:
        for chat_id in ALLOWED_CHAT_IDS:
            bot.add_chat_members(chat_id, user_id)
        bot.reply_to(message, f"✔ @{username} গ্রুপে অ্যাড হয়েছে!")
    except Exception as e:
        bot.reply_to(message, f"❌ অ্যাড করা যায়নি!\n{e}")

# ================================
# LINK FILTER SYSTEM
# ================================
@bot.message_handler(func=lambda m: True, content_types=['text'])
def link_filter(message):
    # Only monitor specified groups
    if message.chat.id not in ALLOWED_CHAT_IDS:
        return
    # Skip owner
    if message.from_user.id == OWNER_ID:
        return
    # Find links in message
    urls = re.findall(r'(https?://\S+)', message.text)
    if not urls:
        return
    for link in urls:
        if any(link.startswith(allowed) for allowed in ALLOWED_LINKS):
            continue  # allowed link
        else:
            # Delete message
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass
            bot.send_message(message.chat.id,
                             f"❌ শুধুমাত্র এই লিংক অনুমোদিত:\n{', '.join(ALLOWED_LINKS)}")

# ================================
# RUN BOT
# ================================
print("Bot Running...")
bot.infinity_polling()
