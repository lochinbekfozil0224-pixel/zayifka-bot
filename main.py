# main.py
# Zayavka qabul qiluvchi va nakrutka bloklovchi Telegram bot
# Foydalanish:
#   pip install aiogram==3.7.0
#   python main.py

import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, ChatJoinRequest, ChatMemberUpdated,
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)

# ============================ KONFIG ============================
BOT_TOKEN = "8201047001:AAH90F9fFfxrFBiGlqOfYdre3kaimDG_YQU"
ADMIN_ID = 8135915671
DB_PATH = "bot.db"

DEFAULT_FREE_LIMIT = 20000          # bepul lifetime limit (har user uchun)
DEFAULT_PRICE_PER_1K = 1000         # 1000 zayavka uchun narx (so'm)
FAKE_THRESHOLD = 35                 # bundan past ball — nakrutka

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ============================ DATABASE ============================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as c, c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT, full_name TEXT,
            balance INTEGER DEFAULT 0,
            free_used INTEGER DEFAULT 0,
            total_accepted INTEGER DEFAULT 0,
            total_blocked INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS bot_channels (
            chat_id INTEGER PRIMARY KEY,
            chat_title TEXT, chat_username TEXT,
            added_by INTEGER, added_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS accept_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, chat_id INTEGER, chat_title TEXT,
            target_count INTEGER,
            accepted_count INTEGER DEFAULT 0,
            blocked_count INTEGER DEFAULT 0,
            fake_filter INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS block_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, chat_id INTEGER, chat_title TEXT,
            scanned_count INTEGER DEFAULT 0,
            blocked_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS mandatory_channels (
            chat_id INTEGER PRIMARY KEY,
            chat_title TEXT, chat_username TEXT, invite_link TEXT
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, amount INTEGER,
            receipt_file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        """)
        defaults = {
            "free_limit": str(DEFAULT_FREE_LIMIT),
            "price_per_1k": str(DEFAULT_PRICE_PER_1K),
            "card_number": "8600 1234 5678 9012",
            "card_holder": "BOT EGASI",
            "welcome_text": (
                "👋 <b>Salomu alaykum!</b>\n\n"
                "Men <b>zayavka qabul qiluvchi</b> va <b>nakrutka bloklovchi</b> botman.\n\n"
                "🔹 Kanalingizga kelgan so'rovlarni avto qabul qilaman\n"
                "🔹 Nakrutka/bot akkauntlarini avto chiqaraman\n\n"
                "Pastdagi tugmalardan birini tanlang 👇"
            ),
            "help_text": (
                "🆘 <b>Yordam</b>\n\n"
                "1️⃣ Botni kanalingizga <b>admin</b> qilib qo'shing "
                "(\"<i>Add new admin</i>\" tugmasidan).\n"
                "2️⃣ Bot kerakli huquqlarga ega bo'lishi shart:\n"
                "   • Add new members / Invite users\n"
                "   • Ban users\n"
                "   • Manage join requests\n\n"
                "3️⃣ Asosiy menyudan funksiyani tanlang.\n\n"
                "💎 <b>Tarif:</b>\n"
                "• Birinchi <b>20 000</b> ta zayavka — <b>BEPUL</b>\n"
                "• Undan keyin <b>1000 ta = 1000 so'm</b>\n\n"
                "Savollar uchun: @sizning_admin"
            ),
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, v))


def gset(k, default=None):
    with closing(db()) as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r else default


def sset(k, v):
    with closing(db()) as c, c:
        c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, str(v)))


def upsert_user(uid, username=None, full_name=None):
    with closing(db()) as c, c:
        c.execute("""INSERT OR IGNORE INTO users(user_id, username, full_name, created_at)
                     VALUES(?,?,?,?)""", (uid, username, full_name, int(time.time())))
        c.execute("UPDATE users SET username=?, full_name=? WHERE user_id=?",
                  (username, full_name, uid))


def get_user(uid):
    with closing(db()) as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def all_user_ids():
    with closing(db()) as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users WHERE is_banned=0")]


def add_balance(uid, amount):
    with closing(db()) as c, c:
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))


def deduct_for_task(uid, paid_cost, free_used):
    with closing(db()) as c, c:
        c.execute("""UPDATE users SET balance=balance-?, free_used=free_used+?
                     WHERE user_id=?""", (paid_cost, free_used, uid))


def inc_accepted(uid):
    with closing(db()) as c, c:
        c.execute("UPDATE users SET total_accepted=total_accepted+1 WHERE user_id=?", (uid,))


def inc_blocked(uid):
    with closing(db()) as c, c:
        c.execute("UPDATE users SET total_blocked=total_blocked+1 WHERE user_id=?", (uid,))


def save_bot_channel(cid, title, uname, by):
    with closing(db()) as c, c:
        c.execute("INSERT OR REPLACE INTO bot_channels VALUES(?,?,?,?,?)",
                  (cid, title, uname, by, int(time.time())))


def remove_bot_channel(cid):
    with closing(db()) as c, c:
        c.execute("DELETE FROM bot_channels WHERE chat_id=?", (cid,))


def user_channels(uid):
    with closing(db()) as c:
        return c.execute("SELECT * FROM bot_channels WHERE added_by=? ORDER BY added_at DESC",
                         (uid,)).fetchall()


def all_bot_channels():
    with closing(db()) as c:
        return c.execute("SELECT * FROM bot_channels").fetchall()


def create_accept_task(uid, cid, title, count, fake_filter=1):
    with closing(db()) as c, c:
        cur = c.execute("""INSERT INTO accept_tasks
                           (user_id,chat_id,chat_title,target_count,fake_filter,created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (uid, cid, title, count, fake_filter, int(time.time())))
        return cur.lastrowid


def active_accept_task(cid):
    with closing(db()) as c:
        return c.execute("""SELECT * FROM accept_tasks
                            WHERE chat_id=? AND status='active'""", (cid,)).fetchone()


def get_task(tid):
    with closing(db()) as c:
        return c.execute("SELECT * FROM accept_tasks WHERE id=?", (tid,)).fetchone()


def update_task_status(tid, status):
    with closing(db()) as c, c:
        c.execute("UPDATE accept_tasks SET status=? WHERE id=?", (status, tid))


def inc_task_accepted(tid):
    with closing(db()) as c, c:
        c.execute("UPDATE accept_tasks SET accepted_count=accepted_count+1 WHERE id=?", (tid,))


def inc_task_blocked(tid):
    with closing(db()) as c, c:
        c.execute("UPDATE accept_tasks SET blocked_count=blocked_count+1 WHERE id=?", (tid,))


def active_block_task(cid):
    with closing(db()) as c:
        return c.execute("""SELECT * FROM block_tasks
                            WHERE chat_id=? AND status='active'""", (cid,)).fetchone()


def create_block_task(uid, cid, title):
    with closing(db()) as c, c:
        cur = c.execute("""INSERT INTO block_tasks(user_id,chat_id,chat_title,created_at)
                           VALUES(?,?,?,?)""", (uid, cid, title, int(time.time())))
        return cur.lastrowid


def stop_block_task(cid):
    with closing(db()) as c, c:
        c.execute("UPDATE block_tasks SET status='stopped' WHERE chat_id=? AND status='active'", (cid,))


def inc_block_task(cid, scanned=0, blocked=0):
    with closing(db()) as c, c:
        c.execute("""UPDATE block_tasks SET scanned_count=scanned_count+?,
                     blocked_count=blocked_count+? WHERE chat_id=? AND status='active'""",
                  (scanned, blocked, cid))


def mandatory_list():
    with closing(db()) as c:
        return c.execute("SELECT * FROM mandatory_channels").fetchall()


def add_mandatory(cid, title, uname, link):
    with closing(db()) as c, c:
        c.execute("INSERT OR REPLACE INTO mandatory_channels VALUES(?,?,?,?)",
                  (cid, title, uname, link))


def remove_mandatory(cid):
    with closing(db()) as c, c:
        c.execute("DELETE FROM mandatory_channels WHERE chat_id=?", (cid,))


def create_payment(uid, amount, receipt):
    with closing(db()) as c, c:
        cur = c.execute("""INSERT INTO payments(user_id,amount,receipt_file_id,created_at)
                           VALUES(?,?,?,?)""", (uid, amount, receipt, int(time.time())))
        return cur.lastrowid


def get_payment(pid):
    with closing(db()) as c:
        return c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()


def update_payment(pid, status):
    with closing(db()) as c, c:
        c.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))


