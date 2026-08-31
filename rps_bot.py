"""
بات سنگ‌کاغذقیچی برای تلگرام — نسخه با ذخیره امتیاز و پنل ادمین
------------------------------------------------------------------
امکانات:
  1) بازی تکی در برابر خود بات (فوری)
  2) چالش با یک دوست: هرکس توی پیوی خودش انتخابش رو می‌زنه، نتیجه
     وقتی هر دو انتخاب کردن هم‌زمان اعلام می‌شه.
  3) امتیازها (برد/باخت/مساوی هر کاربر) توی یک فایل SQLite ذخیره
     می‌شن و با ری‌استارت شدن بات از بین نمی‌رن.
  4) پنل ادمین (/admin): آمار کلی ربات + ارسال پیام همگانی به همه
     کاربرهایی که تا حالا با بات استارت زدن.

نحوه‌ی اجرا:
  1) از @BotFather یه بات بساز و توکنش رو بگیر.
  2) آیدی عددی خودت رو از یه بات مثل @userinfobot بگیر.
  3) pip install -r requirements.txt
  4) متغیرهای محیطی رو ست کن:
        export TOKEN="123456:ABC-your-token"
        export ADMIN_IDS="111111,222222"     # آیدی عددی ادمین‌ها، با کاما جدا
  5) python rps_bot.py

نکته: این اسکریپت باید مدام روی یه سرور/وی‌پی‌اس (یا Railway/Render/
PythonAnywhere always-on) در حال اجرا بمونه؛ با بسته‌شدن اسکریپت بات
آفلاین می‌شه. فایل rps.db کنار اسکریپت ساخته می‌شه و امتیازها و لیست
کاربران توش نگه داشته می‌شه — این فایل رو موقع جابه‌جایی سرور حتماً
با خودت منتقل کن تا امتیازها از بین نرن.
"""

import os
import random
import sqlite3
import uuid
import logging
from contextlib import closing

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rps-bot")

TOKEN = os.environ.get("TOKEN", "8978449695:AAH-UC8RwuX2NuM_sP9u7ZbHGsCAliTyF74")
ADMIN_IDS = {int(x) for x in os.environ.get("7689823397", "").split(",") if x.strip().isdigit()}
DB_PATH = os.environ.get("DB_PATH", "rps.db")

# به‌جای ایموجی دست، از خودِ عناصر بازی استفاده می‌کنیم
CHOICES = {
    "rock":     {"label": "سنگ",  "emoji": "🪨", "beats": "scissors"},
    "paper":    {"label": "کاغذ", "emoji": "📄", "beats": "rock"},
    "scissors": {"label": "قیچی", "emoji": "✂️", "beats": "paper"},
}

DUELS: dict[str, dict] = {}


# ------------------------------------------------------------------ DB ------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                chat_id     INTEGER NOT NULL,
                first_name  TEXT,
                wins        INTEGER NOT NULL DEFAULT 0,
                losses      INTEGER NOT NULL DEFAULT 0,
                ties        INTEGER NOT NULL DEFAULT 0,
                joined_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('games_played', 0)")


def upsert_user(user_id: int, chat_id: int, first_name: str):
    with closing(db()) as conn, conn:
        conn.execute("""
            INSERT INTO users(user_id, chat_id, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET chat_id = excluded.chat_id,
                                                first_name = excluded.first_name
        """, (user_id, chat_id, first_name))


