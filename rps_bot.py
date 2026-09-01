"""
بات سنگ‌کاغذقیچی برای تلگرام — نسخه با ذخیره امتیاز و پنل ادمین
------------------------------------------------------------------
امکانات:
  1) بازی تکی در برابر خود بات (فوری)
  2) چالش با یک دوست از طریق لینک: هرکس توی پیوی خودش انتخابش رو
     می‌زنه، نتیجه وقتی هر دو انتخاب کردن هم‌زمان اعلام می‌شه.
  3) بازی مستقیم داخل هر چتی از طریق حالت اینلاین: کافیه توی هر چتی
     (خصوصی، گروه، حتی چت با یه مخاطب دیگه) بنویسی @یوزرنیم_بات و
     نتیجه رو بفرستی — بازی همون‌جا شروع می‌شه، بدون نیاز به اضافه کردن
     بات به گروه. (نیاز به فعال‌سازی یک‌باره‌ی /setinline توی BotFather)
  4) بازی مستقیم داخل یه گروه با دستور /duel (برای وقتی که بات از قبل
     عضو گروهه): هر دو بازیکن روی همون پیامِ توی همون گروه دکمه‌هاشون
     رو می‌زنن.
  5) امتیازها (برد/باخت/مساوی هر کاربر) توی یک فایل SQLite ذخیره
     می‌شن و با ری‌استارت شدن بات از بین نمی‌رن.
  6) پنل ادمین (/admin): آمار کلی ربات + ارسال پیام همگانی به همه
     کاربرهایی که تا حالا با بات استارت زدن.

نحوه‌ی اجرا:
  1) از @BotFather یه بات بساز و توکنش رو بگیر.
  2) همون‌جا توی BotFather دستور /setinline رو بزن، بات رو انتخاب کن و
     یه متن جای‌گیر کوتاه وارد کن (مثلاً «برای شروع بازی بنویس...»).
     بدون این مرحله، تایپ @یوزرنیم_بات توی چت‌ها هیچ نتیجه‌ای نمی‌ده.
  3) آیدی عددی خودت رو از یه بات مثل @userinfobot بگیر.
  4) pip install -r requirements.txt
  5) متغیرهای محیطی رو ست کن:
        export TOKEN="123456:ABC-your-token"
        export ADMIN_IDS="111111,222222"     # آیدی عددی ادمین‌ها، با کاما جدا
  6) python rps_bot.py

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
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rps-bot")

TOKEN = os.environ.get("TOKEN", "8978449695:AAH-UC8RwuX2NuM_sP9u7ZbHGsCAliTyF74")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "7689823397").split(",") if x.strip().isdigit()}
DB_PATH = os.environ.get("DB_PATH", "rps.db")

# به‌جای ایموجی دست، از خودِ عناصر بازی استفاده می‌کنیم
CHOICES = {
    "rock":     {"label": "سنگ",  "emoji": "🪨", "beats": "scissors"},
    "paper":    {"label": "کاغذ", "emoji": "📄", "beats": "rock"},
    "scissors": {"label": "قیچی", "emoji": "✂️", "beats": "paper"},
}

DUELS: dict[str, dict] = {}

# چالش‌های داخل یک گروه/چت مشترک: هر دو بازیکن روی همون پیامِ توی همون چت
# دکمه می‌زنن، به‌جای اینکه هرکدوم بره توی پیوی جدا با بات.
# هر آیتم: {chat_id, message_id, p1_id, p1_name, p2_id, p2_name, p1_choice, p2_choice}
GDUELS: dict[str, dict] = {}


# ------------------------------------------------------------------ DB ------

def db():
    # اگه پوشه‌ی مقصد (مثلاً /data برای Volume توی Railway) وجود نداشته باشه،
    # sqlite3.connect با خطای "unable to open database file" کرش می‌کنه.
    # این خط اون پوشه رو در صورت نبودن می‌سازه تا این مشکل پیش نیاد.
    parent_dir = os.path.dirname(DB_PATH)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

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
        "می‌تونی همین‌جا با خود من بازی کنی، یا یه لینک چالش برای یه دوست بفرستی. "
        "امتیازهات ذخیره می‌مونه.\n\n"
        "برای بازی مستقیم توی هر چتی (خصوصی یا گروه)، کافیه همون‌جا بنویسی "
        f"@{(await context.bot.get_me()).username} و نتیجه رو بفرستی — بازی همون‌جا شروع می‌شه.",
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


# --------------------------------------------------- بازی داخل خود گپ -------

async def group_duel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /duel — یه بازی باز می‌سازه که مستقیم روی همون پیامِ توی همین چت انجام می‌شه."""
    user = update.effective_user
    chat = update.effective_chat
    upsert_user(user.id, chat.id, user.first_name)

    if chat.type == "private":
        await update.message.reply_text(
            "این دستور برای بازی داخل یه گروهه. من رو به یه گروه اضافه کن و همون‌جا /duel رو بزن، "
            "یا از دکمه‌ی «چالش با یک دوست» بالا برای بازی دو نفره‌ی جداگانه استفاده کن."
        )
        return

    code = uuid.uuid4().hex[:8]
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🤜🤛 پیوستن به بازی", callback_data=f"gduel:{code}:join")]])
    msg = await update.message.reply_text(
        f"{user.first_name} یه بازی سنگ‌کاغذقیچی شروع کرد. کی حاضره باهاش بازی کنه؟",
        reply_markup=keyboard,
    )

    GDUELS[code] = {
        "chat_id": chat.id, "message_id": msg.message_id,
        "p1_id": user.id, "p1_name": user.first_name,
        "p2_id": None, "p2_name": None,
        "p1_choice": None, "p2_choice": None,
    }