# ============================ HELPERS ============================
def calc_cost(free_used: int, requested: int):
    """(paid_count, paid_cost, free_in_task) qaytaradi."""
    free_limit = int(gset("free_limit", DEFAULT_FREE_LIMIT))
    price_per_1k = int(gset("price_per_1k", DEFAULT_PRICE_PER_1K))
    free_remaining = max(0, free_limit - free_used)
    free_in_task = min(requested, free_remaining)
    paid = requested - free_in_task
    cost = ((paid + 999) // 1000) * price_per_1k
    return paid, cost, free_in_task


async def score_user(b: Bot, user) -> int:
    """0 — tozaa fake, 100 — tozaa real."""
    score = 50
    if user.username:
        score += 15
    else:
        score -= 15
    try:
        photos = await b.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            score += 15
        else:
            score -= 10
    except Exception:
        pass
    if user.first_name and len(user.first_name) >= 3:
        score += 5
    if user.last_name:
        score += 10
    if getattr(user, "is_premium", False):
        score += 25
    if user.language_code:
        score += 5
    # Yangi yaratilgan akkauntlar — past trust
    if user.id > 7_500_000_000:
        score -= 25
    elif user.id > 7_000_000_000:
        score -= 10
    elif user.id > 6_000_000_000:
        score -= 5
    return max(0, min(100, score))


async def check_mandatory_subs(b: Bot, uid: int):
    """Obuna bo'lmagan kanallar ro'yxatini qaytaradi."""
    not_subbed = []
    for ch in mandatory_list():
        try:
            m = await b.get_chat_member(ch["chat_id"], uid)
            if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                not_subbed.append(ch)
        except TelegramBadRequest:
            not_subbed.append(ch)
        except Exception as e:
            log.warning(f"Sub check failed: {e}")
    return not_subbed


def mandatory_kb(channels) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        link = ch["invite_link"] or (f"https://t.me/{ch['chat_username']}" if ch["chat_username"] else "")
        if link:
            rows.append([InlineKeyboardButton(text=f"📢 {ch['chat_title']}", url=link)])
    rows.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================ KEYBOARDS ============================
def main_menu_kb(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📥 Zayavka qabul qilish"), KeyboardButton(text="🛡 Nakrutka bloklash")],
        [KeyboardButton(text="👤 Profil"), KeyboardButton(text="🆘 Yordam")],
    ]
    if uid == ADMIN_ID:
        rows.append([KeyboardButton(text="⚙️ Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="👥 Foydalanuvchilar")],
        [KeyboardButton(text="⚙️ Limitlar"), KeyboardButton(text="💳 Karta")],
        [KeyboardButton(text="📝 Matnlar"), KeyboardButton(text="📢 Majburiy obuna")],
        [KeyboardButton(text="📨 Userlarga xabar"), KeyboardButton(text="📡 Kanallarga xabar")],
        [KeyboardButton(text="💰 To'lovlar"), KeyboardButton(text="⬅️ Asosiy menyu")],
    ], resize_keyboard=True)


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


def channels_kb(channels, prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        rows.append([InlineKeyboardButton(
            text=f"📢 {ch['chat_title']}",
            callback_data=f"{prefix}:{ch['chat_id']}"
        )])
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(yes: str, no: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=yes),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=no),
    ]])


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Hisobni to'ldirish", callback_data="topup")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="refresh_profile")],
    ])


