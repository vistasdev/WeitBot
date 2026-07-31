# -*- coding: utf-8 -*-
"""
CS 1.6 Server uchun FULL Telegram Bot
Versiya: 3.2 - Conflict xatosi tuzatilgan
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
from typing import List
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
    except:
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
    return GroupSettings(**data[chat_id_str])

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

def get_level(user_id: int) -> int:
    if user_id == config.OWNER_ID:
        return OWNER_LEVEL
    data = load_admins()
    return int(data.get("admins", {}).get(str(user_id), 0))

def is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_ID

def is_admin(user_id: int) -> bool:
    return get_level(user_id) > 0 or is_owner(user_id)

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
        except:
            pass
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

# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    
    user = update.effective_user
    first_name = user.first_name or "Foydalanuvchi"
    
    keyboard = [
        [
            InlineKeyboardButton("📊 Server holati", callback_data="info"),
            InlineKeyboardButton("🏓 Ping", callback_data="ping")
        ],
        [
            InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings"),
            InlineKeyboardButton("👥 Adminlar", callback_data="admins")
        ],
        [
            InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{context.bot.username}?startgroup=true"),
            InlineKeyboardButton("💬 Yordam", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        f"🎯 <b>Assalomu alaykum, {first_name}!</b>\n\n"
        "Men <b>CS 1.6</b> serveringiz uchun <b>FULL</b> yordamchi botman.\n\n"
        "🛡 <b>Mening imkoniyatlarim:</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✅ Guruh boshqaruvi\n"
        "✅ Avtomatik salomlashish\n"
        "✅ Taqiqlangan so'zlar\n"
        "✅ Link bloklash\n"
        "✅ Spam himoyasi\n"
        "✅ Ogohlantirish tizimi\n"
        "✅ CS 1.6 server ma'lumotlari\n\n"
        "📋 <b>Buyruqlar:</b>\n"
        "• <code>/start</code> - Botni ishga tushirish\n"
        "• <code>/help</code> - Yordam\n"
        "• <code>/info</code> - Server holati\n"
        "• <code>/ping</code> - Server ping\n"
        "• <code>/settings</code> - Guruh sozlamalari\n"
        "• <code>/admins</code> - Adminlar\n\n"
        "💡 Guruhga qo'shib, meni <b>ADMIN</b> qiling!"
    )
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 <b>Yordam menyusi</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🎮 <b>Asosiy buyruqlar:</b>\n"
        "• <code>/start</code> - Botni ishga tushirish\n"
        "• <code>/help</code> - Yordam\n"
        "• <code>/info</code> - Server holati\n"
        "• <code>/ping</code> - Server ping\n"
        "• <code>/settings</code> - Guruh sozlamalari\n"
        "• <code>/admins</code> - Adminlar\n\n"
        "👮 <b>Admin buyruqlari:</b>\n"
        "• <code>.ban @user 1d sabab</code> - Ban\n"
        "• <code>.kick @user sabab</code> - Kick\n"
        "• <code>.mute @user 1h sabab</code> - Mute\n"
        "• <code>.unmute @user</code> - Unmute\n"
        "• <code>.warn @user sabab</code> - Ogohlantirish\n"
        "• <code>.warns @user</code> - Ogohlantirishlar\n"
        "• <code>.clearwarns @user</code> - Tozalash\n"
        "• <code>.pin</code> (reply) - Pin qilish\n"
        "• <code>.del</code> (reply) - O'chirish\n"
        "• <code>.addword so'z</code> - Taqiqlangan so'z qo'shish\n"
        "• <code>.delword so'z</code> - Taqiqlangan so'z o'chirish\n\n"
        "👑 <b>Bot egasi buyruqlari:</b>\n"
        "• <code>.addadmin @user level</code> - Admin qo'shish\n"
        "• <code>.removeadmin @user</code> - Admin o'chirish"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Bu buyruq faqat adminlar uchun!")
        return
    
    keyboard = [
        [InlineKeyboardButton("👋 Salomlashish", callback_data="settings_welcome")],
        [InlineKeyboardButton("🚫 Taqiqlangan so'zlar", callback_data="settings_banned_words")],
        [InlineKeyboardButton("🔗 Link bloklash", callback_data="settings_links")],
        [InlineKeyboardButton("🛡 Spam himoyasi", callback_data="settings_spam")],
        [InlineKeyboardButton("⚠️ Ogohlantirish tizimi", callback_data="settings_warns")],
        [InlineKeyboardButton("🆕 Yangi a'zolar", callback_data="settings_new_users")],
        [InlineKeyboardButton("📊 Joriy holat", callback_data="settings_status")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "⚙️ <b>Guruh sozlamalari</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Kerakli bo'limni tanlang:"
    )
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

# ---------------------------------------------------------------------------
# Settings callback handler
# ---------------------------------------------------------------------------

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
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
        
        text += "\n➕ So'z qo'shish: <code>.addword so'z</code>\n"
        text += "➖ So'z o'chirish: <code>.delword so'z</code>"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
        ]
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "settings_links":
        text = (
            "🔗 <b>Link bloklash</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"Link bloklash: {'✅ Yoqilgan' if settings.banned_links else '❌ O\'chirilgan'}\n"
            f"Xabarni o'chirish: {'✅ Ha' if settings.delete_banned else '❌ Yo\'q'}\n"
            f"Adminni xabardor qilish: {'✅ Ha' if settings.notify_admin else '❌ Yo\'q'}"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Link bloklash", callback_data="settings_links_toggle")],
            [InlineKeyboardButton("🔄 Xabarni o'chirish", callback_data="settings_delete_toggle")],
            [InlineKeyboardButton("🔄 Admin xabari", callback_data="settings_notify_toggle")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="settings")]
        ]
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    
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
            [InlineKeyboardButton("📊 Limitni o'zgartirish", callback_data="settings_warn_limit")],
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
# Inline button handler
# ---------------------------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "info":
        await info_cmd(update, context)
    elif data == "ping":
        await ping_cmd(update, context)
    elif data == "admins":
        await admins_cmd(update, context)
    elif data == "settings":
        await settings_cmd(update, context)
    elif data == "help":
        await help_cmd(update, context)
    elif data.startswith("settings_"):
        await settings_callback(update, context)

# ---------------------------------------------------------------------------
# /info
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

    text = (
        "🎮 <b>CS 1.6 SERVER MA'LUMOTI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🖥 <b>Server:</b> {info.server_name}\n"
        f"🗺 <b>Karta:</b> <code>{info.map_name}</code>\n"
        f"🌐 <b>IP:</b> <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>\n"
        f"👥 <b>O'yinchilar:</b> {info.player_count}/{info.max_players}\n"
    )
    
    password_text = "🔒 Ha" if info.password_protected else "🔓 Yo'q"
    text += f"{password_text} <b>Parol:</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"

    if players:
        real_players = [p for p in players if p.name and p.name.strip()]
        real_players.sort(key=lambda p: p.score, reverse=True)
        if real_players:
            text += f"\n👤 <b>O'yinchilar ({len(real_players)}/{info.player_count})</b>\n"
            text += "```\n"
            text += f"{'№':<3} {'NICK':<20} {'KILLS':>6}\n"
            text += "─" * 30 + "\n"
            for i, p in enumerate(real_players, start=1):
                nick = p.name[:20]
                kills = p.score
                text += f"{i:<3} {nick:<20} {kills:>6}\n"
            text += "```"
        else:
            text += "\n👤 Hozircha serverda o'yinchi yo'q."
    else:
        text += "\n👤 Hozircha serverda o'yinchi yo'q."

    await msg.edit_text(text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# /ping
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
        
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(
            f"❌ Serverga ping yuborib bo'lmadi!\n"
            f"🌐 IP: <code>{config.SERVER_IP}:{config.SERVER_PORT}</code>\n"
            f"⚠️ Xato: {str(e)}",
            parse_mode=ParseMode.HTML
        )

# ---------------------------------------------------------------------------
# /admins
# ---------------------------------------------------------------------------

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
    except:
        text += f"👑 <b>Bot egasi</b>\n"
        text += f"   ID: <code>{config.OWNER_ID}</code>\n"
        text += f"   Daraja: <b>MAX</b>\n\n"
    
    if admins:
        text += "👮 <b>Adminlar</b>\n"
        for user_id, level in sorted(admins.items(), key=lambda x: x[1], reverse=True):
            try:
                user = await context.bot.get_chat(int(user_id))
                text += f"   {mention(user)}\n"
                text += f"   Daraja: <b>{level}</b>\n\n"
            except:
                text += f"   ID: <code>{user_id}</code>\n"
                text += f"   Daraja: <b>{level}</b>\n\n"
    
    if update.callback_query:
        await message.edit_text(text, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(text, parse_mode=ParseMode.HTML)

# ---------------------------------------------------------------------------
# Admin buyruqlar
# ---------------------------------------------------------------------------

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list[str]):
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

def only_group(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await update.message.reply_text("❌ Bu buyruq faqat guruhlarda ishlaydi.")
            return
        return await func(update, context)
    return wrapper

@only_group
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]
    
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

@only_group
async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]
    
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

@only_group
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]
    
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

@only_group
async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]
    
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

@only_group
async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]
    
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
    
    warns_data = load_warns()
    user_id_str = str(target.id)
    
    if user_id_str not in warns_data:
        warns_data[user_id_str] = {"warns": [], "total_warns": 0}
    
    warn = {
        "reason": reason,
        "admin_id": actor.id,
        "admin_name": actor.first_name or str(actor.id),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "warn_id": len(warns_data[user_id_str]["warns"]) + 1
    }
    
    warns_data[user_id_str]["warns"].append(warn)
    warns_data[user_id_str]["total_warns"] += 1
    save_warns(warns_data)
    
    settings = load_settings(update.effective_chat.id)
    total_warns = warns_data[user_id_str]["total_warns"]
    
    await update.message.reply_text(
        f"⚠️ <b>OGOHLANTIRISH</b>\n"
        f"👤 Kim: {mention(target)}\n"
        f"📝 Sabab: {reason}\n"
        f"🔢 Jami: {total_warns}/{settings.warn_limit}\n"
        f"👮 Admin: {mention(actor)}",
        parse_mode=ParseMode.HTML,
    )
    
    if total_warns >= settings.warn_limit:
        if settings.ban_on_warn:
            try:
                await context.bot.ban_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=target.id
                )
                await update.message.reply_text(
                    f"🔨 <b>AVTOMATIK BAN</b>\n"
                    f"{mention(target)} {settings.warn_limit} ta ogohlantirishdan keyin ban qilindi!",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        elif settings.mute_on_warn:
            try:
                until_date = datetime.now(timezone.utc) + timedelta(minutes=settings.mute_duration)
                await context.bot.restrict_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=target.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_date
                )
                await update.message.reply_text(
                    f"🔇 <b>AVTOMATIK MUTE</b>\n"
                    f"{mention(target)} {settings.warn_limit} ta ogohlantirishdan keyin {settings.mute_duration} daqiqaga mute qilindi!",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass

@only_group
async def warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args if context.args else update.message.text.split()[1:]
    
    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.warns @user</code>", parse_mode=ParseMode.HTML)
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

@only_group
async def clear_warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    args = context.args if context.args else update.message.text.split()[1:]
    
    target, rest = await resolve_target(update, context, args)
    if target is None:
        await update.message.reply_text("⚠️ Foydalanish: <code>.clearwarns @user</code>", parse_mode=ParseMode.HTML)
        return
    
    if not is_admin(actor.id):
        await update.message.reply_text("❌ Sizda admin huquqi yo'q.")
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

@only_group
async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if not is_admin(actor.id):
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

@only_group
async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor = update.effective_user
    if not is_admin(actor.id):
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
# So'z qo'shish/o'chirish
# ---------------------------------------------------------------------------

@only_group
async def add_word_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sizda admin huquqi yo'q.")
        return
    
    args = context.args if context.args else update.message.text.split()[1:]
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
async def del_word_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sizda admin huquqi yo'q.")
        return
    
    args = context.args if context.args else update.message.text.split()[1:]
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
# Bot egasi buyruqlari
# ---------------------------------------------------------------------------

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Bu buyruq faqat bot egasi uchun!")
        return
    
    args = context.args if context.args else update.message.text.split()[1:]
    if len(args) < 2:
        await update.message.reply_text("⚠️ Foydalanish: <code>.addadmin @user level</code>", parse_mode=ParseMode.HTML)
        return
    
    target_username = args[0]
    if target_username.startswith("@"):
        target_username = target_username[1:]
    
    try:
        level = int(args[1])
    except:
        await update.message.reply_text("⚠️ Level son bo'lishi kerak!")
        return
    
    try:
        user = await context.bot.get_chat(f"@{target_username}")
    except:
        await update.message.reply_text(f"❌ {target_username} topilmadi!")
        return
    
    data = load_admins()
    data["admins"][str(user.id)] = level
    save_admins(data)
    
    await update.message.reply_text(
        f"✅ {mention(user)} admin qilib tayinlandi!\n"
        f"📊 Daraja: <b>{level}</b>",
        parse_mode=ParseMode.HTML
    )

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Bu buyruq faqat bot egasi uchun!")
        return
    
    args = context.args if context.args else update.message.text.split()[1:]
    if not args:
        await update.message.reply_text("⚠️ Foydalanish: <code>.removeadmin @user</code>", parse_mode=ParseMode.HTML)
        return
    
    target_username = args[0]
    if target_username.startswith("@"):
        target_username = target_username[1:]
    
    try:
        user = await context.bot.get_chat(f"@{target_username}")
    except:
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
# Xabarlarni filtrash
# ---------------------------------------------------------------------------

async def message_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text
    
    if is_admin(user.id):
        return
    
    settings = load_settings(chat_id)
    
    if settings.banned_words and has_banned_words(text, settings.banned_words):
        if settings.delete_banned:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            except:
                pass
        
        if settings.notify_admin:
            await update.message.reply_text(
                f"🚫 <b>Taqiqlangan so'z</b>\n"
                f"{mention(user)} taqiqlangan so'z ishlatdi!",
                parse_mode=ParseMode.HTML
            )
        
        warns_data = load_warns()
        user_id_str = str(user.id)
        if user_id_str not in warns_data:
            warns_data[user_id_str] = {"warns": [], "total_warns": 0}
        
        warns_data[user_id_str]["warns"].append({
            "reason": "Taqiqlangan so'z ishlatish",
            "admin_id": user.id,
            "admin_name": "Bot (avtomatik)",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "warn_id": len(warns_data[user_id_str]["warns"]) + 1
        })
        warns_data[user_id_str]["total_warns"] += 1
        save_warns(warns_data)
        return
    
    if settings.banned_links and has_links(text):
        if settings.delete_banned:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
            except:
                pass
        
        if settings.notify_admin:
            await update.message.reply_text(
                f"🔗 <b>Link</b>\n"
                f"{mention(user)} link yubordi!",
                parse_mode=ParseMode.HTML
            )
        
        warns_data = load_warns()
        user_id_str = str(user.id)
        if user_id_str not in warns_data:
            warns_data[user_id_str] = {"warns": [], "total_warns": 0}
        
        warns_data[user_id_str]["warns"].append({
            "reason": "Link yuborish",
            "admin_id": user.id,
            "admin_name": "Bot (avtomatik)",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "warn_id": len(warns_data[user_id_str]["warns"]) + 1
        })
        warns_data[user_id_str]["total_warns"] += 1
        save_warns(warns_data)
        return

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
                except:
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
            except:
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
# DOT komandalar router
# ---------------------------------------------------------------------------

DOT_COMMANDS = {
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
    ".addadmin": add_admin_cmd,
    ".removeadmin": remove_admin_cmd,
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
# Botni ishga tushirish - CONFLICT XATOSI TUZATILDI
# ---------------------------------------------------------------------------

async def run_bot_async():
    if config.BOT_TOKEN == "BOT_TOKEN_BU_YERGA":
        print("❌ config.py faylida BOT_TOKEN ni to'g'ri kiritmagansiz!")
        return
    if config.OWNER_ID == 0:
        print("❌ config.py faylida OWNER_ID ni to'g'ri kiritmagansiz!")
        return

    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("info", info_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    application.add_handler(CommandHandler("admins", admins_cmd))
    
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(ChatMemberHandler(chat_member_handler, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_filter))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dot_command_router))

    logger.info("Bot ishga tushdi...")
    
    # Webhook ni o'chirish - CONFLICT xatosini oldini olish uchun
    await application.bot.delete_webhook(drop_pending_updates=True)
    
    await application.initialize()
    await application.start()
    
    # Polling ni ishga tushirish
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
