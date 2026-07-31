# -*- coding: utf-8 -*-
"""
CS 1.6 Server uchun Telegram bot.

Buyruqlar:
  /info                      -> serverdagi map, ip, o'yinchilar, killar
  .ban <@user|reply> <vaqt> <sabab>
  .kick <@user|reply> <sabab>
  .mute <@user|reply> <vaqt> <sabab>
  .pin      (reply qilingan xabarni pin qiladi)
  .del      (reply qilingan xabarni o'chiradi)

Admin ierarxiyasi admins.json faylida saqlanadi:
  {"admins": {"<user_id>": level}}
Level qancha katta bo'lsa, admin shuncha "kuchli".
Bot egasi (OWNER_ID) hech qachon target qilinmaydi va u eng yuqori darajaga ega.
Bir admin boshqa adminni faqat o'zidan PASTROQ levelga ega bo'lsagina ban/kick/mute qila oladi.
"""

import json
import logging
import re
import os
from datetime import timedelta, datetime, timezone
from fastapi import FastAPI
import uvicorn
import threading
import asyncio

import a2s
from telegram import Update, Chat
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import config

# FastAPI app for Render
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "Bot is running"}

@app.head("/")
async def root_head():
    return {"status": "Bot is running"}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

OWNER_LEVEL = 999999  # bot egasi uchun ichki daraja (json ga yozilmaydi)


# ---------------------------------------------------------------------------
# admins.json bilan ishlash
# ---------------------------------------------------------------------------

def _admins_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), config.ADMINS_FILE)


def load_admins() -> dict:
    path = _admins_path()
    if not os.path.exists(path):
        return {"admins": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_admins(data: dict) -> None:
    with open(_admins_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_level(user_id: int) -> int:
    """Foydalanuvchining admin darajasini qaytaradi. Admin bo'lmasa 0."""
    if user_id == config.OWNER_ID:
        return OWNER_LEVEL
    data = load_admins()
    return int(data.get("admins", {}).get(str(user_id), 0))


def is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_ID


def can_moderate(actor_id: int, target_id: int) -> tuple[bool, str]:
    """
    actor_id, target_id ustida amal (ban/kick/mute) bajara oladimi tekshiradi.
    Qaytaradi: (ruxsat_bormi, xato_matni)
    """
    if target_id == config.OWNER_ID:
        return False, "❌ Bot egasini hech kim ban/kick/mute qila olmaydi!"

    if actor_id == target_id:
        return False, "❌ O'zingizni ban/kick/mute qila olmaysiz."

    actor_level = get_level(actor_id)
    if actor_level <= 0 and not is_owner(actor_id):
        return False, "❌ Sizda admin huquqi yo'q."

    target_level = get_level(target_id)

    if is_owner(actor_id):
        return True, ""

    if actor_level > target_level:
        return True, ""

    return False, "❌ Bu foydalanuvchi sizdan yuqori yoki teng darajali admin, uni ban/kick/mute qila olmaysiz."


# ---------------------------------------------------------------------------
# Yordamchi funksiyalar
# ---------------------------------------------------------------------------

DURATION_RE = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str):
    """
    '10m', '2h', '1d', '3w' kabi vaqtlarni timedelta ga o'giradi.
    'doim' / 'permanent' / '0' -> None (cheksiz muddat)
    Noto'g'ri format -> False
    """
    text = text.strip().lower()
    if text in ("doim", "doimiy", "permanent", "forever", "0"):
        return None
    m = DURATION_RE.match(text)
    if not m:
        return False
    num, unit = m.groups()
    return timedelta(seconds=int(num) * UNIT_SECONDS[unit])


def fmt_duration(td: timedelta | None) -> str:
    if td is None:
        return "doimiy"
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}k")
    if hours:
        parts.append(f"{hours}s")
    if minutes:
        parts.append(f"{minutes}d")
    return " ".join(parts) if parts else "0 daqiqa"


async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]):
    """
    Target foydalanuvchi va qolgan argumentlarni aniqlaydi.
    Ustuvorlik: reply qilingan xabar > @username / user_id argument.
    Qaytaradi: (target_user, rest_args) yoki (None, None) agar topilmasa.
    """
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        return update.message.reply_to_message.from_user, args

    if not args:
        return None, None

    first = args[0]
    rest = args[1:]

    if first.startswith("@"):
        username = first[1:]
        try:
            chat = await context.bot.get_chat(f"@{username}")
            return chat, rest
        except Exception:
            return None, None

    if first.isdigit():
        try:
            chat = await context.bot.get_chat(int(first))
            return chat, rest
        except Exception:
            # get_chat ishlamasa ham, faqat id bilan amal bajarish uchun
            class Dummy:
                pass
            d = Dummy()
            d.id = int(first)
            d.first_name = first
            d.username = None
            return d, rest

    return None, None