# ============================ STATES ============================
class AcceptFlow(StatesGroup):
    entering_count = State()
    confirming = State()


class TopUpFlow(StatesGroup):
    entering_amount = State()
    sending_receipt = State()


class AdminFlow(StatesGroup):
    broadcast_users = State()
    broadcast_channels = State()
    set_free_limit = State()
    set_price = State()
    set_card_number = State()
    set_card_holder = State()
    edit_welcome = State()
    edit_help = State()
    add_mandatory = State()


# ============================ START / MENU ============================
@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    upsert_user(m.from_user.id, m.from_user.username, m.from_user.full_name)

    not_subbed = await check_mandatory_subs(bot, m.from_user.id)
    if not_subbed:
        await m.answer(
            "📢 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>",
            reply_markup=mandatory_kb(not_subbed),
        )
        return

    await m.answer(gset("welcome_text"), reply_markup=main_menu_kb(m.from_user.id))


@router.callback_query(F.data == "check_subs")
async def cb_check_subs(c: CallbackQuery):
    not_subbed = await check_mandatory_subs(bot, c.from_user.id)
    if not_subbed:
        await c.answer("❌ Hali ham obuna emassiz!", show_alert=True)
        return
    await c.message.delete()
    await c.message.answer(gset("welcome_text"), reply_markup=main_menu_kb(c.from_user.id))
    await c.answer("✅ Tasdiqlandi!")


@router.callback_query(F.data == "cancel")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer("❌ Bekor qilindi.", reply_markup=main_menu_kb(c.from_user.id))
    await c.answer()


@router.message(F.text == "❌ Bekor qilish")
async def msg_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Bekor qilindi.", reply_markup=main_menu_kb(m.from_user.id))


@router.message(F.text == "🆘 Yordam")
async def msg_help(m: Message):
    await m.answer(gset("help_text"), reply_markup=main_menu_kb(m.from_user.id))


# ============================ PROFIL ============================
def profile_text(u) -> str:
    free_limit = int(gset("free_limit", DEFAULT_FREE_LIMIT))
    free_remaining = max(0, free_limit - u["free_used"])
    return (
        f"👤 <b>Profilingiz</b>\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"👤 Ism: {u['full_name'] or '—'}\n"
        f"💰 Balans: <b>{u['balance']:,}</b> so'm\n\n"
        f"📥 Qabul qilingan zayavkalar: <b>{u['total_accepted']:,}</b>\n"
        f"🛡 Bloklangan nakrutkalar: <b>{u['total_blocked']:,}</b>\n\n"
        f"🎁 Bepul limit: <b>{free_remaining:,}</b> / {free_limit:,}\n"
        f"💎 Tarif: 1000 zayavka = {gset('price_per_1k')} so'm"
    )


@router.message(F.text == "👤 Profil")
async def msg_profile(m: Message):
    u = get_user(m.from_user.id)
    if not u:
        upsert_user(m.from_user.id, m.from_user.username, m.from_user.full_name)
        u = get_user(m.from_user.id)
    await m.answer(profile_text(u), reply_markup=profile_kb())


@router.callback_query(F.data == "refresh_profile")
async def cb_refresh_profile(c: CallbackQuery):
    u = get_user(c.from_user.id)
    try:
        await c.message.edit_text(profile_text(u), reply_markup=profile_kb())
    except TelegramBadRequest:
        pass
    await c.answer("🔄 Yangilandi")


# ============================ TO'LOV ============================
@router.callback_query(F.data == "topup")
async def cb_topup(c: CallbackQuery, state: FSMContext):
    await state.set_state(TopUpFlow.entering_amount)
    await c.message.answer(
        "💵 <b>Hisobni to'ldirish</b>\n\n"
        f"💳 Karta raqami: <code>{gset('card_number')}</code>\n"
        f"👤 Karta egasi: <b>{gset('card_holder')}</b>\n\n"
        "✏️ To'ldirmoqchi bo'lgan summani so'mda yuboring (masalan: <code>50000</code>)",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(TopUpFlow.entering_amount, F.text.regexp(r"^\d+$"))
async def topup_amount(m: Message, state: FSMContext):
    amount = int(m.text)
    if amount < 1000:
        await m.answer("⚠️ Minimal summa: 1 000 so'm")
        return
    await state.update_data(amount=amount)
    await state.set_state(TopUpFlow.sending_receipt)
    await m.answer(
        f"📸 <b>{amount:,} so'm</b> to'lov chekini (skrinshot) yuboring.",
        reply_markup=cancel_kb(),
    )


@router.message(TopUpFlow.entering_amount)
async def topup_amount_invalid(m: Message):
    await m.answer("⚠️ Iltimos faqat raqam yuboring (masalan: 50000)")


@router.message(TopUpFlow.sending_receipt, F.photo)
async def topup_receipt(m: Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    file_id = m.photo[-1].file_id
    pid = create_payment(m.from_user.id, amount, file_id)
    await state.clear()
    await m.answer(
        "✅ <b>To'lov adminga yuborildi.</b>\n"
        "Tasdiqlangach balansingizga qo'shiladi.",
        reply_markup=main_menu_kb(m.from_user.id),
    )
    # adminga
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok:{pid}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no:{pid}"),
    ]])
    try:
        await bot.send_photo(
            ADMIN_ID, file_id,
            caption=(
                f"💰 <b>Yangi to'lov</b>\n\n"
                f"👤 User: <a href='tg://user?id={m.from_user.id}'>"
                f"{m.from_user.full_name}</a>\n"
                f"🆔 ID: <code>{m.from_user.id}</code>\n"
                f"💵 Summa: <b>{amount:,}</b> so'm\n"
                f"#️⃣ To'lov ID: <code>{pid}</code>"
            ),
            reply_markup=kb,
        )
    except Exception as e:
        log.error(f"Admin notify failed: {e}")