async def group_duel_join(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    query = update.callback_query
    duel = GDUELS.get(code)
    user = query.from_user

    if duel is None:
        await query.answer("این بازی دیگه فعال نیست.", show_alert=True)
        return
    if user.id == duel["p1_id"]:
        await query.answer("این بازی خودته — باید یه نفر دیگه بپیونده 🙂", show_alert=True)
        return
    if duel["p2_id"] is not None:
        await query.answer("این بازی قبلاً پر شده.", show_alert=True)
        return

    # پیام‌های ساخته‌شده از طریق حالت اینلاین، chat/message معمولی ندارن
    # (فقط inline_message_id) — پس برای این حالت از آیدی خود کاربر به‌جای chat_id استفاده می‌کنیم.
    fallback_chat_id = query.message.chat_id if query.message else user.id
    upsert_user(user.id, fallback_chat_id, user.first_name)
    duel["p2_id"] = user.id
    duel["p2_name"] = user.first_name

    await query.answer("پیوستی! انتخابتو بزن.")
    await query.edit_message_text(
        f"{duel['p1_name']} 🆚 {duel['p2_name']}\n"
        f"هر دو نفر مخفیانه انتخابتون رو بزنید — نتیجه وقتی هر دو زدید نشون داده می‌شه:",
        reply_markup=choice_keyboard(f"gduel:{code}"),
    )


async def group_duel_play(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, choice: str):
    query = update.callback_query
    duel = GDUELS.get(code)
    user_id = query.from_user.id

    if duel is None:
        await query.answer("این بازی دیگه فعال نیست.", show_alert=True)
        return

    if user_id == duel["p1_id"]:
        role, other_role = "p1", "p2"
    elif user_id == duel["p2_id"]:
        role, other_role = "p2", "p1"
    else:
        await query.answer("این بازی برای تو نیست.", show_alert=True)
        return

    if duel[f"{role}_choice"] is not None:
        await query.answer("قبلاً انتخاب کردی، منتظر حریفت باش.")
        return

    duel[f"{role}_choice"] = choice
    # انتخاب فقط به‌صورت popup خصوصی به خودش نشون داده می‌شه — بقیه‌ی گروه نمی‌بینن
    await query.answer(f"انتخاب کردی: {CHOICES[choice]['emoji']} {CHOICES[choice]['label']}")

    if duel[f"{other_role}_choice"] is None:
        await query.edit_message_text(
            f"{duel['p1_name']} 🆚 {duel['p2_name']}\n"
            f"⏳ یک بازیکن انتخاب کرد. منتظر بازیکن دیگه‌ایم…",
            reply_markup=choice_keyboard(f"gduel:{code}"),
        )
        return

    p1_choice, p2_choice = duel["p1_choice"], duel["p2_choice"]
    result_for_p1 = judge(p1_choice, p2_choice)

    record_result(duel["p1_id"], result_for_p1)
    record_result(duel["p2_id"], "tie" if result_for_p1 == "tie" else ("win" if result_for_p1 == "lose" else "lose"))
    bump_games_played()

    if result_for_p1 == "tie":
        body = f"هر دو {CHOICES[p1_choice]['emoji']} {CHOICES[p1_choice]['label']} زدید — مساوی شد."
    else:
        winner_name, winner_choice = (duel["p1_name"], p1_choice) if result_for_p1 == "win" else (duel["p2_name"], p2_choice)
        loser_choice = p2_choice if result_for_p1 == "win" else p1_choice
        body = (f"{CHOICES[winner_choice]['emoji']} {CHOICES[winner_choice]['label']} می‌بره "
                f"{CHOICES[loser_choice]['emoji']} {CHOICES[loser_choice]['label']} رو\n"
                f"🏆 {winner_name} برد!")

    text = (f"{duel['p1_name']}: {CHOICES[p1_choice]['emoji']} {CHOICES[p1_choice]['label']}\n"
            f"{duel['p2_name']}: {CHOICES[p2_choice]['emoji']} {CHOICES[p2_choice]['label']}\n\n"
            f"{body}")

    rematch_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 بازی جدید", callback_data="gduel:new")]])
    await query.edit_message_text(text, reply_markup=rematch_kb)

    del GDUELS[code]


async def group_duel_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی «بازی جدید» بعد از پایان یه دوئل گروهی — یه چالش تازه می‌سازه."""
    query = update.callback_query
    user = query.from_user
    # این دکمه هم ممکنه روی یه پیام اینلاین باشه که chat واقعی نداره
    fallback_chat_id = query.message.chat_id if query.message else user.id
    upsert_user(user.id, fallback_chat_id, user.first_name)
    await query.answer()

    code = uuid.uuid4().hex[:8]
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🤜🤛 پیوستن به بازی", callback_data=f"gduel:{code}:join")]])
    await query.edit_message_text(
        f"{user.first_name} یه بازی سنگ‌کاغذقیچی شروع کرد. کی حاضره باهاش بازی کنه؟",
        reply_markup=keyboard,
    )
    GDUELS[code] = {
        "chat_id": fallback_chat_id, "message_id": query.message.message_id if query.message else None,
        "p1_id": user.id, "p1_name": user.first_name,
        "p2_id": None, "p2_name": None,
        "p1_choice": None, "p2_choice": None,
    }


# --------------------------------------------------------- حالت اینلاین ----

async def on_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    وقتی کاربر توی هر چتی (خصوصی/گروه/حتی چت با یه مخاطب دیگه) بنویسه
    @یوزرنیم_بات، همین نتیجه پیشنهاد می‌شه. با فرستادنش، پیام مستقیم توی
    همون چت ساخته می‌شه و بازی از همون‌جا (بدون نیاز به اضافه کردن بات
    به گروه) شروع می‌شه.
    نکته: برای فعال شدن این قابلیت باید یک‌بار توی @BotFather دستور
    /setinline رو برای این بات بزنی و یه متن جای‌گیر (placeholder) ست کنی.
    """
    user = update.inline_query.from_user
    upsert_user(user.id, user.id, user.first_name)

    code = uuid.uuid4().hex[:8]
    GDUELS[code] = {
        "chat_id": None, "message_id": None,
        "p1_id": user.id, "p1_name": user.first_name,
        "p2_id": None, "p2_name": None,
        "p1_choice": None, "p2_choice": None,
    }

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🤜🤛 پیوستن به بازی", callback_data=f"gduel:{code}:join")]])
    result = InlineQueryResultArticle(
        id=code,
        title="شروع بازی سنگ‌کاغذقیچی 🪨📄✂️",
        description="این نتیجه رو بفرست تا بازی همین‌جا شروع بشه",
        input_message_content=InputTextMessageContent(
            f"{user.first_name} یه بازی سنگ‌کاغذقیچی شروع کرد. کی حاضره باهاش بازی کنه؟"
        ),
        reply_markup=keyboard,
    )
    await update.inline_query.answer([result], cache_time=0, is_personal=True)


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
    elif data == "gduel:new":
        await group_duel_new(update, context)
    elif data.startswith("gduel:"):
        _, code, action = data.split(":", 2)
        if action == "join":
            await group_duel_join(update, context, code)
        else:
            await group_duel_play(update, context, code, action)
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
    app.add_handler(CommandHandler("duel", group_duel_start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("cancel", cancel_broadcast))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(InlineQueryHandler(on_inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_text))

    log.info("بات در حال اجراست…")
    app.run_polling()


if __name__ == "__main__":
    main()