def record_result(user_id: int, result: str):
    column = {"win": "wins", "lose": "losses", "tie": "ties"}[result]
    with closing(db()) as conn, conn:
        conn.execute(f"UPDATE users SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))


def bump_games_played():
    with closing(db()) as conn, conn:
        conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'games_played'")


def get_stats(user_id: int):
    with closing(db()) as conn:
        row = conn.execute("SELECT wins, losses, ties FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return (row["wins"], row["losses"], row["ties"]) if row else (0, 0, 0)


def get_all_chat_ids() -> list[int]:
    with closing(db()) as conn:
        return [r["chat_id"] for r in conn.execute("SELECT chat_id FROM users")]


def count_users() -> int:
    with closing(db()) as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def count_games_played() -> int:
    with closing(db()) as conn:
        return conn.execute("SELECT value FROM meta WHERE key = 'games_played'").fetchone()["value"]


# --------------------------------------------------------------- helpers ----

def choice_keyboard(prefix: str) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(f"{c['emoji']} {c['label']}", callback_data=f"{prefix}:{key}")
        for key, c in CHOICES.items()
    ]
    return InlineKeyboardMarkup([row])


def judge(a: str, b: str) -> str:
    if a == b:
        return "tie"
    return "win" if CHOICES[a]["beats"] == b else "lose"


def stats_line(user_id: int) -> str:
    wins, losses, ties = get_stats(user_id)
    return f"امتیاز کلی تو: {wins} برد، {losses} باخت، {ties} مساوی"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ---------------------------------------------------------------- /start ----

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    upsert_user(user.id, chat_id, user.first_name)

    args = context.args
    if args and args[0].startswith("duel_"):
        await join_duel(update, context, args[0].removeprefix("duel_"))
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 بازی با ربات", callback_data="solo:menu")],
        [InlineKeyboardButton("👥 چالش با یک دوست", callback_data="challenge:new")],
        [InlineKeyboardButton("📊 امتیاز من", callback_data="stats:me")],
    ])
    await update.message.reply_text(
        "سنگ، کاغذ، قیچی 🪨📄✂️\n"
        "می‌تونی همین‌جا با خود من بازی کنی، یا یه لینک چالش برای یه دوست بفرستی "
        "و هرکدومتون توی پیوی خودتون انتخاب کنید. امتیازهات ذخیره می‌مونه.",
        reply_markup=keyboard,
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(stats_line(update.effective_user.id))


# ------------------------------------------------------------ حالت تکی ------

async def solo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("انتخابتو بزن:", reply_markup=choice_keyboard("solo"))


async def solo_play(update: Update, context: ContextTypes.DEFAULT_TYPE, choice: str):
    query = update.callback_query
    user = query.from_user
    upsert_user(user.id, query.message.chat_id, user.first_name)

    bot_choice = random.choice(list(CHOICES))
    result = judge(choice, bot_choice)
    record_result(user.id, result)
    bump_games_played()

    verdict = {
        "win":  f"{CHOICES[choice]['emoji']} {CHOICES[choice]['label']} می‌بره "
                f"{CHOICES[bot_choice]['emoji']} {CHOICES[bot_choice]['label']} رو — بردی! 🎉",
        "lose": f"{CHOICES[bot_choice]['emoji']} {CHOICES[bot_choice]['label']} می‌بره "
                f"{CHOICES[choice]['emoji']} {CHOICES[choice]['label']} رو — باختی.",
        "tie":  f"هر دو {CHOICES[choice]['emoji']} {CHOICES[choice]['label']} زدید — مساوی شد.",
    }[result]

    text = f"{verdict}\n\n{stats_line(user.id)}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 دوباره بازی کن", callback_data="solo:menu")],
        [InlineKeyboardButton("👥 چالش با یک دوست", callback_data="challenge:new")],
    ])
    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


# --------------------------------------------------------- ساخت چالش --------

async def new_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    code = uuid.uuid4().hex[:8]
    DUELS[code] = {
        "p1_id": user.id, "p1_name": user.first_name, "p1_chat": query.message.chat_id,
        "p2_id": None, "p2_name": None, "p2_chat": None,
        "p1_choice": None, "p2_choice": None,
    }

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=duel_{code}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 ارسال لینک چالش", switch_inline_query=f"بیا سنگ‌کاغذقیچی بازی کنیم! {link}")],
    ])
    await query.edit_message_text(
        "لینک چالش ساخته شد. اینو برای دوستت بفرست؛ به‌محض این‌که روش بزنه و بات رو استارت کنه، "
        "هر دو نفر دکمه‌های انتخاب رو می‌گیرید:\n\n" + link,
        reply_markup=keyboard,
    )