@router.message(TopUpFlow.sending_receipt)
async def topup_receipt_invalid(m: Message):
    await m.answer("⚠️ Faqat <b>rasm</b> (skrinshot) yuboring.")


@router.callback_query(F.data.startswith("pay_ok:"))
async def cb_pay_ok(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        await c.answer("⛔️ Faqat admin", show_alert=True)
        return
    pid = int(c.data.split(":")[1])
    p = get_payment(pid)
    if not p or p["status"] != "pending":
        await c.answer("Allaqachon yopilgan", show_alert=True)
        return
    add_balance(p["user_id"], p["amount"])
    update_payment(pid, "approved")
    try:
        await c.message.edit_caption(
            (c.message.caption or "") + f"\n\n✅ <b>Tasdiqlandi</b>",
        )
    except Exception:
        pass
    try:
        await bot.send_message(
            p["user_id"],
            f"✅ <b>{p['amount']:,} so'm</b> balansingizga qo'shildi!",
        )
    except Exception:
        pass
    await c.answer("Tasdiqlandi")


@router.callback_query(F.data.startswith("pay_no:"))
async def cb_pay_no(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        await c.answer("⛔️ Faqat admin", show_alert=True)
        return
    pid = int(c.data.split(":")[1])
    p = get_payment(pid)
    if not p or p["status"] != "pending":
        await c.answer("Allaqachon yopilgan", show_alert=True)
        return
    update_payment(pid, "rejected")
    try:
        await c.message.edit_caption((c.message.caption or "") + "\n\n❌ <b>Rad etildi</b>")
    except Exception:
        pass
    try:
        await bot.send_message(p["user_id"], "❌ To'lovingiz rad etildi. Adminga murojaat qiling.")
    except Exception:
        pass
    await c.answer("Rad etildi")


# ============================ ZAYAVKA QABUL ============================
@router.message(F.text == "📥 Zayavka qabul qilish")
async def msg_accept_start(m: Message, state: FSMContext):
    await state.clear()
    chs = user_channels(m.from_user.id)
    if not chs:
        await m.answer(
            "⚠️ <b>Hech qaysi kanal topilmadi.</b>\n\n"
            "📌 <b>Qadamlar:</b>\n"
            "1. Kanalingizga kiring → Sozlamalar → Adminlar\n"
            "2. \"<i>Add admin</i>\" → @{} ni tanlang\n"
            "3. <b>Add new members</b> va <b>Manage join requests</b> "
            "huquqlarini bering\n"
            "4. Shu yerga qaytib tugmani qayta bosing".format((await bot.get_me()).username),
            reply_markup=main_menu_kb(m.from_user.id),
        )
        return
    await m.answer(
        "📢 <b>Qaysi kanalga zayavka qabul qilamiz?</b>",
        reply_markup=channels_kb(chs, "acc"),
    )


@router.callback_query(F.data.startswith("acc:"))
async def cb_accept_channel(c: CallbackQuery, state: FSMContext):
    cid = int(c.data.split(":")[1])
    # Active task bormi?
    if active_accept_task(cid):
        await c.answer("⚠️ Bu kanalda allaqachon faol vazifa bor", show_alert=True)
        return
    await state.update_data(chat_id=cid)
    await state.set_state(AcceptFlow.entering_count)
    try:
        await c.message.delete()
    except Exception:
        pass
    u = get_user(c.from_user.id)
    free_limit = int(gset("free_limit", DEFAULT_FREE_LIMIT))
    free_remaining = max(0, free_limit - u["free_used"])
    await c.message.answer(
        f"✏️ <b>Nechta zayavka qabul qilamiz?</b>\n\n"
        f"🎁 Bepul limit: <b>{free_remaining:,}</b>\n"
        f"💰 Balans: <b>{u['balance']:,}</b> so'm\n"
        f"💎 Narx: 1000 ta = {gset('price_per_1k')} so'm\n\n"
        f"Faqat raqam yuboring (masalan: <code>5000</code>)",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AcceptFlow.entering_count, F.text.regexp(r"^\d+$"))
async def acc_count(m: Message, state: FSMContext):
    count = int(m.text)
    if count < 1:
        await m.answer("⚠️ Eng kami 1 ta")
        return
    if count > 1_000_000:
        await m.answer("⚠️ Eng ko'pi 1 000 000 ta")
        return

    u = get_user(m.from_user.id)
    paid, cost, free_in_task = calc_cost(u["free_used"], count)

    if cost > u["balance"]:
        await m.answer(
            f"⚠️ <b>Balans yetarli emas!</b>\n\n"
            f"📊 Hisob:\n"
            f"• Bepul: {free_in_task:,} ta\n"
            f"• Pulli: {paid:,} ta = <b>{cost:,}</b> so'm\n"
            f"• Sizda bor: {u['balance']:,} so'm\n\n"
            f"Hisobni to'ldiring (👤 Profil → 💵 To'ldirish)",
            reply_markup=main_menu_kb(m.from_user.id),
        )
        await state.clear()
        return

    data = await state.get_data()
    cid = data["chat_id"]
    await state.update_data(count=count, paid=paid, cost=cost, free_in_task=free_in_task)
    await state.set_state(AcceptFlow.confirming)

    try:
        ch = await bot.get_chat(cid)
        title = ch.title
    except Exception:
        title = "—"

    await m.answer(
        f"📋 <b>Tasdiqlash</b>\n\n"
        f"📢 Kanal: <b>{title}</b>\n"
        f"📥 Jami: <b>{count:,}</b> ta\n"
        f"🎁 Bepul: {free_in_task:,} ta\n"
        f"💰 Pulli: {paid:,} ta = <b>{cost:,}</b> so'm\n"
        f"🛡 Nakrutka filtri: <b>YOQILGAN</b>\n\n"
        f"Davom etamizmi?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Boshlash", callback_data="acc_go"),
             InlineKeyboardButton(text="🛡 Filtrsiz", callback_data="acc_nofilter")],
            [InlineKeyboardButton(text="❌ Bekor", callback_data="cancel")],
        ]),
    )


