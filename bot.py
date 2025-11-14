import telebot
import sqlite3
import re

# ================================
# CONFIG
# ================================
TOKEN = "YOUR_BOT_TOKEN"
OWNER_ID = "123456789"     # এখানে নিজের Telegram user_id দেবে
TARGET_GROUP_ID = -100123456789  # যেই গ্রুপে অ্যাড করবে

bot = telebot.TeleBot(TOKEN)

# ================================
# DATABASE
# ================================
conn = sqlite3.connect("bot_data.sqlite3", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS settings(
    id INTEGER PRIMARY KEY,
    allowed_link TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS users(
    username TEXT PRIMARY KEY,
    user_id INTEGER
)
""")

conn.commit()

# যদি লিংক না থাকে → খালি অ্যাড করে
c.execute("INSERT OR IGNORE INTO settings(id, allowed_link) VALUES(1,'')")
conn.commit()


# ================================
# COMMAND: Save user info
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
# COMMAND: Set Allowed Link (Owner Only)
# ================================
@bot.message_handler(commands=['setlink'])
def set_link(message):
    if str(message.from_user.id) != OWNER_ID:
        return bot.reply_to(message, "❌ আপনি এই কমান্ড ব্যবহার করতে পারবেন না!")

    try:
        new_link = message.text.split()[1]
    except:
        return bot.reply_to(message, "ব্যবহার:\n/setlink https://example.com")

    c.execute("UPDATE settings SET allowed_link = ? WHERE id=1", (new_link,))
    conn.commit()

    bot.reply_to(message, f"✔ নতুন অনুমোদিত লিংক সেট করা হয়েছে:\n{new_link}")


# ================================
# COMMAND: Add user to group by username
# ================================
@bot.message_handler(commands=['adduser'])
def add_user(message):
    if str(message.from_user.id) != OWNER_ID:
        return bot.reply_to(message, "❌ শুধু ওনার ব্যবহার করতে পারবেন!")

    try:
        username = message.text.split()[1].replace("@", "").lower()
    except:
        return bot.reply_to(message, "ব্যবহার:\n/adduser @username")

    c.execute("SELECT user_id FROM users WHERE username=?", (username,))
    result = c.fetchone()

    if not result:
        return bot.reply_to(message,
                            "❌ এই ইউজারের user_id পাওয়া যায়নি!\n"
                            "উনাকে আগে বটকে /start করতে বলুন।")

    user_id = result[0]

    try:
        bot.add_chat_members(TARGET_GROUP_ID, user_id)
        bot.reply_to(message, f"✔ @{username} গ্রুপে অ্যাড হয়েছে!")
    except Exception as e:
        bot.reply_to(message, f"❌ অ্যাড করা যায়নি!\n{e}")


# ================================
# AUTO LINK FILTER SYSTEM
# ================================
@bot.message_handler(func=lambda m: True, content_types=['text'])
def link_filter(message):
    # ওনারকে লিংক দিলে কোনো মেসেজ দেবে না
    if str(message.from_user.id) == OWNER_ID:
        return

    # লিংক আছে কিনা চেক
    urls = re.findall(r'(https?://\S+)', message.text)

    if not urls:
        return  # লিংক নাই → কিছু করো না

    # ডাটাবেজ থেকে allowed_link আনো
    c.execute("SELECT allowed_link FROM settings WHERE id=1")
    allowed_link = c.fetchone()[0]

    for link in urls:
        if allowed_link and allowed_link in link:
            return  # ঠিক লিংক → কিছু করো না
        else:
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

            bot.send_message(message.chat.id,
                             f"❌ শুধুমাত্র এই লিংক অনুমোদিত:\n{allowed_link}")
            return


# ================================
# RUN
# ================================
print("Bot Running...")
bot.infinity_polling()
