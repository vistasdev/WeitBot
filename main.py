# -*- coding: utf-8 -*-
"""
CS 1.6 Server uchun FULL Telegram Bot
Versiya: 4.0 - Rolega qarab menyu (user/admin/owner), xavfsizlik yaxshilandi,
                UI/UX chiroyli qilindi.
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
import time
import socket
from typing import List, Optional
from dataclasses import dataclass, asdict, field

import a2s
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    filters, CallbackQueryHandler, ChatMemberHandler
)

import config

# FastAPI app
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "Bot is running", "name": "CS 1.6 Server Bot"}

@app.head("/")
async def root_head():
    return {"status": "Bot is running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# ---------------------------------------------------------------------------
# DEBUG endpoint - server ulanish muammosini aniqlash uchun
# ---------------------------------------------------------------------------

@app.get("/debug-server")
async def debug_server():
    result = {"ip": config.SERVER_IP, "port": config.SERVER_PORT}

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(4.0)
        sock.sendto(b"\xFF\xFF\xFF\xFFTSource Engine Query\x00", (config.SERVER_IP, config.SERVER_PORT))
        data, addr = sock.recvfrom(4096)
        result["raw_udp"] = f"OK, {len(data)} bytes qaytdi, addr={addr}"
        sock.close()
    except Exception as e:
        result["raw_udp"] = f"XATO: {type(e).__name__}: {e}"

    try:
        info = a2s.info((config.SERVER_IP, config.SERVER_PORT), timeout=5.0)
        result["a2s_info"] = f"OK: {info.server_name} | map={info.map_name} | players={info.player_count}/{info.max_players}"
    except Exception as e:
        result["a2s_info"] = f"XATO: {type(e).__name__}: {e}"

    try:
        players = a2s.players((config.SERVER_IP, config.SERVER_PORT), timeout=5.0)
        result["a2s_players"] = f"OK: {len(players)} ta o'yinchi"
    except Exception as e:
        result["a2s_players"] = f"XATO: {type(e).__name__}: {e}"

    try:
        import urllib.request
        with urllib.request.urlopen("https://api.ipify.org", timeout=4.0) as resp:
            result["render_outbound_ip"] = resp.read().decode().strip()
    except Exception as e:
        result["render_outbound_ip"] = f"XATO: {type(e).__name__}: {e}"

    return result

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

OWNER_LEVEL = 999999

# ---------------------------------------------------------------------------
# Ma'lumotlar tuzilmalari
# ---------------------------------------------------------------------------

@dataclass
class GroupSettings:
    welcome_enabled: bool = True
    welcome_text: str = "👋 Assalomu alaykum {user}! {group} guruhiga xush kelibsiz!"
    goodbye_enabled: bool = True
    goodbye_text: str = "👋 {user} guruhni tark etdi. Xayr!"
    auto_mute_new: bool = False
    mute_duration: int = 5
    warn_limit: int = 3
    mute_on_warn: bool = True
    ban_on_warn: bool = False
    banned_words: List[str] = field(default_factory=lambda: [])
    banned_links: bool = True
    spam_protection: bool = True
    spam_limit: int = 5
    spam_time: int = 10
    delete_banned: bool = True
    notify_admin: bool = True
    restrict_new: bool = False
    captcha_enabled: bool = False
    # Taqiqlangan so'z uchun jazo: none | warn | mute | kick | ban
    word_action: str = "warn"
    word_mute_minutes: int = 15
    word_ban_duration: str = "doimiy"
    # Link yuborgani uchun jazo: none | warn | mute | kick | ban
    link_action: str = "warn"
    link_mute_minutes: int = 15
    link_ban_duration: str = "doimiy"

@dataclass
class Stats:
    total_commands: int = 0
    total_bans: int = 0
    total_kicks: int = 0
    total_mutes: int = 0
    total_warns: int = 0
    total_messages: int = 0
    active_users: int = 0
    last_update: str = ""

# ---------------------------------------------------------------------------
# Fayl operatsiyalari
# ---------------------------------------------------------------------------

def get_file_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

def load_json(filename: str, default: dict = None) -> dict:
    path = get_file_path(filename)
    if not os.path.exists(path):
        return default or {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default or {}

def save_json(filename: str, data: dict) -> None:
    path = get_file_path(filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# Ma'lumotlarni yuklash/saqlash
# ---------------------------------------------------------------------------

def load_admins() -> dict:
    return load_json(config.ADMINS_FILE, {"admins": {}})

def save_admins(data: dict) -> None:
    save_json(config.ADMINS_FILE, data)

def load_settings(chat_id: int) -> GroupSettings:
    data = load_json("settings.json", {})
    chat_id_str = str(chat_id)
    if chat_id_str not in data:
        return GroupSettings()
    # Eski saqlangan sozlamalarda yangi maydon bo'lmasa ham xato bermasin
    known_fields = {f.name for f in GroupSettings.__dataclass_fields__.values()}
    clean = {k: v for k, v in data[chat_id_str].items() if k in known_fields}
    return GroupSettings(**clean)

def save_settings(chat_id: int, settings: GroupSettings) -> None:
    data = load_json("settings.json", {})
    data[str(chat_id)] = asdict(settings)
    save_json("settings.json", data)

def load_warns() -> dict:
    return load_json("warns.json", {})

def save_warns(data: dict) -> None:
    save_json("warns.json", data)

def load_stats() -> dict:
    return load_json("stats.json", asdict(Stats()))

def save_stats(data: dict) -> None:
    save_json("stats.json", data)

# ---------------------------------------------------------------------------
# Jazo turlari (taqiqlangan so'z / link uchun) - to'liq sozlanadigan
# ---------------------------------------------------------------------------

ACTION_ORDER = ["none", "warn", "mute", "kick", "ban"]
ACTION_LABELS = {
    "none": "🚫 Faqat xabarni o'chirish",
    "warn": "⚠️ Ogohlantirish berish",
    "mute": "🔇 Mute qilish",
    "kick": "👢 Kick qilish",
    "ban": "🔨 Ban qilish",
}

def next_action(current: str) -> str:
    idx = ACTION_ORDER.index(current) if current in ACTION_ORDER else 0
    return ACTION_ORDER[(idx + 1) % len(ACTION_ORDER)]

# ---------------------------------------------------------------------------
# Rol / darajalarni aniqlash (xavfsizlikning yuragi shu yerda)
# ---------------------------------------------------------------------------

def get_level(user_id: int) -> int:
    if user_id == config.OWNER_ID:
        return OWNER_LEVEL
    data = load_admins()
    try:
        return int(data.get("admins", {}).get(str(user_id), 0))
    except (TypeError, ValueError):
        return 0

def is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_ID

def is_admin(user_id: int) -> bool:
    return get_level(user_id) > 0 or is_owner(user_id)

def rank_name(level: int) -> str:
    """Darajaga qarab chiroyli unvon"""
    if level >= OWNER_LEVEL:
        return "👑 Bot egasi"
    if level >= 10:
        return "🛡 Bosh admin"
    if level >= 5:
        return "⭐️ Katta admin"
    if level >= 1:
        return "👮 Admin"
    return "👤 Foydalanuvchi"

def can_moderate(actor_id: int, target_id: int) -> tuple[bool, str]:
    if target_id == config.OWNER_ID:
        return False, "❌ Bot egasini hech kim jazolay olmaydi!"
    if actor_id == target_id:
        return False, "❌ O'zingizni jazolay olmaysiz."

    actor_level = get_level(actor_id)
    if actor_level <= 0 and not is_owner(actor_id):
        return False, "❌ Sizda admin huquqi yo'q."

    target_level = get_level(target_id)
    if is_owner(actor_id):
        return True, ""
    if actor_level > target_level:
        return True, ""

    return False, "❌ Bu foydalanuvchi sizdan yuqori yoki teng darajali admin."

def require_admin(func):
    """Faqat adminlar (yoki owner) ishlata oladigan buyruqlar uchun dekorator.
    Oddiy foydalanuvchiga hech qanday admin buyrug'i haqida ma'lumot bermaydi -
    faqat qisqa rad javobi qaytaradi."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await update.effective_message.reply_text("❌ Bu buyruq faqat adminlar uchun.")
            return
        return await func(update, context)
    return wrapper