@router.message(AcceptFlow.entering_count)
async def acc_count_invalid(m: Message):
    await m.answer("⚠️ Iltimos faqat raqam yuboring")


async def _start_accept_task(uid: int, chat_id: int, count: int, paid: int,
                             cost: int, free_in_task: int, fake_filter: int) -> Optional[int]:
    try:
        ch = await bot.get_chat(chat_id)
        title = ch.title or "—"
    except Exception:
        title = "—"
    deduct_for_task(uid, cost, free_in_task)
    return create_accept_task(uid, chat_id, title, count, fake_filter)


@router.callback_query(AcceptFlow.confirming, F.data.in_(["acc_go", "acc_nofilter"]))
async def cb_acc_confirm(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    fake_filter = 1 if c.data == "acc_go" else 0
    tid = await _start_accept_task(
        c.from_user.id, data["chat_id"], data["count"],
        data["paid"], data["cost"], data["free_in_task"], fake_filter,
    )
    await state.clear()
    try:
        await c.message.delete()
    except Exception:
        pass
    filter_status = "YOQILGAN" if fake_filter else "O'CHIRILGAN"
    await c.message.answer(
        f"✅ <b>Vazifa boshlandi!</b>\n\n"
        f"📥 Maqsad: {data['count']:,} ta zayavka\n"
        f"🛡 Nakrutka filtri: <b>{filter_status}</b>\n\n"
        f"Endi kanalga zayavka kelganida bot avtomatik qabul qiladi.\n"
        f"Holatni 👤 Profildan kuzatib boring.",
        reply_markup=main_menu_kb(c.from_user.id),
    )
    await c.answer()


# ============================ NAKRUTKA BLOKLASH ============================
@router.message(F.text == "🛡 Nakrutka bloklash")
async def msg_block_start(m: Message, state: FSMContext):
    await state.clear()
    chs = user_channels(m.from_user.id)
    if not chs:
        await m.answer(
            "⚠️ <b>Botni avval kanalingizga admin qiling.</b>\n\n"
            "Bot quyidagi huquqlarga ega bo'lishi shart:\n"
            "• <b>Ban users</b> — fakelarni chiqarish uchun\n"
            "• <b>Manage join requests</b>",
            reply_markup=main_menu_kb(m.from_user.id),
        )
        return
    await m.answer(
        "🛡 <b>Nakrutka bloklash</b>\n\n"
        "Bot kanalga yangi qo'shilayotgan har bir a'zoni real-time skanerlaydi "
        "va nakrutka topsa darhol chiqaradi.\n\n"
        "📢 Qaysi kanal uchun yoqamiz?",
        reply_markup=channels_kb(chs, "blk"),
    )


@router.callback_query(F.data.startswith("blk:"))
async def cb_block_channel(c: CallbackQuery):
    cid = int(c.data.split(":")[1])
    try:
        ch = await bot.get_chat(cid)
        title = ch.title or "—"
    except Exception:
        title = "—"

    existing = active_block_task(cid)
    if existing:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛑 To'xtatish", callback_data=f"blk_stop:{cid}"),
            InlineKeyboardButton(text="❌ Bekor", callback_data="cancel"),
        ]])
        await c.message.edit_text(
            f"⚠️ <b>{title}</b> uchun nakrutka filtri allaqachon yoqilgan.\n\n"
            f"📊 Skaner qilingan: {existing['scanned_count']}\n"
            f"🛡 Bloklangan: {existing['blocked_count']}",
            reply_markup=kb,
        )
        await c.answer()
        return

    create_block_task(c.from_user.id, cid, title)
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer(
        f"✅ <b>{title}</b> uchun nakrutka filtri yoqildi!\n\n"
        f"Endi bu kanalga yangi qo'shilayotgan barcha a'zolar tekshiriladi. "
        f"Nakrutka aniqlangan akkauntlar avtomatik chiqarib yuboriladi.\n\n"
        f"To'xtatish uchun shu menyuga qaytib kelishingiz mumkin.",
        reply_markup=main_menu_kb(c.from_user.id),
    )
    await c.answer()