async def join_duel(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    user = update.effective_user
    duel = DUELS.get(code)

    if duel is None:
        await update.message.reply_text("این لینک چالش دیگه معتبر نیست یا قبلاً استفاده شده.")
        return
    if duel["p1_id"] == user.id:
        await update.message.reply_text("این لینک چالش خودته — باید یه نفر دیگه روش بزنه 🙂")
        return
    if duel["p2_id"] is not None:
        await update.message.reply_text("این چالش قبلاً توسط یه نفر دیگه پر شده.")
        return

    duel["p2_id"] = user.id
    duel["p2_name"] = user.first_name
    duel["p2_chat"] = update.effective_chat.id

    await context.bot.send_message(
        chat_id=duel["p1_chat"],
        text=f"{duel['p2_name']} وارد چالش شد! انتخابتو بزن:",
        reply_markup=choice_keyboard(f"duel:{code}"),
    )
    await update.message.reply_text(
        f"وارد چالش {duel['p1_name']} شدی. انتخابتو بزن:",
        reply_markup=choice_keyboard(f"duel:{code}"),
    )


async def duel_play(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, choice: str):
    query = update.callback_query
    duel = DUELS.get(code)

    if duel is None:
        await query.answer("این چالش دیگه فعال نیست.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id == duel["p1_id"]:
        role, other_role = "p1", "p2"
    elif user_id == duel["p2_id"]:
        role, other_role = "p2", "p1"
    else:
        await query.answer("این چالش برای تو نیست.", show_alert=True)
        return

    duel[f"{role}_choice"] = choice
    await query.answer(f"انتخاب کردی: {CHOICES[choice]['emoji']} {CHOICES[choice]['label']}")

    if duel[f"{other_role}_choice"] is None:
        await query.edit_message_text("انتخابت ثبت شد. منتظر حریفتیم… ⏳")
        return

    p1_choice, p2_choice = duel["p1_choice"], duel["p2_choice"]
    result_for_p1 = judge(p1_choice, p2_choice)
    result_for_p2 = "tie" if result_for_p1 == "tie" else ("win" if result_for_p1 == "lose" else "lose")

    record_result(duel["p1_id"], result_for_p1)
    record_result(duel["p2_id"], result_for_p2)
    bump_games_played()

    def render(name_self, choice_self, name_other, choice_other, result, self_id):
        if result == "tie":
            body = f"هر دو {CHOICES[choice_self]['emoji']} {CHOICES[choice_self]['label']} زدید — مساوی شد."
        else:
            winner_choice = choice_self if result == "win" else choice_other
            loser_choice = choice_other if result == "win" else choice_self
            winner_name = name_self if result == "win" else name_other
            line = (f"{CHOICES[winner_choice]['emoji']} {CHOICES[winner_choice]['label']} می‌بره "
                    f"{CHOICES[loser_choice]['emoji']} {CHOICES[loser_choice]['label']} رو")
            outcome = "بردی! 🎉" if result == "win" else f"{winner_name} برد."
            body = f"{line}\n{outcome}"
        return f"{body}\n\n{stats_line(self_id)}"

    text_p1 = render(duel["p1_name"], p1_choice, duel["p2_name"], p2_choice, result_for_p1, duel["p1_id"])
    text_p2 = render(duel["p2_name"], p2_choice, duel["p1_name"], p1_choice, result_for_p2, duel["p2_id"])

    rematch_kb = InlineKeyboardMarkup([[InlineKeyboardButton("👥 چالش تازه", callback_data="challenge:new")]])

    if role == "p1":
        await query.edit_message_text(text_p1, reply_markup=rematch_kb)
        await context.bot.send_message(chat_id=duel["p2_chat"], text=text_p2, reply_markup=rematch_kb)
    else:
        await query.edit_message_text(text_p2, reply_markup=rematch_kb)
        await context.bot.send_message(chat_id=duel["p1_chat"], text=text_p1, reply_markup=rematch_kb)

    del DUELS[code]


# ------------------------------------------------------------ پنل ادمین ----

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await show_admin_panel(update.message.reply_text)


async def show_admin_panel(send):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 آمار ربات", callback_data="admin:stats")],
        [InlineKeyboardButton("📣 ارسال پیام همگانی", callback_data="admin:broadcast")],
    ])
    await send("پنل مدیریت:", reply_markup=keyboard)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (f"👤 تعداد کاربران: {count_users()}\n"
            f"🎮 تعداد بازی‌های انجام‌شده: {count_games_played()}")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📣 ارسال پیام همگانی", callback_data="admin:broadcast")]])
    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_broadcast_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_broadcast"] = True
    await query.edit_message_text(
        "متن پیامی که می‌خوای برای همه‌ی کاربرهای ربات ارسال بشه رو بفرست.\n"
        "برای لغو /cancel رو بزن."
    )


async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        await update.message.reply_text("ارسال پیام همگانی لغو شد.")


async def on_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فقط وقتی ادمین توی حالت 'منتظر متن پیام همگانی' باشه فعال می‌شه."""
    user_id = update.effective_user.id
    if not is_admin(user_id) or not context.user_data.get("awaiting_broadcast"):
        return

    context.user_data["awaiting_broadcast"] = False
    message_text = update.message.text

    chat_ids = get_all_chat_ids()
    sent, failed = 0, 0
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message_text)
            sent += 1
        except Exception as e:  # کاربرهایی که بات رو بلاک کرده باشن خطا می‌ده
            failed += 1
            log.warning("broadcast failed for %s: %s", chat_id, e)

    await update.message.reply_text(f"ارسال شد ✅\nموفق: {sent}\nناموفق: {failed}")


# --------------------------------------------------------- روتر کالبک‌ها ----

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data

    if data == "solo:menu":
        await solo_menu(update, context)
    elif data.startswith("solo:"):
        await solo_play(update, context, data.split(":", 1)[1])
    elif data == "challenge:new":
        await new_challenge(update, context)
    elif data.startswith("duel:"):
        _, code, choice = data.split(":", 2)
        await duel_play(update, context, code, choice)
    elif data == "stats:me":
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(stats_line(update.callback_query.from_user.id))
    elif data == "admin:stats" and is_admin(update.callback_query.from_user.id):
        await admin_stats(update, context)
    elif data == "admin:broadcast" and is_admin(update.callback_query.from_user.id):
        await admin_broadcast_prompt(update, context)


def main():
    if TOKEN == "PUT-YOUR-TOKEN-HERE":
        raise SystemExit("لطفاً TOKEN رو ست کن (متغیر محیطی TOKEN یا مستقیم توی کد).")
    if not ADMIN_IDS:
        log.warning("هیچ ADMIN_IDS ست نشده — پنل ادمین برای هیچ‌کس در دسترس نیست.")

    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("cancel", cancel_broadcast))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_text))

    log.info("بات در حال اجراست…")
    app.run_polling()


if __name__ == "__main__":
    main()