def require_owner(func):
    """Faqat bot egasi ishlata oladigan buyruqlar uchun dekorator."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_owner(user.id):
            await update.effective_message.reply_text("❌ Bu buyruq faqat bot egasi uchun.")
            return
        return await func(update, context)
    return wrapper

def only_group(func):
    """Faqat guruhlarda ishlaydigan buyruqlar uchun dekorator"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await update.message.reply_text("❌ Bu buyruq faqat guruhlarda ishlaydi.")
            return
        return await func(update, context)
    return wrapper

# ---------------------------------------------------------------------------
# Yordamchi funksiyalar
# ---------------------------------------------------------------------------

DURATION_RE = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

def parse_duration(text: str):
    text = text.strip().lower()
    if text in ("doim", "doimiy", "permanent", "forever", "0"):
        return None
    m = DURATION_RE.match(text)
    if not m:
        return False
    num, unit = m.groups()
    return timedelta(seconds=int(num) * UNIT_SECONDS[unit])

def fmt_duration(td: Optional[timedelta]) -> str:
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

def mention(user) -> str:
    name = getattr(user, "first_name", None) or getattr(user, "username", None) or str(user.id)
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def get_server_info():
    """Server ma'lumotlarini olish"""
    address = (config.SERVER_IP, config.SERVER_PORT)
    try:
        info = a2s.info(address, timeout=3.0)
        players = []
        try:
            players = a2s.players(address, timeout=3.0)
        except Exception as e:
            logger.warning(f"Players olishda xato: {e}")
        return info, players
    except Exception as e:
        logger.error(f"Server info olishda xato: {e}")
        return None, None

def has_banned_words(text: str, banned_words: List[str]) -> bool:
    if not banned_words:
        return False
    text_lower = text.lower()
    for word in banned_words:
        if word.lower() in text_lower:
            return True
    return False

def has_links(text: str) -> bool:
    url_pattern = re.compile(r'https?://\S+|www\.\S+|[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/\S*)?')
    return bool(url_pattern.search(text))

def add_warn_record(warns_data: dict, user_id: int, reason: str, admin_id: int, admin_name: str) -> int:
    """Warns bazasiga yozuv qo'shadi va jami warnlar sonini qaytaradi."""
    user_id_str = str(user_id)
    if user_id_str not in warns_data:
        warns_data[user_id_str] = {"warns": [], "total_warns": 0}

    warn = {
        "reason": reason,
        "admin_id": admin_id,
        "admin_name": admin_name,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "warn_id": len(warns_data[user_id_str]["warns"]) + 1,
    }
    warns_data[user_id_str]["warns"].append(warn)
    warns_data[user_id_str]["total_warns"] += 1
    return warns_data[user_id_str]["total_warns"]

async def apply_warn_limit_consequence(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                                        target_user, total_warns: int, settings: GroupSettings):
    """warn_limit ga yetilganda .settings dagi ban_on_warn/mute_on_warn bo'yicha
    avtomatik chora ko'radi. Natija: None yoki ("ban", None) / ("mute", daqiqa)."""
    if total_warns < settings.warn_limit:
        return None
    if settings.ban_on_warn:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_user.id)
            return ("ban", None)
        except Exception:
            return None
    elif settings.mute_on_warn:
        try:
            until_date = datetime.now(timezone.utc) + timedelta(minutes=settings.mute_duration)
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=target_user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_date,
            )
            return ("mute", settings.mute_duration)
        except Exception:
            return None
    return None

async def execute_violation_action(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    settings: GroupSettings, user, kind: str, reason: str):
    """Taqiqlangan so'z / link aniqlanganda .settings da tanlangan jazoni qo'llaydi:
    none / warn / mute / kick / ban - har biri to'liq sozlanadigan."""
    chat_id = update.effective_chat.id

    if settings.delete_banned:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except Exception:
            pass

    if settings.notify_admin:
        icon = "🚫" if kind == "word" else "🔗"
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"{icon} <b>{reason}</b>\n👤 Kim: {mention(user)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    if kind == "word":
        action = settings.word_action
        mute_minutes = settings.word_mute_minutes
        ban_duration_str = settings.word_ban_duration
    else:
        action = settings.link_action
        mute_minutes = settings.link_mute_minutes
        ban_duration_str = settings.link_ban_duration

    if action == "none":
        return

    if action == "warn":
        warns_data = load_warns()
        total_warns = add_warn_record(warns_data, user.id, reason, user.id, "Bot (avtomatik)")
        save_warns(warns_data)
        consequence = await apply_warn_limit_consequence(context, chat_id, user, total_warns, settings)
        if consequence:
            c_kind, c_val = consequence
            if c_kind == "ban":
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔨 <b>AVTOMATIK BAN</b>\n{mention(user)} {settings.warn_limit} ta ogohlantirishdan keyin ban qilindi!",
                    parse_mode=ParseMode.HTML,
                )
            elif c_kind == "mute":
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔇 <b>AVTOMATIK MUTE</b>\n{mention(user)} {settings.warn_limit} ta ogohlantirishdan keyin {c_val} daqiqaga mute qilindi!",
                    parse_mode=ParseMode.HTML,
                )
        return

    if action == "mute":
        until_date = datetime.now(timezone.utc) + timedelta(minutes=mute_minutes)
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_date,
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔇 <b>MUTE QILINDI</b>\n👤 {mention(user)}\n⏱ Muddat: {mute_minutes} daqiqa\n📝 Sabab: {reason}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if action == "kick":
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"👢 <b>KICK QILINDI</b>\n👤 {mention(user)}\n📝 Sabab: {reason}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if action == "ban":
        duration = parse_duration(ban_duration_str)
        if duration is False:
            duration = None
        until_date = None if duration is None else datetime.now(timezone.utc) + duration
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id, until_date=until_date)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔨 <b>BAN QILINDI</b>\n👤 {mention(user)}\n⏱ Muddat: {fmt_duration(duration)}\n📝 Sabab: {reason}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