@router.callback_query(F.data.startswith("blk_stop:"))
async def cb_block_stop(c: CallbackQuery):
    cid = int(c.data.split(":")[1])
    stop_block_task(cid)
    try:
        await c.message.edit_text("🛑 Nakrutka filtri to'xtatildi.")
    except Exception:
        pass
    await c.answer("To'xtatildi")


# ============================ ASOSIY: JOIN REQUEST ============================
@router.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    """Kanalga zayavka kelganda — avto qabul + fake filter."""
    cid = req.chat.id
    user = req.from_user
    task = active_accept_task(cid)
    if not task:
        return  # bu kanal uchun vazifa yo'q

    # Limit tugaganmi?
    if task["accepted_count"] >= task["target_count"]:
        update_task_status(task["id"], "completed")
        try:
            await bot.send_message(
                task["user_id"],
                f"✅ <b>{task['chat_title']}</b> kanaliga "
                f"<b>{task['target_count']:,}</b> ta zayavka qabul qilib bo'lindi!\n\n"
                f"📊 Statistika:\n"
                f"• Qabul: {task['accepted_count']:,}\n"
                f"• Bloklangan nakrutka: {task['blocked_count']:,}",
            )
        except Exception:
            pass
        return

    # Fake filter
    if task["fake_filter"]:
        score = await score_user(bot, user)
        if score < FAKE_THRESHOLD:
            try:
                await bot.decline_chat_join_request(cid, user.id)
                inc_task_blocked(task["id"])
                inc_blocked(task["user_id"])
            except Exception as e:
                log.warning(f"Decline failed: {e}")
            return

    # Qabul
    try:
        await bot.approve_chat_join_request(cid, user.id)
        inc_task_accepted(task["id"])
        inc_accepted(task["user_id"])
        # Tugadimi?
        new = get_task(task["id"])
        if new["accepted_count"] >= new["target_count"]:
            update_task_status(task["id"], "completed")
            try:
                await bot.send_message(
                    task["user_id"],
                    f"🎉 <b>{task['chat_title']}</b> kanaliga "
                    f"<b>{task['target_count']:,}</b> ta zayavka qabul qilib bo'lindi!"
                )
            except Exception:
                pass
    except TelegramBadRequest as e:
        log.warning(f"Approve failed: {e}")


# ============================ MEMBER QO'SHILGANDA (BLOCK) ============================
@router.chat_member()
async def on_chat_member(upd: ChatMemberUpdated):
    """Kanal/guruhga yangi a'zo qo'shilganda — nakrutka tekshirish."""
    cid = upd.chat.id
    new_status = upd.new_chat_member.status
    old_status = upd.old_chat_member.status

    # Faqat yangi a'zo qo'shilgan vaqtda
    if old_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED) and \
       new_status == ChatMemberStatus.MEMBER:
        task = active_block_task(cid)
        if not task:
            return
        user = upd.new_chat_member.user
        if user.is_bot:
            return  # botlarni o'tkazib yuboramiz (admin qo'shgan bo'lishi mumkin)
        score = await score_user(bot, user)
        inc_block_task(cid, scanned=1)
        if score < FAKE_THRESHOLD:
            try:
                await bot.ban_chat_member(cid, user.id)
                # Darrov un-ban — keyin qayta kira olishi uchun (lekin filterga tushadi)
                await asyncio.sleep(0.5)
                await bot.unban_chat_member(cid, user.id, only_if_banned=True)
                inc_block_task(cid, blocked=1)
                inc_blocked(task["user_id"])
            except Exception as e:
                log.warning(f"Block kick failed: {e}")


# ============================ BOT ADMIN STATUS O'ZGARGANDA ============================
@router.my_chat_member()
async def on_my_chat_member(upd: ChatMemberUpdated):
    chat = upd.chat
    if chat.type not in ("channel", "supergroup", "group"):
        return
    new_status = upd.new_chat_member.status
    if new_status == ChatMemberStatus.ADMINISTRATOR:
        adder = upd.from_user
        save_bot_channel(chat.id, chat.title or "—", chat.username, adder.id)
        try:
            await bot.send_message(
                adder.id,
                f"✅ Bot <b>{chat.title}</b> kanaliga admin qilindi!\n"
                f"Endi botda \"📥 Zayavka qabul qilish\" yoki \"🛡 Nakrutka bloklash\""
                f" tugmalaridan foydalansangiz bo'ladi.",
            )
        except Exception:
            pass
    elif new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.MEMBER):
        remove_bot_channel(chat.id)


# ============================ ADMIN PANEL ============================
def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