def only_group(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await update.message.reply_text("❌ Bu buyruq faqat guruhlarda ishlaydi.")
            return
        return await func(update, context)
    return wrapper


def mention(user) -> str:
    name = getattr(user, "first_name", None) or getattr(user, "username", None) or str(user.id)
    return f'<a href="tg://user?id={user.id}">{name}</a>'


# ---------------------------------------------------------------------------
# /info -- CS 1.6 server holati
# ---------------------------------------------------------------------------

async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = (config.SERVER_IP, config.SERVER_PORT)
    msg = await update.message.reply_text("⏳ Server holati tekshirilmoqda...")

    try:
        info = a2s.info(address, timeout=4)
    except Exception as e:
        logger.warning(f"a2s.info xato: {e}")
        await msg.edit_text(
            f"❌ Serverga ulanib bo'lmadi.\n"
            f"IP: <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    players = []
    try:
        players = a2s.players(address, timeout=4)
    except Exception as e:
        logger.warning(f"a2s.players xato: {e}")

    text = (
        "🎮 <b>CS 1.6 SERVER MA'LUMOTI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🖥 <b>Server:</b> {info.server_name}\n"
        f"🗺 <b>Karta:</b> <code>{info.map_name}</code>\n"
        f"🌐 <b>IP:</b> <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>\n"
        f"👥 <b>O'yinchilar:</b> {info.player_count}/{info.max_players}\n"
    )
    password_text = "Ha" if info.password_protected else "Yo'q"
    text += f"🔒 <b>Parol:</b> {password_text}\n"

    if players:
        # faqat haqiqiy o'yinchilarni (ismi bo'sh bo'lmagan) chiqaramiz, kill bo'yicha saralaymiz
        real_players = [p for p in players if p.name]
        real_players.sort(key=lambda p: p.score, reverse=True)
        if real_players:
            text += "\n💀 <b>Killar (frag) bo'yicha:</b>\n"
            for i, p in enumerate(real_players, start=1):
                minutes = int(p.duration // 60)
                text += f"{i}. <b>{p.name}</b> — {p.score} kill, {minutes} daq\n"
        else:
            text += "\nHozircha serverda o'yinchi yo'q.\n"
    else:
        text += "\nHozircha serverda o'yinchi yo'q.\n"

    text += "━━━━━━━━━━━━━━━━━━"

    await msg.edit_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# .ban
# ---------------------------------------------------------------------------

@only_group
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text(
            "⚠️ Foydalanish: <code>.ban @user 1d sabab</code> yoki xabarga reply qilib <code>.ban 1d sabab</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ok, err = can_moderate(actor.id, target.id)
    if not ok:
        await update.message.reply_text(err)
        return

    if not rest:
        await update.message.reply_text("⚠️ Vaqt va sababni ko'rsating. Masalan: <code>1d spam</code>", parse_mode=ParseMode.HTML)
        return

    duration = parse_duration(rest[0])
    if duration is False:
        await update.message.reply_text(
            "⚠️ Vaqt formati noto'g'ri. Namuna: 10m, 2h, 1d, 1w yoki 'doim'."
        )
        return

    reason = " ".join(rest[1:]) if len(rest) > 1 else "sabab ko'rsatilmagan"

    until_date = None
    if duration is not None:
        until_date = datetime.now(timezone.utc) + duration

    try:
        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            until_date=until_date,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ban qilib bo'lmadi: {e}")
        return

    await update.message.reply_text(
        f"🔨 <b>BAN QILINDI</b>\n"
        f"👤 Kim: {mention(target)}\n"
        f"⏱ Muddat: {fmt_duration(duration)}\n"
        f"📝 Sabab: {reason}\n"
        f"👮 Admin: {mention(actor)}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# .kick
# ---------------------------------------------------------------------------

@only_group
async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text(
            "⚠️ Foydalanish: <code>.kick @user sabab</code> yoki xabarga reply qilib <code>.kick sabab</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ok, err = can_moderate(actor.id, target.id)
    if not ok:
        await update.message.reply_text(err)
        return

    reason = " ".join(rest) if rest else "sabab ko'rsatilmagan"

    try:
        await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=target.id)
        await context.bot.unban_chat_member(chat_id=update.effective_chat.id, user_id=target.id, only_if_banned=True)
    except Exception as e:
        await update.message.reply_text(f"❌ Kick qilib bo'lmadi: {e}")
        return

    await update.message.reply_text(
        f"👢 <b>KICK QILINDI</b>\n"
        f"👤 Kim: {mention(target)}\n"
        f"📝 Sabab: {reason}\n"
        f"👮 Admin: {mention(actor)}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# .mute
# ---------------------------------------------------------------------------

@only_group
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text(
            "⚠️ Foydalanish: <code>.mute @user 1h sabab</code> yoki xabarga reply qilib <code>.mute 1h sabab</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ok, err = can_moderate(actor.id, target.id)
    if not ok:
        await update.message.reply_text(err)
        return

    if not rest:
        await update.message.reply_text("⚠️ Vaqt va sababni ko'rsating. Masalan: <code>1h spam</code>", parse_mode=ParseMode.HTML)
        return

    duration = parse_duration(rest[0])
    if duration is False:
        await update.message.reply_text("⚠️ Vaqt formati noto'g'ri. Namuna: 10m, 2h, 1d, 1w yoki 'doim'.")
        return

    reason = " ".join(rest[1:]) if len(rest) > 1 else "sabab ko'rsatilmagan"

    until_date = None
    if duration is not None:
        until_date = datetime.now(timezone.utc) + duration

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            permissions=__import__("telegram").ChatPermissions(can_send_messages=False),
            until_date=until_date,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Mute qilib bo'lmadi: {e}")
        return

    await update.message.reply_text(
        f"🔇 <b>MUTE QILINDI</b>\n"
        f"👤 Kim: {mention(target)}\n"
        f"⏱ Muddat: {fmt_duration(duration)}\n"
        f"📝 Sabab: {reason}\n"
        f"👮 Admin: {mention(actor)}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# .pin
# ---------------------------------------------------------------------------

@only_group
async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if get_level(actor.id) <= 0 and not is_owner(actor.id):
        await update.message.reply_text("❌ Sizda admin huquqi yo'q.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Pin qilish uchun xabarga reply qiling: <code>.pin</code>", parse_mode=ParseMode.HTML)
        return

    try:
        await context.bot.pin_chat_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.reply_to_message.message_id,
        )
        await update.message.reply_text("📌 Xabar pin qilindi.")
    except Exception as e:
        await update.message.reply_text(f"❌ Pin qilib bo'lmadi: {e}")


# ---------------------------------------------------------------------------
# .del
# ---------------------------------------------------------------------------

@only_group
async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if get_level(actor.id) <= 0 and not is_owner(actor.id):
        await update.message.reply_text("❌ Sizda admin huquqi yo'q.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ O'chirish uchun xabarga reply qiling: <code>.del</code>", parse_mode=ParseMode.HTML)
        return

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.reply_to_message.message_id,
        )
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ O'chirib bo'lmadi: {e}")


# ---------------------------------------------------------------------------
# '.' bilan boshlanadigan buyruqlarni yo'naltirish
# ---------------------------------------------------------------------------

DOT_COMMANDS = {
    ".ban": ban_cmd,
    ".kick": kick_cmd,
    ".mute": mute_cmd,
    ".pin": pin_cmd,
    ".del": del_cmd,
}


async def dot_command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    parts = text.split()
    if not parts:
        return
    cmd = parts[0].lower()
    if cmd in DOT_COMMANDS:
        context.args = parts[1:]
        await DOT_COMMANDS[cmd](update, context)


# ---------------------------------------------------------------------------
# Ishga tushirish
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salom! Men CS 1.6 server boti.\n\n"
        "/info — server holatini ko'rsataman\n\n"
        "Guruh adminlari uchun:\n"
        "<code>.ban @user vaqt sabab</code>\n"
        "<code>.kick @user sabab</code>\n"
        "<code>.mute @user vaqt sabab</code>\n"
        "<code>.pin</code> (reply qilib)\n"
        "<code>.del</code> (reply qilib)",
        parse_mode=ParseMode.HTML,
    )


async def run_bot_async():
    """Telegram botni asinxron ishga tushiradi"""
    if config.BOT_TOKEN == "BOT_TOKEN_BU_YERGA":
        print("❌ config.py faylida BOT_TOKEN ni to'g'ri kiritmagansiz!")
        return
    if config.OWNER_ID == 0:
        print("❌ config.py faylida OWNER_ID ni to'g'ri kiritmagansiz!")
        return

    # Application obyektini yaratamiz
    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("info", info_cmd))

    # '.' bilan boshlanuvchi buyruqlar uchun umumiy router
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dot_command_router))

    logger.info("Bot ishga tushdi...")
    
    # Polling orqali ishga tushiramiz
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Botni ishlab turishi uchun
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def run_bot():
    """Telegram botni threadda ishga tushirish"""
    # Yangi event loop yaratamiz
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(run_bot_async())
    finally:
        loop.close()


if __name__ == "__main__":
    # 1. Telegram botni alohida threadda ishga tushiramiz
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("Telegram bot Long Polling rejimida ishga tushdi...")
    
    # 2. Render portni tinglashi uchun FastAPI veb-serverini yurgizamiz
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