# ---------------------------------------------------------------------------
# /start - rolega qarab tugmalar (admin bo'lmasa, admin tugmalari umuman
# ko'rinmaydi)
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    user = update.effective_user

    # Guruhdan "⚙️ Shaxsiy chatda ochish" tugmasi orqali kelingan bo'lsa —
    # to'g'ridan-to'g'ri o'sha guruhning sozlamalar panelini ochamiz.
    args = context.args or []
    if args and args[0].startswith("settings_"):
        raw_chat_id = args[0][len("settings_"):]
        try:
            target_chat_id = int(raw_chat_id)
        except ValueError:
            target_chat_id = None

        if not is_admin(user.id):
            await update.message.reply_text("❌ Sozlamalarni faqat adminlar boshqara oladi.")
            return
        if target_chat_id is None:
            await update.message.reply_text("❌ Havola noto'g'ri. Guruhda qaytadan <code>.settings</code> deb yozing.", parse_mode=ParseMode.HTML)
            return

        context.user_data["settings_chat_id"] = target_chat_id
        await render_settings_menu(update, context, target_chat_id)
        return

    first_name = user.first_name or "Foydalanuvchi"
    level = get_level(user.id)

    keyboard = [
        [
            InlineKeyboardButton("📊 Server holati", callback_data="info"),
            InlineKeyboardButton("🏓 Ping", callback_data="ping")
        ],
        [
            InlineKeyboardButton("📜 Qoidalar", callback_data="rules"),
            InlineKeyboardButton("💬 Yordam", callback_data="help")
        ],
    ]

    # Faqat adminlarga ko'rinadigan tugma
    if is_admin(user.id):
        keyboard.append([
            InlineKeyboardButton("👥 Adminlar", callback_data="admins"),
        ])

    keyboard.append([
        InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{context.bot.username}?startgroup=true"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    role_line = f"🎖 Sizning darajangiz: <b>{rank_name(level)}</b>\n\n" if is_admin(user.id) else ""
    settings_hint = (
        "\n⚙️ Guruh sozlamalarini boshqarish uchun o'sha guruhda <code>.settings</code> deb yozing — "
        "men sizga shu yerga (shaxsiy chatga) havola yuboraman.\n"
        if is_admin(user.id) else ""
    )

    text = (
        f"🎯 <b>Assalomu alaykum, {first_name}!</b>\n\n"
        f"{role_line}"
        "Men <b>CS 1.6</b> serveringiz uchun <b>FULL</b> yordamchi botman.\n\n"
        "🛡 <b>Mening imkoniyatlarim:</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✅ Guruh boshqaruvi\n"
        "✅ Avtomatik salomlashish\n"
        "✅ Taqiqlangan so'zlar\n"
        "✅ Link bloklash\n"
        "✅ Spam himoyasi\n"
        "✅ Ogohlantirish tizimi\n"
        "✅ CS 1.6 server ma'lumotlari\n"
        f"{settings_hint}\n"
        "💡 Guruhga qo'shib, meni <b>ADMIN</b> qiling!\n"
        "📋 Barcha buyruqlar uchun: <code>.help</code>"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

# ---------------------------------------------------------------------------
# .help — rolega qarab: oddiy user admin buyruqlarini UMUMAN ko'rmaydi
# ---------------------------------------------------------------------------

USER_HELP_TEXT = (
    "📚 <b>Yordam menyusi</b>\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "🎮 <b>Buyruqlar:</b>\n"
    "• <code>/start</code> - Botni ishga tushirish (shaxsiy chatda)\n"
    "• <code>.help</code> - Yordam\n"
    "• <code>.info</code> - Server holati\n"
    "• <code>.ping</code> - Server ping\n"
    "• <code>.rules</code> - Server qoidalari\n"
    "• <code>.warns</code> - O'zingizning ogohlantirishlaringiz\n"
)

ADMIN_HELP_EXTRA = (
    "\n👮 <b>Admin buyruqlari:</b>\n"
    "• <code>.ban @user 1d sabab</code> - Ban\n"
    "• <code>.kick @user sabab</code> - Kick\n"
    "• <code>.mute @user 1h sabab</code> - Mute\n"
    "• <code>.unmute @user</code> - Unmute\n"
    "• <code>.warn @user sabab</code> - Ogohlantirish\n"
    "• <code>.warns @user</code> - Kimningdir ogohlantirishlari\n"
    "• <code>.clearwarns @user</code> - Tozalash\n"
    "• <code>.pin</code> (reply) - Pin qilish\n"
    "• <code>.del</code> (reply) - O'chirish\n"
    "• <code>.addword so'z</code> - Taqiqlangan so'z qo'shish\n"
    "• <code>.delword so'z</code> - Taqiqlangan so'z o'chirish\n"
    "• <code>.settings</code> - Guruh sozlamalari\n"
    "• <code>.admins</code> - Adminlar ro'yxati\n\n"
    "🎨 <b>Moslashtirish:</b>\n"
    "• <code>.setwelcome matn</code> - Salomlashish matnini o'zgartirish\n"
    "• <code>.setgoodbye matn</code> - Xayrlashish matnini o'zgartirish\n"
    "• <code>.setwordaction none|warn|mute|kick|ban</code> - So'z uchun jazo\n"
    "• <code>.setlinkaction none|warn|mute|kick|ban</code> - Link uchun jazo\n"
    "• <code>.setwordmute 15</code> - So'z uchun mute (daqiqa)\n"
    "• <code>.setlinkmute 15</code> - Link uchun mute (daqiqa)\n"
    "• <code>.setwordban 1d</code> - So'z uchun ban muddati\n"
    "• <code>.setlinkban 1d</code> - Link uchun ban muddati\n"
)

OWNER_HELP_EXTRA = (
    "\n👑 <b>Bot egasi buyruqlari:</b>\n"
    "• <code>.addadmin @user level</code> - Admin qo'shish\n"
    "• <code>.removeadmin @user</code> - Admin o'chirish\n"
)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = USER_HELP_TEXT
    if is_admin(user.id):
        text += ADMIN_HELP_EXTRA
    if is_owner(user.id):
        text += OWNER_HELP_EXTRA

    text += f"\n🎖 Sizning darajangiz: <b>{rank_name(get_level(user.id))}</b>"

    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# .rules - Guruh qoidalari
# ---------------------------------------------------------------------------

RULES_TEXT = (
    "📜 <b>WEIT CS | SERVER QOIDALARI</b>\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "Hurmatli o'yinchilar! Serverimizda tartib va adolatni saqlash uchun "
    "quyidagi qoidalarga amal qiling.\n\n"
    "<b>1. Hurmat</b>\n"
    "• Barcha o'yinchilarni hurmat qiling.\n"
    "• So'kinish, haqorat, irqchilik va diniy yoki milliy kamsitish taqiqlanadi.\n\n"
    "<b>2. Cheat va Hack</b>\n"
    "• Har qanday cheat, WH, AIM, SpeedHack, Script va boshqa noqonuniy "
    "dasturlardan foydalanish qat'iyan taqiqlanadi.\n"
    "• Aniqlangan taqdirda doimiy (Permanent) ban beriladi.\n\n"
    "<b>3. Reklama</b>\n"
    "• Boshqa serverlar, Telegram guruhlari yoki ijtimoiy tarmoqlarni "
    "reklama qilish taqiqlanadi.\n\n"
    "<b>4. Mikrofon va Chat</b>\n"
    "• Mikrofonni behuda ishlatmang.\n"
    "• Flood, spam va bir xil xabarni qayta-qayta yozish taqiqlanadi.\n\n"
    "<b>5. Nickname</b>\n"
    "• Haqoratli, nomaqbul yoki administrator nomidan foydalanish taqiqlanadi.\n\n"
    "<b>6. O'yin Jarayoni</b>\n"
    "• O'yinni ataylab buzish, jamoadoshlarni bloklash yoki zarar yetkazish taqiqlanadi.\n"
    "• Serverdagi bug va xatolardan foydalanish mumkin emas.\n\n"
    "<b>7. Administratorlar</b>\n"
    "• Administrator qarorlariga hurmat bilan yondashing.\n"
    "• Agar norozi bo'lsangiz, Telegram guruhimiz orqali murojaat qiling.\n\n"
    "<b>8. VIP va Boshqa Imtiyozlar</b>\n"
    "• VIP yoki boshqa xizmatlarni tekin so'ramang.\n"
    "• Barcha xizmatlar belgilangan tartibda beriladi.\n\n"
    "<b>9. Ban Masalalari</b>\n"
    "• Ban olgan bo'lsangiz, boshqa akkaunt bilan kirib ban'dan qochishga urinmang.\n"
    "• Apellyatsiya faqat rasmiy aloqa orqali ko'rib chiqiladi.\n\n"
    "<b>10. Eng Muhimi</b>\n"
    "Serverda do'stona muhitni saqlang, qoidalarga amal qiling va o'yindan zavqlaning!\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🎮 <b>WEIT CS</b> – Halol o'yin, yaxshi atmosfera va kuchli hamjamiyat!\n"
    "Qoidalarga amal qilmagan o'yinchilar ogohlantiriladi yoki banlanadi."
)

async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📢 Asosiy kanal", url="https://t.me/weit_cs")]
    ]
    if update.callback_query:
        await update.callback_query.message.edit_text(
            RULES_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )
    else:
        await update.effective_message.reply_text(
            RULES_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )

# ---------------------------------------------------------------------------
# .settings - faqat botning SHAXSIY chatida ishlaydi. Guruhda yozilsa,
# adminga shaxsiy chatga o'tuvchi havola beriladi (guruh sozlamalar bilan
# to'lib qolmasligi uchun).
# ---------------------------------------------------------------------------

async def render_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    context.user_data["settings_chat_id"] = chat_id

    keyboard = [
        [InlineKeyboardButton("👋 Salomlashish", callback_data="settings_welcome")],
        [InlineKeyboardButton("🚫 Taqiqlangan so'zlar", callback_data="settings_banned_words")],
        [InlineKeyboardButton("🔗 Link bloklash", callback_data="settings_links")],
        [InlineKeyboardButton("🛡 Spam himoyasi", callback_data="settings_spam")],
        [InlineKeyboardButton("⚠️ Ogohlantirish tizimi", callback_data="settings_warns")],
        [InlineKeyboardButton("🆕 Yangi a'zolar", callback_data="settings_new_users")],
        [InlineKeyboardButton("📊 Joriy holat", callback_data="settings_status")],
        [InlineKeyboardButton("🔙 Bosh menyu", callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "⚙️ <b>Guruh sozlamalari</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Kerakli bo'limni tanlang:"
    )

    if update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

@require_admin
async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        chat_id = update.effective_chat.id
        keyboard = [
            [InlineKeyboardButton(
                "⚙️ Shaxsiy chatda ochish",
                url=f"https://t.me/{context.bot.username}?start=settings_{chat_id}"
            )]
        ]
        await update.effective_message.reply_text(
            "⚙️ <b>Sozlamalar endi shaxsiy chatda boshqariladi</b>\n\n"
            "Guruhni ortiqcha xabarlar bilan to'ldirmaslik uchun sozlamalar paneli botning "
            "shaxsiy chatiga ko'chirildi. Pastdagi tugmani bosing.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Shaxsiy chat: oldin biror guruhdan havola orqali kirilganmi tekshiramiz
    chat_id = context.user_data.get("settings_chat_id")
    if chat_id is None:
        await update.effective_message.reply_text(
            "⚠️ Qaysi guruh sozlamalarini ochish kerakligini bilmayapman.\n"
            "Iltimos, kerakli guruhda <code>.settings</code> deb yozing — men sizga shu yerga "
            "(shaxsiy chatga) to'g'ridan-to'g'ri havola yuboraman.",
            parse_mode=ParseMode.HTML,
        )
        return

    await render_settings_menu(update, context, chat_id)

# ---------------------------------------------------------------------------
# Settings callback handler - har bosishda admin ekanligi qayta tekshiriladi
# (aks holda link orqali kirib qolgan oddiy user tugmalarni bosib sozlamani
# o'zgartirib qo'yishi mumkin edi)
# ---------------------------------------------------------------------------

async def render_banned_words_settings(message, settings: GroupSettings):
    text = (
        "🚫 <b>Taqiqlangan so'zlar</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📝 Taqiqlangan so'zlar:\n"
    )
    if settings.banned_words:
        for word in settings.banned_words:
            text += f"• <code>{word}</code>\n"
    else:
        text += "❌ Hech qanday so'z taqiqlanmagan\n\n"

    text += (
        f"\n⚖️ <b>Jazo turi:</b> {ACTION_LABELS[settings.word_action]}\n"
        f"🔇 Mute vaqti: <b>{settings.word_mute_minutes}</b> daqiqa\n"
        f"🔨 Ban muddati: <b>{fmt_duration(parse_duration(settings.word_ban_duration))}</b>\n\n"
    )
    text += "➕ So'z qo'shish: <code>.addword so'z</code>\n"
    text += "➖ So'z o'chirish: <code>.delword so'z</code>\n\n"
    text += "🔧 <code>.setwordaction none|warn|mute|kick|ban</code>\n"
    text += "🔧 <code>.setwordmute 15</code> — mute daqiqasi\n"
    text += "🔧 <code>.setwordban 1d</code> — ban muddati"

    keyboard = [
        [InlineKeyboardButton(f"🔁 Jazo: {ACTION_LABELS[settings.word_action]}", callback_data="settings_word_action_cycle")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
    ]
    await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def render_links_settings(message, settings: GroupSettings):
    text = (
        "🔗 <b>Link bloklash</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"Link bloklash: {'✅ Yoqilgan' if settings.banned_links else '❌ O\'chirilgan'}\n"
        f"Xabarni o'chirish: {'✅ Ha' if settings.delete_banned else '❌ Yo\'q'}\n"
        f"Adminni xabardor qilish: {'✅ Ha' if settings.notify_admin else '❌ Yo\'q'}\n\n"
        f"⚖️ <b>Jazo turi:</b> {ACTION_LABELS[settings.link_action]}\n"
        f"🔇 Mute vaqti: <b>{settings.link_mute_minutes}</b> daqiqa\n"
        f"🔨 Ban muddati: <b>{fmt_duration(parse_duration(settings.link_ban_duration))}</b>\n\n"
        "🔧 <code>.setlinkaction none|warn|mute|kick|ban</code>\n"
        "🔧 <code>.setlinkmute 15</code> — mute daqiqasi\n"
        "🔧 <code>.setlinkban 1d</code> — ban muddati"
    )
    keyboard = [
        [InlineKeyboardButton(f"🔁 Jazo: {ACTION_LABELS[settings.link_action]}", callback_data="settings_link_action_cycle")],
        [InlineKeyboardButton("🔄 Link bloklash", callback_data="settings_links_toggle")],
        [InlineKeyboardButton("🔄 Xabarni o'chirish", callback_data="settings_delete_toggle")],
        [InlineKeyboardButton("🔄 Admin xabari", callback_data="settings_notify_toggle")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
    ]
    await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Bu tugma faqat adminlar uchun.", show_alert=True)
        return

    chat_id = context.user_data.get("settings_chat_id")
    if chat_id is None:
        await query.answer("❌ Sessiya tugadi. Guruhda .settings deb qaytadan yozing.", show_alert=True)
        return

    await query.answer()

    settings = load_settings(chat_id)
    data = query.data
    message = query.message

    if data == "settings_welcome":
        text = (
            "👋 <b>Salomlashish sozlamalari</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"Salomlashish: {'✅ Yoqilgan' if settings.welcome_enabled else '❌ O\'chirilgan'}\n"
            f"Xayrlashish: {'✅ Yoqilgan' if settings.goodbye_enabled else '❌ O\'chirilgan'}\n\n"
            f"<b>Salomlashish matni:</b>\n<code>{settings.welcome_text}</code>\n\n"
            f"<b>Xayrlashish matni:</b>\n<code>{settings.goodbye_text}</code>\n\n"
            "📌 O'zgaruvchilar:\n"
            "<code>{user}</code> - Foydalanuvchi ismi\n"
            "<code>{group}</code> - Guruh nomi\n"
            "<code>{count}</code> - A'zolar soni"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Salomlashish", callback_data="settings_welcome_toggle")],
            [InlineKeyboardButton("🔄 Xayrlashish", callback_data="settings_goodbye_toggle")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
        ]
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings_welcome_toggle":
        settings.welcome_enabled = not settings.welcome_enabled
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_goodbye_toggle":
        settings.goodbye_enabled = not settings.goodbye_enabled
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_banned_words":
        await render_banned_words_settings(message, settings)

    elif data == "settings_word_action_cycle":
        settings.word_action = next_action(settings.word_action)
        save_settings(chat_id, settings)
        await render_banned_words_settings(message, settings)

    elif data == "settings_links":
        await render_links_settings(message, settings)

    elif data == "settings_link_action_cycle":
        settings.link_action = next_action(settings.link_action)
        save_settings(chat_id, settings)
        await render_links_settings(message, settings)

    elif data == "settings_links_toggle":
        settings.banned_links = not settings.banned_links
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_delete_toggle":
        settings.delete_banned = not settings.delete_banned
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_notify_toggle":
        settings.notify_admin = not settings.notify_admin
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_spam":
        text = (
            "🛡 <b>Spam himoyasi</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"Spam himoyasi: {'✅ Yoqilgan' if settings.spam_protection else '❌ O\'chirilgan'}\n"
            f"Xabar limiti: <b>{settings.spam_limit}</b> xabar / {settings.spam_time} sekund\n"
            f"Avtomatik mute: {'✅ Ha' if settings.mute_on_warn else '❌ Yo\'q'}\n"
            f"Mute vaqti: <b>{settings.mute_duration}</b> daqiqa"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Spam himoyasi", callback_data="settings_spam_toggle")],
            [InlineKeyboardButton("🔇 Avtomatik mute", callback_data="settings_auto_mute")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
        ]
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings_spam_toggle":
        settings.spam_protection = not settings.spam_protection
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_auto_mute":
        settings.mute_on_warn = not settings.mute_on_warn
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_warns":
        text = (
            "⚠️ <b>Ogohlantirish tizimi</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"Ogohlantirish limiti: <b>{settings.warn_limit}</b>\n"
            f"Mute qilish: {'✅ Ha' if settings.mute_on_warn else '❌ Yo\'q'}\n"
            f"Ban qilish: {'✅ Ha' if settings.ban_on_warn else '❌ Yo\'q'}"
        )
        keyboard = [
            [InlineKeyboardButton("🔇 Mute qilish", callback_data="settings_warn_mute")],
            [InlineKeyboardButton("🔨 Ban qilish", callback_data="settings_warn_ban")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
        ]
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings_warn_mute":
        settings.mute_on_warn = not settings.mute_on_warn
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_warn_ban":
        settings.ban_on_warn = not settings.ban_on_warn
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_new_users":
        text = (
            "🆕 <b>Yangi a'zolar sozlamalari</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"Avtomatik mute: {'✅ Ha' if settings.auto_mute_new else '❌ Yo\'q'}\n"
            f"Mute vaqti: <b>{settings.mute_duration}</b> daqiqa\n"
            f"Cheklash: {'✅ Ha' if settings.restrict_new else '❌ Yo\'q'}\n"
            f"Captcha: {'✅ Ha' if settings.captcha_enabled else '❌ Yo\'q'}"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Avtomatik mute", callback_data="settings_auto_mute_new")],
            [InlineKeyboardButton("🔄 Cheklash", callback_data="settings_restrict_new")],
            [InlineKeyboardButton("🔄 Captcha", callback_data="settings_captcha")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
        ]
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings_auto_mute_new":
        settings.auto_mute_new = not settings.auto_mute_new
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_restrict_new":
        settings.restrict_new = not settings.restrict_new
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_captcha":
        settings.captcha_enabled = not settings.captcha_enabled
        save_settings(chat_id, settings)
        await settings_callback(update, context)

    elif data == "settings_status":
        text = (
            "📊 <b>Joriy holat</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"Salomlashish: {'✅' if settings.welcome_enabled else '❌'}\n"
            f"Xayrlashish: {'✅' if settings.goodbye_enabled else '❌'}\n"
            f"Taqiqlangan so'zlar: {len(settings.banned_words)} ta\n"
            f"Link bloklash: {'✅' if settings.banned_links else '❌'}\n"
            f"Spam himoyasi: {'✅' if settings.spam_protection else '❌'}\n"
            f"Ogohlantirish limiti: {settings.warn_limit}\n"
            f"Yangi a'zolar mute: {'✅' if settings.auto_mute_new else '❌'}\n"
            f"Captcha: {'✅' if settings.captcha_enabled else '❌'}"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
        ]
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings":
        await settings_cmd(update, context)

    elif data == "settings_back":
        await start_cmd(update, context)

# ---------------------------------------------------------------------------
# Inline button handler (umumiy tugmalar - hammaga ochiq bo'lgan)
# ---------------------------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("settings"):
        # settings_callback o'zi admin tekshiruvini va query.answer()ni qiladi
        await settings_callback(update, context)
        return

    await query.answer()

    if data == "info":
        await info_cmd(update, context)
    elif data == "ping":
        await ping_cmd(update, context)
    elif data == "admins":
        if not is_admin(update.effective_user.id):
            await query.answer("❌ Bu bo'lim faqat adminlar uchun.", show_alert=True)
            return
        await admins_cmd(update, context)
    elif data == "rules":
        await rules_cmd(update, context)
    elif data == "help":
        await help_cmd(update, context)

# ---------------------------------------------------------------------------
# .info
# ---------------------------------------------------------------------------

async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        message = update.callback_query.message
        await message.edit_text("⏳ Server holati tekshirilmoqda...")
        msg = message
    else:
        msg = await update.effective_message.reply_text("⏳ Server holati tekshirilmoqda...")

    info, players = get_server_info()

    if not info:
        await msg.edit_text(
            f"❌ Serverga ulanib bo'lmadi.\n"
            f"🌐 IP: <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>\n"
            f"💡 Server ishlayaptimi va port ochiqmi tekshiring!",
            parse_mode=ParseMode.HTML,
        )
        return

    password_text = "🔒 Ha" if info.password_protected else "🔓 Yo'q"

    text = (
        "🎮 <b>CS 1.6 SERVER MA'LUMOTI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🖥 <b>Server:</b> {info.server_name}\n"
        f"🗺 <b>Karta:</b> <code>{info.map_name}</code>\n"
        f"🌐 <b>IP:</b> <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>\n"
        f"👥 <b>O'yinchilar:</b> {info.player_count}/{info.max_players}\n"
        f"🔐 <b>Parol:</b> {password_text}\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    real_players = [p for p in players if p.name and p.name.strip()] if players else []
    real_players.sort(key=lambda p: p.score, reverse=True)

    if real_players:
        MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
        text += f"\n👤 <b>O'yinchilar</b> ({len(real_players)}/{info.player_count})\n\n"
        for i, p in enumerate(real_players, start=1):
            rank_icon = MEDALS.get(i, f"{i}.")
            nick = p.name.strip()
            kills = p.score
            text += f"{rank_icon}  <b>{nick}</b> — <code>{kills}</code> kill\n"
    else:
        text += "\n👤 Hozircha serverda o'yinchi yo'q."

    await msg.edit_text(text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# .ping
# ---------------------------------------------------------------------------

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        message = update.callback_query.message
        await message.edit_text("⏳ Ping tekshirilmoqda...")
        msg = message
    else:
        msg = await update.effective_message.reply_text("⏳ Ping tekshirilmoqda...")

    address = (config.SERVER_IP, config.SERVER_PORT)
    start_time = time.time()

    try:
        info = a2s.info(address, timeout=3.0)
        end_time = time.time()
        ping_ms = int((end_time - start_time) * 1000)

        text = (
            f"🏓 <b>Server Ping</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🌐 IP: <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>\n"
            f"📡 Ping: <b>{ping_ms} ms</b>\n"
            f"🖥 Server: {info.server_name}\n"
            f"🗺 Karta: <code>{info.map_name}</code>\n"
            f"👥 O'yinchilar: {info.player_count}/{info.max_players}"
        )

        if ping_ms < 50:
            status = "🟢 Ajoyib"
        elif ping_ms < 100:
            status = "🟡 Yaxshi"
        elif ping_ms < 200:
            status = "🟠 O'rtacha"
        else:
            status = "🔴 Yomon"

        text += f"\n📊 Holat: {status}"

        keyboard = [[InlineKeyboardButton("🔄 Yangilash", callback_data="ping")]]
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await msg.edit_text(
            f"❌ Serverga ping yuborib bo'lmadi!\n"
            f"🌐 IP: <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>\n"
            f"⚠️ Xato: {str(e)}",
            parse_mode=ParseMode.HTML
        )

# ---------------------------------------------------------------------------
# .admins - faqat adminlar
# ---------------------------------------------------------------------------

@require_admin
async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_admins()
    admins = data.get("admins", {})

    if update.callback_query:
        message = update.callback_query.message
    else:
        message = update.effective_message

    text = "👑 <b>Adminlar ro'yxati</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n\n"

    try:
        owner = await context.bot.get_chat(config.OWNER_ID)
        text += f"👑 <b>Bot egasi</b>\n"
        text += f"   {mention(owner)}\n"
        text += f"   Daraja: <b>MAX</b>\n\n"
    except Exception:
        text += f"👑 <b>Bot egasi</b>\n"
        text += f"   ID: <code>{config.OWNER_ID}</code>\n"
        text += f"   Daraja: <b>MAX</b>\n\n"

    if admins:
        text += "👮 <b>Adminlar</b>\n"
        for user_id, level in sorted(admins.items(), key=lambda x: x[1], reverse=True):
            try:
                user = await context.bot.get_chat(int(user_id))
                text += f"   {mention(user)} — {rank_name(level)}\n"
            except Exception:
                text += f"   ID: <code>{user_id}</code> — {rank_name(level)}\n"
    else:
        text += "👮 Hozircha qo'shimcha adminlar yo'q."

    if update.callback_query:
        await message.edit_text(text, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# Admin buyruqlar uchun umumiy yordamchi
# ---------------------------------------------------------------------------

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]):
    """Target foydalanuvchini aniqlash"""
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
            class Dummy:
                pass
            d = Dummy()
            d.id = int(first)
            d.first_name = first
            d.username = None
            return d, rest
    return None, None

# ---------------------------------------------------------------------------
# BAN
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args or []

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.ban @user 1d sabab</code>", parse_mode=ParseMode.HTML)
        return

    ok, err = can_moderate(actor.id, target.id)
    if not ok:
        await update.message.reply_text(err)
        return

    if not rest:
        await update.message.reply_text("⚠️ Vaqt va sababni ko'rsating.", parse_mode=ParseMode.HTML)
        return

    duration = parse_duration(rest[0])
    if duration is False:
        await update.message.reply_text("⚠️ Vaqt formati noto'g'ri.", parse_mode=ParseMode.HTML)
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
# KICK
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args or []

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.kick @user sabab</code>", parse_mode=ParseMode.HTML)
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
# MUTE
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args or []

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.mute @user 1h sabab</code>", parse_mode=ParseMode.HTML)
        return

    ok, err = can_moderate(actor.id, target.id)
    if not ok:
        await update.message.reply_text(err)
        return

    if not rest:
        await update.message.reply_text("⚠️ Vaqt va sababni ko'rsating.", parse_mode=ParseMode.HTML)
        return

    duration = parse_duration(rest[0])
    if duration is False:
        await update.message.reply_text("⚠️ Vaqt formati noto'g'ri.", parse_mode=ParseMode.HTML)
        return

    reason = " ".join(rest[1:]) if len(rest) > 1 else "sabab ko'rsatilmagan"

    until_date = None
    if duration is not None:
        until_date = datetime.now(timezone.utc) + duration

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=False),
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
# UNMUTE
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args or []

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.unmute @user</code>", parse_mode=ParseMode.HTML)
        return

    ok, err = can_moderate(actor.id, target.id)
    if not ok:
        await update.message.reply_text(err)
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            ),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute qilib bo'lmadi: {e}")
        return

    await update.message.reply_text(
        f"🔊 <b>UNMUTE QILINDI</b>\n"
        f"👤 Kim: {mention(target)}\n"
        f"👮 Admin: {mention(actor)}",
        parse_mode=ParseMode.HTML,
    )

# ---------------------------------------------------------------------------
# WARN
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args or []

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.warn @user sabab</code>", parse_mode=ParseMode.HTML)
        return

    ok, err = can_moderate(actor.id, target.id)
    if not ok:
        await update.message.reply_text(err)
        return

    if not rest:
        await update.message.reply_text("⚠️ Sababni yozing.", parse_mode=ParseMode.HTML)
        return

    reason = " ".join(rest)
    chat_id = update.effective_chat.id

    warns_data = load_warns()
    total_warns = add_warn_record(warns_data, target.id, reason, actor.id, actor.first_name or str(actor.id))
    save_warns(warns_data)

    settings = load_settings(chat_id)

    await update.message.reply_text(
        f"⚠️ <b>OGOHLANTIRISH</b>\n"
        f"👤 Kim: {mention(target)}\n"
        f"📝 Sabab: {reason}\n"
        f"🔢 Jami: {total_warns}/{settings.warn_limit}\n"
        f"👮 Admin: {mention(actor)}",
        parse_mode=ParseMode.HTML,
    )

    consequence = await apply_warn_limit_consequence(context, chat_id, target, total_warns, settings)
    if consequence:
        c_kind, c_val = consequence
        if c_kind == "ban":
            await update.message.reply_text(
                f"🔨 <b>AVTOMATIK BAN</b>\n"
                f"{mention(target)} {settings.warn_limit} ta ogohlantirishdan keyin ban qilindi!",
                parse_mode=ParseMode.HTML
            )
        elif c_kind == "mute":
            await update.message.reply_text(
                f"🔇 <b>AVTOMATIK MUTE</b>\n"
                f"{mention(target)} {settings.warn_limit} ta ogohlantirishdan keyin {c_val} daqiqaga mute qilindi!",
                parse_mode=ParseMode.HTML
            )

# ---------------------------------------------------------------------------
# WARNS - oddiy user faqat O'ZINI ko'ra oladi, admin istalganini ko'ra oladi
# ---------------------------------------------------------------------------

@only_group
async def warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args or []

    # Argument berilmasa -> so'ragan odamning o'zini tekshiramiz
    if not args and not update.message.reply_to_message:
        target = actor
        rest = []
    else:
        target, rest = await resolve_target(update, context, args)
        if target is None:
            await update.message.reply_text("⚠️ Foydalanish: <code>.warns</code> yoki <code>.warns @user</code> (faqat admin uchun)", parse_mode=ParseMode.HTML)
            return

    # Faqat admin boshqa birovning warnini ko'ra oladi
    if target.id != actor.id and not is_admin(actor.id):
        await update.message.reply_text("❌ Faqat o'z ogohlantirishlaringizni ko'rishingiz mumkin.")
        return

    warns_data = load_warns()
    user_id_str = str(target.id)

    if user_id_str not in warns_data or not warns_data[user_id_str]["warns"]:
        await update.message.reply_text(f"✅ {mention(target)} da ogohlantirishlar yo'q.", parse_mode=ParseMode.HTML)
        return

    text = f"⚠️ <b>{mention(target)} uchun ogohlantirishlar</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n\n"

    for warn in warns_data[user_id_str]["warns"][-10:]:
        text += f"#{warn['warn_id']}\n"
        text += f"📝 Sabab: {warn['reason']}\n"
        text += f"👮 Admin: {warn['admin_name']}\n"
        text += f"🕐 Vaqt: {warn['date']}\n\n"

    text += f"📊 Jami: <b>{warns_data[user_id_str]['total_warns']}</b> ta ogohlantirish"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# CLEAR WARNS
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def clear_warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []

    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.clearwarns @user</code>", parse_mode=ParseMode.HTML)
        return

    warns_data = load_warns()
    user_id_str = str(target.id)

    if user_id_str in warns_data:
        warns_data[user_id_str]["warns"] = []
        warns_data[user_id_str]["total_warns"] = 0
        save_warns(warns_data)

    await update.message.reply_text(
        f"✅ {mention(target)} uchun barcha ogohlantirishlar tozalandi.",
        parse_mode=ParseMode.HTML
    )

# ---------------------------------------------------------------------------
# PIN
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
# DELETE
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
# So'z qo'shish/o'chirish
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def add_word_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("⚠️ Foydalanish: <code>.addword so'z</code>", parse_mode=ParseMode.HTML)
        return

    word = " ".join(args).lower()
    settings = load_settings(update.effective_chat.id)

    if word in settings.banned_words:
        await update.message.reply_text(f"⚠️ <code>{word}</code> allaqachon ro'yxatda.", parse_mode=ParseMode.HTML)
        return

    settings.banned_words.append(word)
    save_settings(update.effective_chat.id, settings)

    await update.message.reply_text(
        f"✅ <code>{word}</code> taqiqlangan so'zlar ro'yxatiga qo'shildi.",
        parse_mode=ParseMode.HTML
    )

@only_group
@require_admin
async def del_word_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("⚠️ Foydalanish: <code>.delword so'z</code>", parse_mode=ParseMode.HTML)
        return

    word = " ".join(args).lower()
    settings = load_settings(update.effective_chat.id)

    if word not in settings.banned_words:
        await update.message.reply_text(f"⚠️ <code>{word}</code> ro'yxatda yo'q.", parse_mode=ParseMode.HTML)
        return

    settings.banned_words.remove(word)
    save_settings(update.effective_chat.id, settings)

    await update.message.reply_text(
        f"✅ <code>{word}</code> taqiqlangan so'zlar ro'yxatidan o'chirildi.",
        parse_mode=ParseMode.HTML
    )

# ---------------------------------------------------------------------------
# Salomlashish / xayrlashish matnini sozlash
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def set_welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "⚠️ Foydalanish: <code>.setwelcome Salom {user}, {group} guruhiga xush kelibsiz!</code>\n"
            "📌 O'zgaruvchilar: <code>{user}</code> <code>{group}</code> <code>{count}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    text = update.message.text.split(None, 1)[1]
    settings = load_settings(update.effective_chat.id)
    settings.welcome_text = text
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text("✅ Salomlashish matni yangilandi:\n\n" + text, parse_mode=ParseMode.HTML)

@only_group
@require_admin
async def set_goodbye_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "⚠️ Foydalanish: <code>.setgoodbye Xayr {user}!</code>\n"
            "📌 O'zgaruvchilar: <code>{user}</code> <code>{group}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    text = update.message.text.split(None, 1)[1]
    settings = load_settings(update.effective_chat.id)
    settings.goodbye_text = text
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text("✅ Xayrlashish matni yangilandi:\n\n" + text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# Taqiqlangan so'z / link uchun jazo turini to'liq sozlash
# ---------------------------------------------------------------------------

@only_group
@require_admin
async def set_word_action_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args or args[0].lower() not in ACTION_ORDER:
        await update.message.reply_text(
            f"⚠️ Foydalanish: <code>.setwordaction {'|'.join(ACTION_ORDER)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    settings = load_settings(update.effective_chat.id)
    settings.word_action = args[0].lower()
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text(
        f"✅ Taqiqlangan so'z uchun jazo o'rnatildi: <b>{ACTION_LABELS[settings.word_action]}</b>",
        parse_mode=ParseMode.HTML,
    )

@only_group
@require_admin
async def set_link_action_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args or args[0].lower() not in ACTION_ORDER:
        await update.message.reply_text(
            f"⚠️ Foydalanish: <code>.setlinkaction {'|'.join(ACTION_ORDER)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    settings = load_settings(update.effective_chat.id)
    settings.link_action = args[0].lower()
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text(
        f"✅ Link yuborgani uchun jazo o'rnatildi: <b>{ACTION_LABELS[settings.link_action]}</b>",
        parse_mode=ParseMode.HTML,
    )

@only_group
@require_admin
async def set_word_mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text("⚠️ Foydalanish: <code>.setwordmute 15</code> (daqiqa)", parse_mode=ParseMode.HTML)
        return
    settings = load_settings(update.effective_chat.id)
    settings.word_mute_minutes = int(args[0])
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text(f"✅ Taqiqlangan so'z uchun mute vaqti: <b>{settings.word_mute_minutes}</b> daqiqa", parse_mode=ParseMode.HTML)

@only_group
@require_admin
async def set_link_mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        await update.message.reply_text("⚠️ Foydalanish: <code>.setlinkmute 15</code> (daqiqa)", parse_mode=ParseMode.HTML)
        return
    settings = load_settings(update.effective_chat.id)
    settings.link_mute_minutes = int(args[0])
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text(f"✅ Link uchun mute vaqti: <b>{settings.link_mute_minutes}</b> daqiqa", parse_mode=ParseMode.HTML)

@only_group
@require_admin
async def set_word_ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args or parse_duration(args[0]) is False:
        await update.message.reply_text(
            "⚠️ Foydalanish: <code>.setwordban 1d</code> (masalan: 30m, 1h, 7d, doimiy)",
            parse_mode=ParseMode.HTML,
        )
        return
    settings = load_settings(update.effective_chat.id)
    settings.word_ban_duration = args[0].lower()
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text(
        f"✅ Taqiqlangan so'z uchun ban muddati: <b>{fmt_duration(parse_duration(settings.word_ban_duration))}</b>",
        parse_mode=ParseMode.HTML,
    )

@only_group
@require_admin
async def set_link_ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args or parse_duration(args[0]) is False:
        await update.message.reply_text(
            "⚠️ Foydalanish: <code>.setlinkban 1d</code> (masalan: 30m, 1h, 7d, doimiy)",
            parse_mode=ParseMode.HTML,
        )
        return
    settings = load_settings(update.effective_chat.id)
    settings.link_ban_duration = args[0].lower()
    save_settings(update.effective_chat.id, settings)
    await update.message.reply_text(
        f"✅ Link uchun ban muddati: <b>{fmt_duration(parse_duration(settings.link_ban_duration))}</b>",
        parse_mode=ParseMode.HTML,
    )

# ---------------------------------------------------------------------------
# Bot egasi buyruqlari
# ---------------------------------------------------------------------------

@require_owner
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("⚠️ Foydalanish: <code>.addadmin @user level</code>", parse_mode=ParseMode.HTML)
        return

    target_username = args[0]
    if target_username.startswith("@"):
        target_username = target_username[1:]

    try:
        level = int(args[1])
    except ValueError:
        await update.message.reply_text("⚠️ Level son bo'lishi kerak!")
        return

    if level <= 0:
        await update.message.reply_text("⚠️ Level musbat son bo'lishi kerak!")
        return

    try:
        user = await context.bot.get_chat(f"@{target_username}")
    except Exception:
        await update.message.reply_text(f"❌ {target_username} topilmadi!")
        return

    data = load_admins()
    data["admins"][str(user.id)] = level
    save_admins(data)

    await update.message.reply_text(
        f"✅ {mention(user)} admin qilib tayinlandi!\n"
        f"📊 Daraja: <b>{level}</b> ({rank_name(level)})",
        parse_mode=ParseMode.HTML
    )

@require_owner
async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("⚠️ Foydalanish: <code>.removeadmin @user</code>", parse_mode=ParseMode.HTML)
        return

    target_username = args[0]
    if target_username.startswith("@"):
        target_username = target_username[1:]

    try:
        user = await context.bot.get_chat(f"@{target_username}")
    except Exception:
        await update.message.reply_text(f"❌ {target_username} topilmadi!")
        return

    data = load_admins()
    if str(user.id) in data["admins"]:
        del data["admins"][str(user.id)]
        save_admins(data)
        await update.message.reply_text(f"✅ {mention(user)} adminlikdan olindi.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"❌ {mention(user)} admin emas.", parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# Chat member handler
# ---------------------------------------------------------------------------

async def chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.chat_member:
        return

    chat = update.chat_member.chat
    new_member = update.chat_member.new_chat_member
    old_member = update.chat_member.old_chat_member

    if not chat or not new_member:
        return

    settings = load_settings(chat.id)
    user = new_member.user

    if new_member.status in ['member', 'administrator', 'creator'] and old_member.status in ['left', 'kicked']:
        if settings.welcome_enabled:
            try:
                try:
                    member_count = await context.bot.get_chat_member_count(chat.id)
                except Exception:
                    member_count = "?"

                text = settings.welcome_text
                text = text.replace("{user}", mention(user))
                text = text.replace("{group}", chat.title or "Guruh")
                text = text.replace("{count}", str(member_count))

                await context.bot.send_message(
                    chat_id=chat.id,
                    text=text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Welcome xatosi: {e}")

        if settings.auto_mute_new:
            until_date = datetime.now(timezone.utc) + timedelta(minutes=settings.mute_duration)
            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_date
                )
            except Exception:
                pass

    elif old_member.status in ['member', 'administrator'] and new_member.status in ['left', 'kicked']:
        if settings.goodbye_enabled:
            try:
                text = settings.goodbye_text
                text = text.replace("{user}", mention(user))
                text = text.replace("{group}", chat.title or "Guruh")

                await context.bot.send_message(
                    chat_id=chat.id,
                    text=text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Goodbye xatosi: {e}")

# ---------------------------------------------------------------------------
# Xabarlarni filtrash (taqiqlangan so'z / link) — adminlarga tegmaydi
# ---------------------------------------------------------------------------

async def apply_message_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text

    if is_admin(user.id):
        return

    settings = load_settings(chat_id)

    if settings.banned_words and has_banned_words(text, settings.banned_words):
        await execute_violation_action(update, context, settings, user, "word", "Taqiqlangan so'z ishlatish")
        return

    if settings.banned_links and has_links(text):
        await execute_violation_action(update, context, settings, user, "link", "Link yuborish")
        return

# ---------------------------------------------------------------------------
# DOT komandalar router
# ---------------------------------------------------------------------------

DOT_COMMANDS = {
    ".help": help_cmd,
    ".info": info_cmd,
    ".ping": ping_cmd,
    ".settings": settings_cmd,
    ".admins": admins_cmd,
    ".rules": rules_cmd,
    ".ban": ban_cmd,
    ".kick": kick_cmd,
    ".mute": mute_cmd,
    ".unmute": unmute_cmd,
    ".warn": warn_cmd,
    ".warns": warns_cmd,
    ".clearwarns": clear_warns_cmd,
    ".pin": pin_cmd,
    ".del": del_cmd,
    ".addword": add_word_cmd,
    ".delword": del_word_cmd,
    ".setwelcome": set_welcome_cmd,
    ".setgoodbye": set_goodbye_cmd,
    ".setwordaction": set_word_action_cmd,
    ".setlinkaction": set_link_action_cmd,
    ".setwordmute": set_word_mute_cmd,
    ".setlinkmute": set_link_mute_cmd,
    ".setwordban": set_word_ban_cmd,
    ".setlinkban": set_link_ban_cmd,
    ".addadmin": add_admin_cmd,
    ".removeadmin": remove_admin_cmd,
}

# ---------------------------------------------------------------------------
# Yagona matn handleri: avval dot-komanda ekanini tekshiradi,
# bo'lmasa taqiqlangan so'z/link filtriga o'tkazadi.
# Har bir buyruqning o'zida (require_admin / require_owner / only_group)
# huquq tekshiruvi bo'lgani uchun bu yerda qo'shimcha tekshirish shart emas.
# ---------------------------------------------------------------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        return

    # Dot-komanda emas -> taqiqlangan so'z/link filtri
    await apply_message_filter(update, context)

# ---------------------------------------------------------------------------
# Botni ishga tushirish
# ---------------------------------------------------------------------------

async def run_bot_async():
    if config.BOT_TOKEN == "BOT_TOKEN_BU_YERGA":
        print("❌ config.py faylida BOT_TOKEN ni to'g'ri kiritmagansiz!")
        return
    if config.OWNER_ID == 0:
        print("❌ config.py faylida OWNER_ID ni to'g'ri kiritmagansiz!")
        return

    application = Application.builder().token(config.BOT_TOKEN).build()

    # Faqat /start slash bilan qoladi (Telegram "Start" tugmasi buni avtomatik yuboradi)
    application.add_handler(CommandHandler("start", start_cmd))

    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(ChatMemberHandler(chat_member_handler, ChatMemberHandler.CHAT_MEMBER))

    # Barcha qolgan matnli buyruqlar (.help, .info, .ping, .settings, .admins,
    # .rules, .ban, .kick, .mute va h.k.) shu yagona router orqali ishlaydi
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Bot ishga tushdi...")

    # Webhook ni o'chirish
    await application.bot.delete_webhook(drop_pending_updates=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_bot_async())
    finally:
        loop.close()

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("Telegram bot Long Polling rejimida ishga tushdi...")

    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