@router.message(F.text == "⚙️ Admin panel")
async def msg_admin_panel(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    await m.answer("⚙️ <b>Admin panel</b>", reply_markup=admin_menu_kb())


@router.message(F.text == "⬅️ Asosiy menyu")
async def msg_back_main(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🏠 Asosiy menyu", reply_markup=main_menu_kb(m.from_user.id))


@router.message(F.text == "📊 Statistika")
async def msg_stats(m: Message):
    if not is_admin(m.from_user.id):
        return
    with closing(db()) as c:
        users = c.execute("SELECT COUNT(*) cnt FROM users").fetchone()["cnt"]
        active = c.execute("SELECT COUNT(*) cnt FROM users WHERE is_banned=0").fetchone()["cnt"]
        accepted = c.execute("SELECT COALESCE(SUM(total_accepted),0) s FROM users").fetchone()["s"]
        blocked = c.execute("SELECT COALESCE(SUM(total_blocked),0) s FROM users").fetchone()["s"]
        ch_count = c.execute("SELECT COUNT(*) cnt FROM bot_channels").fetchone()["cnt"]
        active_tasks = c.execute("SELECT COUNT(*) cnt FROM accept_tasks WHERE status='active'").fetchone()["cnt"]
    await m.answer(
        f"📊 <b>Umumiy statistika</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users:,}</b> (faol: {active:,})\n"
        f"📢 Bot admin kanallar: <b>{ch_count:,}</b>\n"
        f"🟢 Faol vazifalar: <b>{active_tasks:,}</b>\n\n"
        f"📥 Jami qabul qilingan: <b>{accepted:,}</b>\n"
        f"🛡 Jami bloklangan: <b>{blocked:,}</b>"
    )


@router.message(F.text == "👥 Foydalanuvchilar")
async def msg_users(m: Message):
    if not is_admin(m.from_user.id):
        return
    with closing(db()) as c:
        rows = c.execute("""SELECT * FROM users ORDER BY created_at DESC LIMIT 20""").fetchall()
    if not rows:
        await m.answer("Foydalanuvchilar yo'q")
        return
    text = "👥 <b>Oxirgi 20 foydalanuvchi:</b>\n\n"
    for r in rows:
        text += (f"• <code>{r['user_id']}</code> — {r['full_name'] or '—'} "
                 f"| 💰 {r['balance']:,} | 📥 {r['total_accepted']}\n")
    await m.answer(text)


@router.message(F.text == "⚙️ Limitlar")
async def msg_limits(m: Message):
    if not is_admin(m.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎁 Bepul limit: {gset('free_limit')}",
                              callback_data="set_free_limit")],
        [InlineKeyboardButton(text=f"💎 Narx (1000 ga): {gset('price_per_1k')}",
                              callback_data="set_price")],
    ])
    await m.answer("⚙️ <b>Limit va narxlar</b>", reply_markup=kb)


@router.callback_query(F.data == "set_free_limit")
async def cb_set_free_limit(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminFlow.set_free_limit)
    await c.message.answer("✏️ Yangi bepul limit (raqam):", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminFlow.set_free_limit, F.text.regexp(r"^\d+$"))
async def admin_set_free_limit(m: Message, state: FSMContext):
    sset("free_limit", int(m.text))
    await state.clear()
    await m.answer(f"✅ Bepul limit: <b>{int(m.text):,}</b>", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "set_price")
async def cb_set_price(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminFlow.set_price)
    await c.message.answer("✏️ 1000 zayavka uchun narx (so'm):", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminFlow.set_price, F.text.regexp(r"^\d+$"))
async def admin_set_price(m: Message, state: FSMContext):
    sset("price_per_1k", int(m.text))
    await state.clear()
    await m.answer(f"✅ Narx: <b>{int(m.text):,}</b> so'm / 1000",
                   reply_markup=admin_menu_kb())


@router.message(F.text == "💳 Karta")
async def msg_card(m: Message):
    if not is_admin(m.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Raqam: {gset('card_number')}",
                              callback_data="set_card_number")],
        [InlineKeyboardButton(text=f"👤 Egasi: {gset('card_holder')}",
                              callback_data="set_card_holder")],
    ])
    await m.answer("💳 <b>Karta sozlamalari</b>", reply_markup=kb)


@router.callback_query(F.data == "set_card_number")
async def cb_set_card_number(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminFlow.set_card_number)
    await c.message.answer("✏️ Yangi karta raqami:", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminFlow.set_card_number)
async def admin_set_card_number(m: Message, state: FSMContext):
    sset("card_number", m.text)
    await state.clear()
    await m.answer(f"✅ Karta raqami yangilandi", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "set_card_holder")
async def cb_set_card_holder(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminFlow.set_card_holder)
    await c.message.answer("✏️ Karta egasi ismi:", reply_markup=cancel_kb())
    await c.answer()


@router.message(AdminFlow.set_card_holder)
async def admin_set_card_holder(m: Message, state: FSMContext):
    sset("card_holder", m.text)
    await state.clear()
    await m.answer(f"✅ Karta egasi yangilandi", reply_markup=admin_menu_kb())


@router.message(F.text == "📝 Matnlar")
async def msg_texts(m: Message):
    if not is_admin(m.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👋 Xush kelibsiz matni", callback_data="edit_welcome")],
        [InlineKeyboardButton(text="🆘 Yordam matni", callback_data="edit_help")],
    ])
    await m.answer("📝 <b>Matnlarni tahrirlash</b>", reply_markup=kb)


@router.callback_query(F.data == "edit_welcome")
async def cb_edit_welcome(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminFlow.edit_welcome)
    await c.message.answer(
        f"Hozirgi matn:\n\n{gset('welcome_text')}\n\n✏️ Yangi matnni yuboring (HTML qo'llab-quvvatlanadi):",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminFlow.edit_welcome)
async def admin_edit_welcome(m: Message, state: FSMContext):
    sset("welcome_text", m.html_text)
    await state.clear()
    await m.answer("✅ Yangilandi", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "edit_help")
async def cb_edit_help(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminFlow.edit_help)
    await c.message.answer(
        f"Hozirgi matn:\n\n{gset('help_text')}\n\n✏️ Yangi matnni yuboring:",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminFlow.edit_help)
async def admin_edit_help(m: Message, state: FSMContext):
    sset("help_text", m.html_text)
    await state.clear()
    await m.answer("✅ Yangilandi", reply_markup=admin_menu_kb())


@router.message(F.text == "📢 Majburiy obuna")
async def msg_mandatory(m: Message):
    if not is_admin(m.from_user.id):
        return
    chs = mandatory_list()
    text = "📢 <b>Majburiy obuna kanallari:</b>\n\n"
    if not chs:
        text += "❌ Hech narsa yo'q"
    else:
        for ch in chs:
            text += f"• <b>{ch['chat_title']}</b> (<code>{ch['chat_id']}</code>)\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_mand")],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data="del_mand")],
    ])
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data == "add_mand")
async def cb_add_mand(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminFlow.add_mandatory)
    await c.message.answer(
        "➕ Botni avval shu kanalga admin qiling, keyin kanaldan istalgan xabarni shu yerga forward qiling.",
        reply_markup=cancel_kb(),
    )
    await c.answer()


@router.message(AdminFlow.add_mandatory, F.forward_from_chat)
async def admin_add_mand(m: Message, state: FSMContext):
    ch = m.forward_from_chat
    if ch.type not in ("channel", "supergroup"):
        await m.answer("⚠️ Faqat kanal yoki guruh bo'lishi kerak")
        return
    try:
        link = await bot.export_chat_invite_link(ch.id)
    except Exception:
        link = f"https://t.me/{ch.username}" if ch.username else None
    add_mandatory(ch.id, ch.title, ch.username, link)
    await state.clear()
    await m.answer(f"✅ <b>{ch.title}</b> majburiy ro'yxatga qo'shildi",
                   reply_markup=admin_menu_kb())


@router.message(AdminFlow.add_mandatory)
async def admin_add_mand_invalid(m: Message):
    await m.answer("⚠️ Iltimos kanaldan xabar forward qiling")


@router.callback_query(F.data == "del_mand")
async def cb_del_mand(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    chs = mandatory_list()
    if not chs:
        await c.answer("Bo'sh", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=f"🗑 {ch['chat_title']}",
                                   callback_data=f"del_mand_id:{ch['chat_id']}")] for ch in chs]
    rows.append([InlineKeyboardButton(text="❌ Bekor", callback_data="cancel")])
    await c.message.answer("Qaysi birini o'chiramiz?",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@router.callback_query(F.data.startswith("del_mand_id:"))
async def cb_del_mand_id(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return
    cid = int(c.data.split(":")[1])
    remove_mandatory(cid)
    try:
        await c.message.edit_text("✅ O'chirildi")
    except Exception:
        pass
    await c.answer("O'chirildi")


# ============================ BROADCAST ============================
@router.message(F.text == "📨 Userlarga xabar")
async def msg_bcast_users(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.set_state(AdminFlow.broadcast_users)
    await m.answer(
        "📨 Foydalanuvchilarga yuboriladigan xabarni yuboring "
        "(matn, rasm, video, sticker — har xil format).",
        reply_markup=cancel_kb(),
    )


@router.message(AdminFlow.broadcast_users)
async def admin_bcast_users(m: Message, state: FSMContext):
    await state.clear()
    ids = all_user_ids()
    await m.answer(f"📤 Yuborish boshlandi... ({len(ids)} foydalanuvchi)",
                   reply_markup=admin_menu_kb())
    ok = fail = 0
    for uid in ids:
        try:
            await m.copy_to(uid)
            ok += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)  # flood protection
    await m.answer(f"✅ Yuborildi: <b>{ok}</b>\n❌ Xato: <b>{fail}</b>")


@router.message(F.text == "📡 Kanallarga xabar")
async def msg_bcast_channels(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.set_state(AdminFlow.broadcast_channels)
    await m.answer(
        "📡 Bot admin bo'lgan barcha kanallarga yuboriladigan xabarni yuboring.",
        reply_markup=cancel_kb(),
    )


@router.message(AdminFlow.broadcast_channels)
async def admin_bcast_channels(m: Message, state: FSMContext):
    await state.clear()
    chs = all_bot_channels()
    await m.answer(f"📤 Yuborish boshlandi... ({len(chs)} kanal)",
                   reply_markup=admin_menu_kb())
    ok = fail = 0
    for ch in chs:
        try:
            await m.copy_to(ch["chat_id"])
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.1)
    await m.answer(f"✅ Yuborildi: <b>{ok}</b>\n❌ Xato: <b>{fail}</b>")


@router.message(F.text == "💰 To'lovlar")
async def msg_payments(m: Message):
    if not is_admin(m.from_user.id):
        return
    with closing(db()) as c:
        rows = c.execute("""SELECT * FROM payments WHERE status='pending'
                            ORDER BY created_at DESC LIMIT 10""").fetchall()
    if not rows:
        await m.answer("Yangi to'lov yo'q")
        return
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Tasdiq", callback_data=f"pay_ok:{r['id']}"),
            InlineKeyboardButton(text="❌ Rad", callback_data=f"pay_no:{r['id']}"),
        ]])
        try:
            await bot.send_photo(
                m.from_user.id, r["receipt_file_id"],
                caption=(f"💰 To'lov #{r['id']}\n"
                         f"👤 User: <code>{r['user_id']}</code>\n"
                         f"💵 Summa: <b>{r['amount']:,}</b> so'm"),
                reply_markup=kb,
            )
        except Exception as e:
            log.warning(f"Send pay failed: {e}")


# ============================ MAIN ============================
async def main():
    init_db()
    log.info("Bot ishga tushdi")
    try:
        await bot.send_message(ADMIN_ID, "✅ Bot ishga tushdi!")
    except Exception:
        pass
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message", "callback_query",
            "chat_join_request", "my_chat_member", "chat_member",
        ],
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot to'xtatildi")