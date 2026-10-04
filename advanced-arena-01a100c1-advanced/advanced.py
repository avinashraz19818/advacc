import asyncio
import logging
import json
import time
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from functools import wraps

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import user_sender as user_sender_mod
from user_sender import UserAccountSender, API_ID, API_HASH
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Bot,
    MessageEntity,
)
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut

# ================= RETRY DECORATOR =================
def retry_async(max_retries=3, delay=1, backoff=2):
    """
    IMPORTANT: In python-telegram-bot, `BadRequest` is a SUBCLASS of `NetworkError`.
    That means a plain `except (NetworkError, TimedOut, ConnectionError)` clause will
    also silently swallow-and-retry things like "Document_invalid",
    "Voice_messages_forbidden", "Message is not modified", etc. Those are permanent
    errors caused by the request itself (bad file, forbidden content, bad markup) -
    retrying them wastes time (delay + backoff seconds) and will NEVER succeed, and
    then finally raises anyway, which is what caused the "Retry 1/3 ... Document_invalid"
    spam in the logs and the generic "Callback error: Document_invalid" crashes.
    Fix: catch BadRequest FIRST and re-raise it immediately, without retrying.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            current_delay = delay
            while retries < max_retries:
                try:
                    return await func(*args, **kwargs)
                except BadRequest:
                    # Permanent error - retrying is pointless, fail fast.
                    raise
                except (NetworkError, TimedOut, ConnectionError) as e:
                    retries += 1
                    if retries >= max_retries:
                        logging.error(f"Failed after {max_retries} retries: {e}")
                        raise
                    logging.warning(f"Retry {retries}/{max_retries} after {current_delay}s: {e}")
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
                except Exception:
                    raise
            return None
        return wrapper
    return decorator

# ================= TIMEZONE HELPER =================
def now_aware():
    return datetime.now(timezone.utc)

def make_aware(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

# ================= CONFIG =================
# Saare secrets/config .env se aate hain (kuch bhi hardcoded nahi).
# Zaroori vars dekhein: .env.example
MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN", "").strip()
ADMIN_USER_ID = 8015937475
ADMIN_USERNAME = "@zayro_o"
_ADMIN_IDS_RAW = os.getenv("ADMIN_USER_IDS", "").strip()
ADMIN_USER_IDS = {ADMIN_USER_ID}
if _ADMIN_IDS_RAW:
    for _x in _ADMIN_IDS_RAW.split(","):
        _x = _x.strip()
        if _x.isdigit():
            ADMIN_USER_IDS.add(int(_x))

SUPPORT_REPLY_MAP: Dict[int, Dict] = {}
USERBOT_SUPPORT_REPLY_MAP: Dict[str, Dict] = {}
SUPPORT_MAP_TTL = 86400

# ================= PREMIUM EMOJI IDS =================
EMOJI_IDS = {
    "💎": "5042050649248760772", "⭐️": "5042176294222037888", "⚡️": "5042334757040423886",
    "👑": "5039727497143387500", "✅": "5039844895779455925", "❌": "5040042498634810056",
    "‼️": "5042003580702164014", "🔔": "5042111805288089118", "📊": "5042290883949495533",
    "💬": "5040036030414062506", "🔄": "5041837837914211014", "🎉": "5039778134807806727",
    "🔍": "5039649904264217620", "👀": "5039623284056917259", "🛡": "5042328396193864923",
    "🔴": "5042042652019655612", "🟢": "5039928501612839813", "📣": "5041888071851705019",
    "🗑": "5039614900280754969", "💰": "5039789890133296083", "⚠️": "5039665997506675838",
    "🔗": "5042101437237036298", "📞": "5407025283456835913", "🔥": "5389038097860144794",
    "🚀": "5389057356493511934", "🎯": "5041888071851705019", "📅": "5413879192267805083",
    "🔐": "5305609152704297298", "⚙️": "5042101437237036298", "✈️": "5041888071851705019",
    "📝": "5039844895779455925", "📋": "5042290883949495533", "🔙": "5041837837914211014",
    "➡️": "5041837837914211014", "⬅️": "5041837837914211014", "➕": "5039844895779455925",
    "🖼️": "5040016479722931047", "🔘": "5042101437237036298", "🤖": "5042290883949495533",
    "🛑": "5042042652019655612", "⏰": "6285240160120477644", "📇": "5042290883949495533",
    "📌": "5039600026809009149", "🔝": "5042102141611672423", "💙": "5039560388555834382",
    "👍": "5039544445637231745", "👎": "5042067236412458007", "🚫": "5039671744172917707",
    "🧠": "5040030395416969985", "💡": "5039660273953853888", "💫": "5042200814190330758",
    "✨": "5040016479722931047", "🎨": "5040016479722931047",
    "📁": "5042290883949495533", "🎵": "5042101437237036298", "🎬": "5039778134807806727",
    "📄": "5039844895779455925", "🗂️": "5042290883949495533", "📦": "5042101437237036298",
    "🎙️": "5042101437237036298", "🎤": "5042101437237036298",
    "📩": "5267930814523847773", "👤": "5426536590798641686",
    "🔖": "5445273572664679078", "🟥": "5271091265042120271",
}

def pe(emoji_char: str) -> str:
    """Return plain emoji character (no HTML tags) for safe use in user-facing messages."""
    return emoji_char

def pp(emoji_char: str) -> str:
    """Return premium emoji HTML tag for bot UI/admin messages. Returns plain emoji if no ID found."""
    eid = EMOJI_IDS.get(emoji_char)
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{emoji_char}</tg-emoji>'
    return emoji_char

def format_support_msg(user_name: str, username: str, user_id: int, message_text: str = None, clickable: bool = True) -> str:
    """Format support message with PLAIN emojis (user-facing, no premium)."""
    if clickable:
        name_line = f'<a href="tg://user?id={user_id}">{pe("👤")} {user_name}</a>'
    else:
        name_line = f'{pe("👤")} {user_name}'
    username_line = f'{pe("🔖")} @{username}' if username and username != "N/A" else f'{pe("🔖")} —'
    header = f'\n{pe("📩")} <b>New Message</b>\n'
    body = f'{name_line}\n{username_line}'
    if message_text:
        body += f'\n\n{pe("💬")} <b>Message:</b>\n▸ {message_text}'
    return f'{header}\n\n{body}\n\n'

def _degrade_markup(markup):
    """
    Fallback helper: strip icon_custom_emoji_id/style from every button, keeping
    only text/url/callback_data. Used when Telegram rejects a "styled" message
    (custom emoji icon referencing an invalid/inaccessible document) so we can
    retry with a plain version instead of crashing.
    """
    if not markup:
        return markup
    try:
        new_rows = []
        for row in markup.inline_keyboard:
            new_row = []
            for b in row:
                if b.url:
                    new_row.append(InlineKeyboardButton(b.text, url=b.url))
                elif b.callback_data:
                    new_row.append(InlineKeyboardButton(b.text, callback_data=b.callback_data))
                else:
                    new_row.append(b)
            new_rows.append(new_row)
        return InlineKeyboardMarkup(new_rows)
    except Exception:
        return markup


def btn(text: str, callback_data: str, style: str = "primary", emoji: str = None) -> InlineKeyboardButton:
    api_kwargs = {}
    style_map = {"primary": "primary", "success": "success", "danger": "danger"}
    if style and style in style_map:
        api_kwargs["style"] = style_map[style]
    if emoji and emoji in EMOJI_IDS:
        api_kwargs["icon_custom_emoji_id"] = EMOJI_IDS[emoji]
        clean_text = text
        for e in EMOJI_IDS.keys():
            clean_text = clean_text.replace(e, "").strip()
        text = clean_text if clean_text else "Button"
    else:
        clean_text = text
        for e in EMOJI_IDS.keys():
            clean_text = clean_text.replace(e, "").strip()
        text = clean_text if clean_text else text
    if api_kwargs:
        return InlineKeyboardButton(text, callback_data=callback_data, **api_kwargs)
    return InlineKeyboardButton(text, callback_data=callback_data)

def btn_url(text: str, url: str, style: str = "primary", emoji: str = None) -> InlineKeyboardButton:
    api_kwargs = {}
    style_map = {"primary": "primary", "success": "success", "danger": "danger"}
    if style and style in style_map:
        api_kwargs["style"] = style_map[style]
    if emoji and emoji in EMOJI_IDS:
        api_kwargs["icon_custom_emoji_id"] = EMOJI_IDS[emoji]
        clean_text = text
        for e in EMOJI_IDS.keys():
            clean_text = clean_text.replace(e, "").strip()
        text = clean_text if clean_text else "Button"
    else:
        clean_text = text
        for e in EMOJI_IDS.keys():
            clean_text = clean_text.replace(e, "").strip()
        text = clean_text if clean_text else text
    if api_kwargs:
        return InlineKeyboardButton(text, url=url, **api_kwargs)
    return InlineKeyboardButton(text, url=url)

def premiumize_ui_emojis(text: Optional[str]) -> str:
    """Convert plain emojis to premium <tg-emoji> tags in bot UI text."""
    if not text:
        return ""
    # First remove any existing <tg-emoji> tags to avoid nesting
    text = re.sub(r'<tg-emoji[^>]*>', '', text)
    text = re.sub(r'</tg-emoji>', '', text)
    # Now convert plain emojis to premium
    for emoji_char, eid in EMOJI_IDS.items():
        text = text.replace(emoji_char, f'<tg-emoji emoji-id="{eid}">{emoji_char}</tg-emoji>')
    return text

def strip_premium_emojis(text: Optional[str]) -> str:
    """Strip <tg-emoji> tags and return plain text with plain emojis. For user-facing messages."""
    if not text:
        return ""
    text = re.sub(r'<tg-emoji[^>]*>', '', text)
    text = re.sub(r'</tg-emoji>', '', text)
    return text

def escape_preserving_premium_emojis(text: Optional[str]) -> str:
    """Escape unsafe HTML while keeping Telegram premium emoji tags usable."""
    if not text:
        return ""
    placeholders = {}

    def keep_tag(match):
        key = f"__PREMIUM_EMOJI_{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    protected = re.sub(r'</?tg-emoji(?:\s+emoji-id="\d+")?>', keep_tag, text)
    escaped = EmojiManager._html_escape(protected)
    for key, tag in placeholders.items():
        escaped = escaped.replace(key, tag)
    return escaped

# ================= STYLED KEYBOARD BUILDERS =================
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    lines = []
    user_bots = db.get_user_bots_by_owner(uid)
    if user_bots:
        for bot in user_bots:
            bot_id = bot["bot_id"]
            bot_username = bot["bot_username"]
            sub = db.get_subscription_for_bot(bot_id)
            is_active = False
            if sub and sub.get("expiry_date"):
                expiry = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
                if isinstance(expiry, str):
                    try:
                        expiry = datetime.fromisoformat(expiry.replace('+00:00', ''))
                        expiry = make_aware(expiry)
                    except:
                        expiry = now_aware()
                is_active = expiry > now_aware()
            status = "🟢" if is_active else "🔴"
            lines.append([btn(f"{status} @{bot_username}", f"manage_bot_{bot_id}", "primary", "🤖")])
        lines.append([btn("Add New Bot", "add_new_bot", "success", "➕")])
    else:
        lines.append([btn("Create New Bot", "add_new_bot", "success", "➕")])
    lines.append([btn_url("Contact Admin", f"https://t.me/{ADMIN_USERNAME.lstrip('@')}", "primary", "📞")])
    if is_admin(uid):
        lines.append([btn("Admin Panel", "admin_panel", "danger", "👑")])
    return InlineKeyboardMarkup(lines)

def bot_management_kb(bot_id: str, user_id: int) -> InlineKeyboardMarkup:
    sub = db.get_subscription_for_bot(bot_id)
    lines = []
    is_active = False
    days_left = 0
    if sub and sub.get("expiry_date"):
        expiry = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
        if isinstance(expiry, str):
            try:
                expiry = datetime.fromisoformat(expiry.replace('+00:00', ''))
                expiry = make_aware(expiry)
            except:
                expiry = now_aware()
        is_active = expiry > now_aware()
        if is_active:
            days_left = (expiry - now_aware()).days
    if is_active:
        lines.append([
            btn("Add Channel", f"ub_add_channel_{bot_id}", "success", "✈️"),
            btn("Set Message(s)", f"ub_set_message_{bot_id}", "primary", "📝")
        ])
        lines.append([
            btn("Preview/Edit", f"ub_manage_messages_{bot_id}", "primary", "👀"),
            btn("Delete Msgs", f"ub_delete_messages_{bot_id}", "danger", "🗑")
        ])
        lines.append([
            btn("Remove Channel", f"ub_remove_channel_{bot_id}", "danger", "❌"),
            btn("Auto-Approve", f"ub_toggle_auto_{bot_id}", "success", "⚙️")
        ])
        lines.append([
            btn("My Channels", f"ub_list_channels_{bot_id}", "primary", "📋"),
            btn("Bot Stats", f"ub_stats_{bot_id}", "primary", "📊")
        ])
        lines.append([
            btn("Pending", f"ub_pending_requests_{bot_id}", "primary", "📊"),
            btn("Accept All", f"ub_accept_all_{bot_id}", "success", "✅")
        ])
        lines.append([btn("Broadcast to Users", f"ub_broadcast_{bot_id}", "success", "✈️")])
        lines.append([btn(f"Subscription — {days_left}d left", f"ub_subscription_{bot_id}", "primary", "📅")])
    else:
        lines.append([btn("Get Subscription", f"sub_for_bot_{bot_id}", "danger", "⚠️")])
    lines.append([btn_url("Contact Admin", f"https://t.me/{ADMIN_USERNAME.lstrip('@')}", "primary", "📞")])
    lines.append([btn("Back to Main", "main_menu", "primary", "🔙")])
    return InlineKeyboardMarkup(lines)

def subscription_plans_kb(bot_id: str = None) -> InlineKeyboardMarkup:
    back_cb = f"manage_bot_{bot_id}" if bot_id else "main_menu"
    return InlineKeyboardMarkup([
        [btn("Basic — Rs2599/mo (1 channel)", f"sub_basic_{bot_id}" if bot_id else "sub_basic", "primary", "💰")],
        [btn("Pro — Rs3999/mo (5 channels)", f"sub_pro_{bot_id}" if bot_id else "sub_pro", "success", "⚡️")],
        [btn("Back", back_cb, "primary", "🔙")],
    ])

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("All Users", "admin_all_users", "primary", "📇"),
         btn("Manage UserBots", "admin_userbots", "success", "🤖")],
        [btn("Add UserBot", "admin_add_userbot", "success", "➕"),
         btn("Add Subscription", "admin_add_sub", "success", "⭐️")],
        [btn("Subscription List", "admin_sub_list", "primary", "📋")],
        [btn("Check Expiry", "admin_check_expiry", "primary", "⏰"),
         btn("Stats", "admin_stats", "primary", "📊")],
        [btn("Start All Bots", "admin_start_all", "success", "🚀"),
         btn("Stop All Bots", "admin_stop_all", "danger", "🛑")],
        [btn("Leave Recovery", "admin_leave_recovery", "primary", "🔔")],
        [btn("Default First Message", "admin_default_first_msg", "primary", "💬")],
        [btn("Broadcast", "admin_broadcast", "success", "✈️"),
         btn("Send Reminders", "admin_send_reminders", "primary", "🔔")],
        [btn("Main Menu", "main_menu", "primary", "🔙")],
    ])

def verification_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("Verify Now", "human_verify", "success", "✅")]])

def confirm_kb(confirm_cb: str, cancel_cb: str, confirm_text: str = "Confirm", cancel_text: str = "Cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn(confirm_text, confirm_cb, "success", "✅"),
         btn(cancel_text, cancel_cb, "danger", "❌")]
    ])

def pagination_kb(current_page: int, total_pages: int, prefix: str) -> list:
    nav = []
    if current_page > 1:
        nav.append(btn("Prev", f"{prefix}_page_{current_page-1}", "primary", "⬅️"))
    if current_page < total_pages:
        nav.append(btn("Next", f"{prefix}_page_{current_page+1}", "primary", "➡️"))
    return [nav] if nav else []

# ================= SUPPORT MAP HELPERS =================
def _store_support_map(store: dict, key, user_id: int):
    store[key] = {"uid": user_id, "ts": time.time()}

def _get_support_uid(store: dict, key):
    entry = store.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] > SUPPORT_MAP_TTL:
        store.pop(key, None)
        return None
    return entry["uid"]

def _cleanup_support_maps():
    now = time.time()
    for store in [SUPPORT_REPLY_MAP, USERBOT_SUPPORT_REPLY_MAP]:
        stale = [k for k, v in store.items() if now - v.get("ts", 0) > SUPPORT_MAP_TTL]
        for k in stale:
            store.pop(k, None)

def _parse_id(s: str):
    try:
        return int(s)
    except (ValueError, TypeError):
        return None

def _extract_last_id(parts: list) -> int:
    v = _parse_id(parts[-1])
    if v is not None:
        return v
    if len(parts) >= 2:
        v = _parse_id(parts[-2])
        if v is not None:
            return v
    return 0

# ================= DATABASE (MongoDB) =================
# Poora database layer MongoDB par hai (pehle PostgreSQL tha). Koi ORM nahi —
# seedha pymongo. Method names wahi rakhe gaye hain jo purane Database class me
# the, isliye baaki application code unchanged chalta hai.
#
# Collections:
#   users, user_bots, bot_subscriptions, user_bot_channels, user_bot_messages,
#   join_requests, reachable_users, user_emoji_maps, system_settings,
#   leave_recovery_messages, counters
#
# Config: MONGO_URI / MONGO_DB env vars (.env file se).

import certifi
import pymongo
from pymongo import ReturnDocument

MONGO_URI = os.getenv("MONGO_URI", "").strip() or "mongodb://127.0.0.1:27017"
MONGO_DB_NAME = os.getenv("MONGO_DB", "").strip() or "advacc_bot"


def _utc(dt):
    """DB se aaya koi bhi datetime -> timezone-aware UTC (ya None)."""
    if dt is None:
        return None
    return make_aware(dt) if isinstance(dt, datetime) else dt


def _awareify(v):
    """Recursive: dict/list ke andar ke saare datetime ko aware UTC banao."""
    if isinstance(v, datetime):
        return _utc(v)
    if isinstance(v, dict):
        return {k: _awareify(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_awareify(x) for x in v]
    return v


class Database:
    def __init__(self, client=None):
        if client is None:
            kwargs = {"serverSelectionTimeoutMS": 15000, "tz_aware": True}
            if MONGO_URI.startswith("mongodb+srv"):
                kwargs["tlsCAFile"] = certifi.where()
            client = pymongo.MongoClient(MONGO_URI, **kwargs)
        self.client = client
        self.db = client[MONGO_DB_NAME]
        self.init_db()
        logging.info("MongoDB connected (db=%s)", MONGO_DB_NAME)

    # ── collections shortcuts ──────────────────────────────────────────
    @property
    def users(self):
        return self.db.users

    @property
    def user_bots(self):
        return self.db.user_bots

    @property
    def bot_subscriptions(self):
        return self.db.bot_subscriptions

    @property
    def user_bot_channels(self):
        return self.db.user_bot_channels

    @property
    def user_bot_messages(self):
        return self.db.user_bot_messages

    @property
    def join_requests(self):
        return self.db.join_requests

    @property
    def reachable_users(self):
        return self.db.reachable_users

    @property
    def user_emoji_maps(self):
        return self.db.user_emoji_maps

    @property
    def system_settings(self):
        return self.db.system_settings

    @property
    def leave_recovery_messages(self):
        return self.db.leave_recovery_messages

    def _next_id(self, name: str) -> int:
        """Auto-increment integer id (PostgreSQL SERIAL jaisa) via counters."""
        doc = self.db.counters.find_one_and_update(
            {"_id": name}, {"$inc": {"seq": 1}}, upsert=True,
            return_document=ReturnDocument.AFTER)
        return int(doc["seq"])

    @staticmethod
    def _doc(doc):
        if not doc:
            return None
        d = dict(doc)
        d.pop("_id", None)
        return _awareify(d)

    def init_db(self):
        specs = [
            (self.users, [("user_id", 1)], True),
            (self.user_bots, [("bot_id", 1)], True),
            (self.user_bots, [("bot_token", 1)], True),
            (self.user_bots, [("user_id", 1)], False),
            (self.bot_subscriptions, [("bot_id", 1), ("expiry_date", -1)], False),
            (self.user_bot_channels, [("bot_id", 1), ("channel_id", 1)], True),
            (self.user_bot_messages, [("bot_id", 1), ("channel_id", 1), ("id", 1)], False),
            (self.join_requests, [("bot_id", 1), ("requester_id", 1), ("channel_id", 1)], True),
            (self.join_requests, [("bot_id", 1), ("status", 1)], False),
            (self.reachable_users, [("bot_id", 1), ("requester_id", 1)], True),
            (self.reachable_users, [("bot_id", 1), ("last_ok_at", -1)], False),
            (self.user_emoji_maps, [("bot_id", 1), ("msg_id", 1)], True),
            (self.system_settings, [("key", 1)], True),
            (self.leave_recovery_messages, [("bot_id", 1), ("user_id", 1), ("target_channel_id", 1)], False),
        ]
        for coll, keys, unique in specs:
            try:
                coll.create_index(keys, unique=unique)
            except Exception as e:
                logging.warning("Index creation warning: %s", e)

    # ── users ──────────────────────────────────────────────────────────
    def add_user(self, user_id, username, first_name, last_name):
        updates = {}
        if username is not None:
            updates["username"] = username
        if first_name is not None:
            updates["first_name"] = first_name
        if last_name is not None:
            updates["last_name"] = last_name
        self.users.update_one(
            {"user_id": user_id},
            {"$set": updates,
             "$setOnInsert": {"verified": False, "created_at": now_aware()}},
            upsert=True)

    def get_user(self, user_id):
        return self._doc(self.users.find_one({"user_id": user_id})) or {}

    def mark_user_verified(self, user_id: int):
        self.users.update_one({"user_id": user_id}, {"$set": {"verified": True}})

    def is_user_verified(self, user_id: int) -> bool:
        row = self.users.find_one({"user_id": user_id}, {"verified": 1})
        return bool(row and row.get("verified"))

    def get_all_users(self):
        rows = self.users.find({}, {"_id": 0}).sort("user_id", 1)
        return [self._doc(r) for r in rows]

    # ── user bots ──────────────────────────────────────────────────────
    def get_next_bot_id(self, user_id: int) -> str:
        numbers = []
        for row in self.user_bots.find({"user_id": user_id}, {"bot_id": 1}):
            parts = str(row.get("bot_id", "")).split("_")
            if len(parts) == 2 and parts[1].isdigit():
                numbers.append(int(parts[1]))
        next_num = max(numbers) + 1 if numbers else 1
        return f"{user_id}_{next_num}"

    def add_user_bot(self, user_id, token, username, tg_bot_id=None):
        # Token pehle se linked hai to wahi bot_id wapas (naya id mat banao)
        existing = self.user_bots.find_one({"bot_token": token})
        if existing:
            upd = {"bot_username": username, "is_active": 1}
            if tg_bot_id:
                upd["tg_bot_id"] = tg_bot_id
            self.user_bots.update_one({"bot_token": token}, {"$set": upd})
            return existing["bot_id"]
        bot_id = self.get_next_bot_id(user_id)
        self.add_user(user_id, None, f"User{user_id}", None)
        self.user_bots.insert_one({
            "bot_id": bot_id, "user_id": user_id, "bot_token": token,
            "bot_username": username, "is_active": 1,
            "tg_bot_id": tg_bot_id, "user_session": None,
            "created_at": now_aware()})
        return bot_id

    def set_user_session(self, bot_id: str, session_str: str):
        self.user_bots.update_one({"bot_id": bot_id},
                                  {"$set": {"user_session": session_str or None}})

    def set_user_bot_telegram_id(self, bot_id: str, tg_bot_id: int):
        if tg_bot_id is not None:
            self.user_bots.update_one(
                {"bot_id": bot_id}, {"$set": {"tg_bot_id": int(tg_bot_id)}})

    def get_user_bot(self, bot_id: str):
        return self._doc(self.user_bots.find_one({"bot_id": bot_id}))

    def get_user_bots_by_owner(self, user_id: int):
        rows = self.user_bots.find({"user_id": user_id}).sort("created_at", -1)
        return [dict(self._doc(r)) for r in rows]

    def get_all_user_bots(self):
        rows = self.user_bots.find({}).sort([("user_id", 1), ("created_at", 1)])
        return [self._doc(r) for r in rows]

    def get_bot_by_username(self, username: str):
        return self._doc(self.user_bots.find_one({"bot_username": username}))

    def set_user_bot_active(self, bot_id: str, active):
        self.user_bots.update_one({"bot_id": bot_id}, {"$set": {"is_active": 1 if active else 0}})

    def remove_user_bot(self, bot_id: str):
        # No FK cascades in MongoDB — related docs khud delete karo
        self.user_bots.delete_one({"bot_id": bot_id})
        self.bot_subscriptions.delete_many({"bot_id": bot_id})
        self.user_bot_channels.delete_many({"bot_id": bot_id})
        msg_ids = [m["id"] for m in self.user_bot_messages.find({"bot_id": bot_id}, {"id": 1})]
        self.user_bot_messages.delete_many({"bot_id": bot_id})
        if msg_ids:
            self.user_emoji_maps.delete_many({"bot_id": bot_id, "msg_id": {"$in": msg_ids}})
        else:
            self.user_emoji_maps.delete_many({"bot_id": bot_id})
        self.join_requests.delete_many({"bot_id": bot_id})
        self.reachable_users.delete_many({"bot_id": bot_id})

    # ── subscriptions ──────────────────────────────────────────────────
    def get_subscription_for_bot(self, bot_id: str):
        row = self.bot_subscriptions.find_one({"bot_id": bot_id}, sort=[("expiry_date", -1)])
        return self._doc(row)

    def add_subscription_for_bot(self, bot_id: str, sub_type, days):
        max_channels = 1 if str(sub_type).lower() == "basic" else 5
        self.bot_subscriptions.insert_one({
            "id": self._next_id("bot_subscriptions"),
            "bot_id": bot_id, "subscription_type": sub_type,
            "expiry_date": now_aware() + timedelta(days=days),
            "max_channels": max_channels,
            "reminder_3d_sent": False, "reminder_1d_sent": False,
            "created_at": now_aware()})

    def update_subscription_expiry(self, bot_id: str, new_expiry):
        row = self.get_subscription_for_bot(bot_id)
        if row:
            self.bot_subscriptions.update_one(
                {"id": row["id"]},
                {"$set": {"expiry_date": _utc(new_expiry),
                          "reminder_3d_sent": False, "reminder_1d_sent": False}})

    def get_expiring_subscriptions(self, days_threshold: int):
        reminder = "reminder_3d_sent" if days_threshold == 3 else "reminder_1d_sent"
        now = now_aware()
        until = now + timedelta(days=days_threshold)
        out = []
        for s in self.bot_subscriptions.find({reminder: {"$ne": True}}):
            exp = _utc(s.get("expiry_date"))
            if exp and now <= exp <= until:
                out.append({"bot_id": s.get("bot_id"),
                            "subscription_type": s.get("subscription_type"),
                            "expiry_date": exp})
        out.sort(key=lambda r: r["expiry_date"])
        return out

    def get_expired_subscriptions(self):
        # Bots jinke paas koi bhi live (expiry >= now) subscription nahi hai
        by_bot = {}
        for s in self.bot_subscriptions.find({}):
            by_bot.setdefault(s.get("bot_id"), []).append(_utc(s.get("expiry_date")))
        now = now_aware()
        expired = []
        for bot_id, expiries in by_bot.items():
            if not any(e and e >= now for e in expiries):
                expired.append(bot_id)
        return expired

    def mark_reminder_sent(self, bot_id: str, days: int):
        field = "reminder_3d_sent" if days == 3 else "reminder_1d_sent"
        self.bot_subscriptions.update_many(
            {"bot_id": bot_id, "expiry_date": {"$gte": now_aware()}},
            {"$set": {field: True}})

    def get_all_subscriptions(self):
        rows = []
        subs = list(self.bot_subscriptions.find({}).sort("expiry_date", -1))
        for s in subs:
            b = self.user_bots.find_one({"bot_id": s.get("bot_id")}) or {}
            u = self.users.find_one({"user_id": b.get("user_id")}) or {} if b else {}
            rows.append({
                "bot_id": s.get("bot_id"),
                "subscription_type": s.get("subscription_type"),
                "expiry_date": _utc(s.get("expiry_date")),
                "max_channels": s.get("max_channels"),
                "user_id": b.get("user_id"),
                "bot_username": b.get("bot_username"),
                "bot_active": int(b.get("is_active") or 0),
                "owner_username": u.get("username"),
                "owner_name": u.get("first_name"),
            })
        return rows

    # ── channels ───────────────────────────────────────────────────────
    def add_channel(self, bot_id: str, channel_id, username, title):
        self.user_bot_channels.update_one(
            {"bot_id": bot_id, "channel_id": channel_id},
            {"$set": {"channel_username": username, "channel_title": title},
             "$setOnInsert": {"welcome_message": None, "welcome_media_id": None,
                              "welcome_media_type": None, "auto_approve": 0,
                              "created_at": now_aware()}},
            upsert=True)

    def get_bot_channels(self, bot_id: str):
        rows = self.user_bot_channels.find({"bot_id": bot_id}).sort("channel_id", 1)
        return [self._doc(r) for r in rows]

    def set_auto_approve(self, bot_id: str, channel_id, val):
        self.user_bot_channels.update_one(
            {"bot_id": bot_id, "channel_id": channel_id},
            {"$set": {"auto_approve": 1 if val else 0}})

    def get_channel_owner_data(self, channel_id, bot_id=None):
        if bot_id:
            row = self.user_bot_channels.find_one({"bot_id": bot_id, "channel_id": channel_id})
        else:
            row = self.user_bot_channels.find_one(
                {"channel_id": channel_id}, sort=[("created_at", -1)])
        return self._doc(row)

    def clear_messages(self, bot_id: str, channel_id):
        msgs = list(self.user_bot_messages.find(
            {"bot_id": bot_id, "channel_id": channel_id}, {"id": 1}))
        for msg in msgs:
            self.delete_user_emoji_map(bot_id, msg["id"])
        self.user_bot_messages.delete_many({"bot_id": bot_id, "channel_id": channel_id})
        self.user_bot_channels.update_one(
            {"bot_id": bot_id, "channel_id": channel_id},
            {"$set": {"welcome_message": None, "welcome_media_id": None,
                      "welcome_media_type": None}})

    def remove_channel(self, bot_id: str, channel_id):
        self.clear_messages(bot_id, channel_id)
        self.user_bot_channels.delete_one({"bot_id": bot_id, "channel_id": channel_id})
        self.join_requests.delete_many({"bot_id": bot_id, "channel_id": channel_id})
        return True

    def _refresh_channel_welcome(self, bot_id: str, channel_id):
        first = self.user_bot_messages.find_one(
            {"bot_id": bot_id, "channel_id": channel_id}, sort=[("id", 1)])
        if first:
            self.user_bot_channels.update_one(
                {"bot_id": bot_id, "channel_id": channel_id},
                {"$set": {"welcome_message": first.get("content_text"),
                          "welcome_media_id": first.get("media_id"),
                          "welcome_media_type": first.get("media_type")}})
        else:
            self.user_bot_channels.update_one(
                {"bot_id": bot_id, "channel_id": channel_id},
                {"$set": {"welcome_message": None, "welcome_media_id": None,
                          "welcome_media_type": None}})

    # ── messages ───────────────────────────────────────────────────────
    def add_message(self, bot_id: str, channel_id, text, media_id, media_type,
                    media_group_id=None, entities_json=None, file_name=None,
                    mime_type=None, telegram_message_id=None):
        msg_id = self._next_id("user_bot_messages")
        self.user_bot_messages.insert_one({
            "id": msg_id, "bot_id": bot_id, "channel_id": channel_id,
            "content_text": text, "media_id": media_id, "media_type": media_type,
            "file_name": file_name, "mime_type": mime_type,
            "telegram_message_id": telegram_message_id,
            "media_group_id": media_group_id, "buttons_json": None,
            "entities_json": entities_json, "created_at": now_aware()})
        self._refresh_channel_welcome(bot_id, channel_id)
        return msg_id

    def update_message_text(self, msg_id, text, entities_json=None):
        row = self.get_message_by_id(msg_id)
        if row:
            updates = {"content_text": text}
            if entities_json is not None:
                updates["entities_json"] = entities_json
            self.user_bot_messages.update_one({"id": int(msg_id)}, {"$set": updates})
            self._refresh_channel_welcome(row["bot_id"], row["channel_id"])

    def update_message_media(self, msg_id, media_id, media_type, text=None,
                             entities_json=None, file_name=None, mime_type=None,
                             telegram_message_id=None):
        row = self.get_message_by_id(msg_id)
        if row:
            updates = {"media_id": media_id, "media_type": media_type}
            if text is not None:
                updates["content_text"] = text
            if entities_json is not None:
                updates["entities_json"] = entities_json
            if file_name is not None:
                updates["file_name"] = file_name
            if mime_type is not None:
                updates["mime_type"] = mime_type
            if telegram_message_id is not None:
                updates["telegram_message_id"] = telegram_message_id
            self.user_bot_messages.update_one({"id": int(msg_id)}, {"$set": updates})
            self._refresh_channel_welcome(row["bot_id"], row["channel_id"])

    def delete_message(self, msg_id):
        row = self.get_message_by_id(msg_id)
        if row:
            self.user_bot_messages.delete_one({"id": int(msg_id)})
            self.delete_user_emoji_map(row["bot_id"], int(msg_id))
            self._refresh_channel_welcome(row["bot_id"], row["channel_id"])
            return True
        return False

    def delete_message_by_telegram_id(self, bot_id: str, telegram_message_id: int):
        row = self.user_bot_messages.find_one(
            {"bot_id": bot_id, "telegram_message_id": telegram_message_id})
        if row:
            self.user_bot_messages.delete_one({"id": row["id"]})
            self.delete_user_emoji_map(bot_id, row["id"])
            self._refresh_channel_welcome(bot_id, row["channel_id"])
            return True
        return False

    def delete_media_group_messages(self, bot_id: str, media_group_id):
        first = self.user_bot_messages.find_one(
            {"bot_id": bot_id, "media_group_id": media_group_id})
        if first:
            ids = [r["id"] for r in self.user_bot_messages.find(
                {"bot_id": bot_id, "media_group_id": media_group_id}, {"id": 1})]
            self.user_bot_messages.delete_many(
                {"bot_id": bot_id, "media_group_id": media_group_id})
            for msg_id in ids:
                self.delete_user_emoji_map(bot_id, msg_id)
            self._refresh_channel_welcome(bot_id, first["channel_id"])

    def get_message_count(self, channel_id, bot_id=None):
        q = {"channel_id": channel_id}
        if bot_id:
            q["bot_id"] = bot_id
        return self.user_bot_messages.count_documents(q)

    def get_messages(self, channel_id, bot_id=None):
        q = {"channel_id": channel_id}
        if bot_id:
            q["bot_id"] = bot_id
        rows = self.user_bot_messages.find(q).sort("id", 1)
        return [self._doc(r) for r in rows]

    def get_message_by_id(self, msg_id):
        return self._doc(self.user_bot_messages.find_one({"id": int(msg_id)}))

    def get_message_by_telegram_id(self, bot_id: str, telegram_message_id: int):
        return self._doc(self.user_bot_messages.find_one(
            {"bot_id": bot_id, "telegram_message_id": telegram_message_id}))

    def update_message_buttons(self, msg_id, buttons_json):
        self.user_bot_messages.update_one({"id": int(msg_id)}, {"$set": {"buttons_json": buttons_json}})

    def append_message_buttons(self, msg_id, new_buttons_json):
        row = self.user_bot_messages.find_one({"id": int(msg_id)}, {"buttons_json": 1})
        existing = []
        if row and row.get("buttons_json"):
            try:
                existing = json.loads(row["buttons_json"]) or []
            except Exception:
                existing = []
        new_btns = []
        if new_buttons_json:
            try:
                new_btns = json.loads(new_buttons_json) or []
            except Exception:
                new_btns = []
        combined = existing + new_btns
        self.user_bot_messages.update_one(
            {"id": int(msg_id)}, {"$set": {"buttons_json": json.dumps(combined)}})

    # ── emoji maps ─────────────────────────────────────────────────────
    def save_user_emoji_map(self, bot_id: str, msg_id: int, emoji_map: dict):
        if emoji_map:
            self.user_emoji_maps.update_one(
                {"bot_id": bot_id, "msg_id": int(msg_id)},
                {"$set": {"emoji_map": dict(emoji_map), "updated_at": now_aware()}},
                upsert=True)

    def get_user_emoji_map(self, bot_id: str, msg_id: int) -> dict:
        row = self.user_emoji_maps.find_one({"bot_id": bot_id, "msg_id": int(msg_id)})
        return dict(row.get("emoji_map") or {}) if row else {}

    def delete_user_emoji_map(self, bot_id: str, msg_id: int):
        self.user_emoji_maps.delete_one({"bot_id": bot_id, "msg_id": int(msg_id)})

    # ── join requests ──────────────────────────────────────────────────
    def add_join_request(self, bot_id: str, requester_id, channel_id, status):
        key = {"bot_id": bot_id, "requester_id": requester_id, "channel_id": channel_id}
        existing = self.join_requests.find_one(key)
        if existing:
            # approved kabhi pending me downgrade nahi hota (purane SQL jaisa)
            new_status = status
            if existing.get("status") == "approved" and status == "pending":
                new_status = "approved"
            updates = {"status": new_status}
            if status == "approved":
                updates["approved_date"] = now_aware()
            self.join_requests.update_one({"id": existing["id"]}, {"$set": updates})
        else:
            self.join_requests.insert_one({
                "id": self._next_id("join_requests"), **key,
                "status": status,
                "request_date": now_aware(),
                "approved_date": now_aware() if status == "approved" else None})

    def get_pending_requests(self, bot_id: str):
        rows = self.join_requests.find(
            {"bot_id": bot_id, "status": "pending"}).sort("request_date", 1)
        return [{"id": r["id"], "requester_id": r["requester_id"],
                 "channel_id": r["channel_id"]} for r in rows]

    def mark_request_status(self, request_id, status):
        updates = {"status": status}
        if status == "approved":
            updates["approved_date"] = now_aware()
        self.join_requests.update_one({"id": int(request_id)}, {"$set": updates})

    def get_pending_count(self, bot_id: str):
        return self.join_requests.count_documents({"bot_id": bot_id, "status": "pending"})

    # ── reachability ───────────────────────────────────────────────────
    def mark_reachable(self, bot_id: str, requester_id):
        self.reachable_users.update_one(
            {"bot_id": bot_id, "requester_id": requester_id},
            {"$set": {"last_ok_at": now_aware()}}, upsert=True)

    def mark_unreachable(self, bot_id: str, requester_id):
        self.reachable_users.delete_one({"bot_id": bot_id, "requester_id": requester_id})

    def get_requesters_for_bot(self, bot_id: str):
        rows = list(self.reachable_users.find(
            {"bot_id": bot_id}, {"requester_id": 1}).sort("last_ok_at", -1))
        if rows:
            return [r["requester_id"] for r in rows]
        seen = []
        for r in self.join_requests.find(
                {"bot_id": bot_id, "status": "approved"}, {"requester_id": 1}):
            if r["requester_id"] not in seen:
                seen.append(r["requester_id"])
        return seen

    def get_total_requesters_count(self, bot_id: str):
        return len(self.join_requests.distinct("requester_id", {"bot_id": bot_id}))

    def get_reachable_requesters_count(self, bot_id: str):
        return self.reachable_users.count_documents({"bot_id": bot_id})

    def get_userbot_user_counts(self):
        rows = []
        for b in self.get_all_user_bots():
            users = len(self.join_requests.distinct("requester_id", {"bot_id": b["bot_id"]}))
            sub = self.get_subscription_for_bot(b["bot_id"])
            plan = sub.get("subscription_type") if sub else "None"
            exp = _utc(sub.get("expiry_date")) if sub else None
            expired = (exp < now_aware()) if exp else True
            rows.append({"bot_id": b["bot_id"], "bot_username": b.get("bot_username"),
                         "user_id": b.get("user_id"), "users": users,
                         "plan": plan or "None", "expired": bool(expired)})
        return rows

    # ── settings ───────────────────────────────────────────────────────
    def get_setting(self, key: str, default=None):
        row = self.system_settings.find_one({"key": key})
        return row.get("value") if row else default

    def set_setting(self, key: str, value: dict):
        self.system_settings.update_one(
            {"key": key},
            {"$set": {"value": value, "updated_at": now_aware()}}, upsert=True)

    def get_leave_recovery_config(self) -> dict:
        cfg = self.get_setting("leave_recovery", {}) or {}
        cfg.setdefault("enabled", False)
        cfg.setdefault("target_channel_id", None)
        cfg.setdefault("target_channel_link", "")
        cfg.setdefault("messages", [])
        if not cfg["messages"] and cfg.get("message"):
            cfg["messages"] = [{"text": cfg["message"], "buttons_json": cfg.get("buttons_json", "")}]
        cfg.setdefault("channel_configs", {})
        return cfg

    def set_leave_recovery_config(self, cfg: dict):
        self.set_setting("leave_recovery", cfg)

    def get_default_first_message(self) -> str:
        return self.get_setting("default_first_message", None) or (
            "Hello {first_name},\n\nAapki request mil gayi hai, jaldi hi accept ho jayegi.\n\n"
            "Tab tak aap niche diye hue video dekh lo ⚠️ Miss mat karna — properly follow karna!")

    def set_default_first_message(self, text: str):
        self.set_setting("default_first_message", text)

    # ── leave recovery tracking ────────────────────────────────────────
    def add_leave_recovery_message(self, bot_id: str, user_id, source_channel_id,
                                   target_channel_id, message_id):
        self.leave_recovery_messages.insert_one({
            "id": self._next_id("leave_recovery_messages"),
            "bot_id": bot_id, "user_id": user_id,
            "source_channel_id": source_channel_id,
            "target_channel_id": target_channel_id,
            "message_id": message_id,
            "sent_at": now_aware(), "deleted_at": None})

    def get_pending_leave_recovery_messages(self, bot_id: str, user_id, target_channel_id):
        rows = self.leave_recovery_messages.find({
            "bot_id": bot_id, "user_id": user_id,
            "target_channel_id": target_channel_id,
            "deleted_at": None}).sort("sent_at", -1)
        return [(r["id"], r["message_id"]) for r in rows]

    def mark_leave_recovery_deleted(self, row_id):
        self.leave_recovery_messages.update_one(
            {"id": int(row_id)}, {"$set": {"deleted_at": now_aware()}})

    def clear_pending_leave_recovery(self):
        self.leave_recovery_messages.update_many(
            {"deleted_at": None}, {"$set": {"deleted_at": now_aware()}})


# ================= GLOBALS =================
# Database MongoDB par hai — module import par connect NAHI hota (tests aur
# tooling ke liye lazy). init_database() main() aur tests dono use karte hain.
db: Optional[Database] = None
user_bot_applications: Dict[str, Application] = {}

# User-account sender ka singleton (user_sender.py). User-facing DMs ka ek hi
# sender hai: logged-in Telegram user account. Bot API fallback jaan-bujhkar band hai.
user_account: UserAccountSender = user_sender_mod.user_sender


def init_database(client=None) -> Database:
    """MongoDB Database init karo (injectable client ke saath tests ke liye)."""
    global db
    db = Database(client)
    return db


# ================= EMOJI MANAGER =================
class EmojiManager:
    @staticmethod
    def extract_from_entities(text: str, entities: Optional[List]) -> dict:
        if not entities or not text:
            return {}
        emoji_map = {}
        char_to_utf16 = []
        utf16_pos = 0
        for ch in text:
            char_to_utf16.append(utf16_pos)
            utf16_pos += len(ch.encode('utf-16-le')) // 2
        for entity in entities:
            if entity.type == "custom_emoji" and entity.custom_emoji_id:
                try:
                    start_utf16 = entity.offset
                    end_utf16 = entity.offset + entity.length
                    start_char = None
                    end_char = None
                    for ci, u16 in enumerate(char_to_utf16):
                        if u16 == start_utf16 and start_char is None:
                            start_char = ci
                        if u16 == end_utf16 and end_char is None:
                            end_char = ci
                            break
                    if end_char is None:
                        end_char = len(text)
                    if start_char is None:
                        continue
                    emoji_char = text[start_char:end_char]
                    emoji_map[emoji_char] = entity.custom_emoji_id
                except Exception as ex:
                    logging.warning(f"Emoji extraction failed: {ex}")
        return emoji_map

    @staticmethod
    def entities_to_json(entities: Optional[List]) -> Optional[str]:
        if not entities:
            return None
        try:
            serialized = []
            for e in entities:
                d = {"type": e.type, "offset": e.offset, "length": e.length}
                if hasattr(e, "custom_emoji_id") and e.custom_emoji_id:
                    d["custom_emoji_id"] = e.custom_emoji_id
                if hasattr(e, "url") and e.url:
                    d["url"] = e.url
                if hasattr(e, "user") and e.user:
                    d["user_id"] = e.user.id
                if hasattr(e, "language") and e.language:
                    d["language"] = e.language
                serialized.append(d)
            return json.dumps(serialized, ensure_ascii=False)
        except Exception as ex:
            logging.error(f"Error serializing entities: {ex}")
            return None

    @staticmethod
    def render_entities_html(text: str, entities_json: Optional[str]) -> str:
        if not text:
            return ""
        if not entities_json:
            return EmojiManager._html_escape(text)
        try:
            entities = json.loads(entities_json)
        except Exception:
            return EmojiManager._html_escape(text)
        if not entities:
            return EmojiManager._html_escape(text)
        char_to_utf16: List[int] = []
        utf16_pos = 0
        for ch in text:
            char_to_utf16.append(utf16_pos)
            utf16_pos += len(ch.encode('utf-16-le')) // 2
        total_utf16 = utf16_pos
        opens: Dict[int, List[str]] = {}
        closes: Dict[int, List[str]] = {}
        for e in sorted(entities, key=lambda x: (x.get("offset", 0), -(x.get("length", 0)))):
            etype = e.get("type", "")
            offset = e.get("offset", 0)
            length = e.get("length", 0)
            end = offset + length
            open_tag = close_tag = None
            if etype == "bold":
                open_tag, close_tag = "<b>", "</b>"
            elif etype == "italic":
                open_tag, close_tag = "<i>", "</i>"
            elif etype == "code":
                open_tag, close_tag = "<code>", "</code>"
            elif etype == "pre":
                lang = e.get("language", "")
                open_tag = f'<pre><code class="language-{lang}">' if lang else "<pre>"
                close_tag = "</code></pre>" if lang else "</pre>"
            elif etype == "strikethrough":
                open_tag, close_tag = "<s>", "</s>"
            elif etype == "underline":
                open_tag, close_tag = "<u>", "</u>"
            elif etype == "spoiler":
                open_tag, close_tag = '<span class="tg-spoiler">', "</span>"
            elif etype == "blockquote":
                open_tag, close_tag = "<blockquote>", "</blockquote>"
            elif etype == "text_link":
                url = e.get("url", "")
                open_tag, close_tag = f'<a href="{url}">', "</a>"
            elif etype == "custom_emoji":
                emoji_id = e.get("custom_emoji_id", "")
                open_tag = f'<tg-emoji emoji-id="{emoji_id}">'
                close_tag = "</tg-emoji>"
            if open_tag and close_tag:
                opens.setdefault(offset, []).append(open_tag)
                closes.setdefault(end, []).append(close_tag)
        result = []
        for i, ch in enumerate(text):
            u16 = char_to_utf16[i]
            if u16 in closes:
                for ct in reversed(closes[u16]):
                    result.append(ct)
            if u16 in opens:
                for ot in opens[u16]:
                    result.append(ot)
            if ch == '<':
                result.append('&lt;')
            elif ch == '>':
                result.append('&gt;')
            elif ch == '&':
                result.append('&amp;')
            else:
                result.append(ch)
        if total_utf16 in closes:
            for ct in reversed(closes[total_utf16]):
                result.append(ct)
        return "".join(result)

    @staticmethod
    def _html_escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class MessageManager:
    @staticmethod
    def extract_from_message(msg) -> dict:
        text = msg.text or msg.caption or ""
        entities = list(msg.entities or msg.caption_entities or [])
        media_id = None
        media_type = None
        file_name = None
        mime_type = None
        telegram_message_id = msg.message_id
        if msg.photo:
            media_id = msg.photo[-1].file_id
            media_type = "photo"
        elif msg.video:
            media_id = msg.video.file_id
            media_type = "video"
            file_name = getattr(msg.video, "file_name", None)
            mime_type = getattr(msg.video, "mime_type", None)
        elif msg.document:
            media_id = msg.document.file_id
            media_type = "document"
            file_name = getattr(msg.document, "file_name", None)
            mime_type = getattr(msg.document, "mime_type", None)
        elif msg.animation:
            media_id = msg.animation.file_id
            media_type = "animation"
            file_name = getattr(msg.animation, "file_name", None)
            mime_type = getattr(msg.animation, "mime_type", None)
        elif msg.audio:
            media_id = msg.audio.file_id
            media_type = "audio"
            file_name = getattr(msg.audio, "file_name", None)
            mime_type = getattr(msg.audio, "mime_type", None)
        elif msg.voice:
            media_id = msg.voice.file_id
            media_type = "voice"
            mime_type = getattr(msg.voice, "mime_type", None)
        elif msg.video_note:
            media_id = msg.video_note.file_id
            media_type = "video_note"
        elif msg.sticker:
            media_id = msg.sticker.file_id
            media_type = "sticker"
        elif msg.contact:
            media_type = "contact"
        elif msg.location:
            media_type = "location"
        elif msg.venue:
            media_type = "venue"
        elif msg.poll:
            media_type = "poll"
        elif msg.dice:
            media_type = "dice"
        emoji_map = EmojiManager.extract_from_entities(text, entities)
        entities_json = EmojiManager.entities_to_json(entities)
        return {
            "text": text, "entities": entities, "entities_json": entities_json,
            "emoji_map": emoji_map, "media_id": media_id, "media_type": media_type,
            "file_name": file_name, "mime_type": mime_type,
            "media_group_id": getattr(msg, "media_group_id", None),
            "telegram_message_id": telegram_message_id,
        }

    @staticmethod
    def prepare_for_sending(text: str, entities_json: Optional[str] = None,
                            emoji_map: Optional[dict] = None) -> str:
        """Prepare text for sending to USERS - uses plain emojis, no premium tags."""
        if not text:
            return ""
        if entities_json:
            # Use entities but any custom_emoji will be rendered as plain text by render_entities_html
            return EmojiManager.render_entities_html(text, entities_json)
        return EmojiManager._html_escape(text)


class UIFormatter:
    """Bot UI messages - use pp() for PREMIUM emojis in admin/bot interface."""
    @staticmethod
    def main_menu(user_name: str = "") -> str:
        name_part = f" <b>{user_name}</b>" if user_name else ""
        return (f"<blockquote>{pp('💎')} <b>WELCOME{name_part}</b></blockquote>\n\n"
                f"<b>Your Premium Bot Panel</b>\n\n"
                f"{pp('💎')} Manage your bots\n"
                f"{pp('⚡️')} Set welcome messages\n"
                f"{pp('📊')} Track your users\n"
                f"{pp('📣')} Broadcast to audience\n\n"
                f"<i>Select a bot below to get started:</i>")

    @staticmethod
    def verification_prompt() -> str:
        return (f"<blockquote>{pp('🔐')} <b>HUMAN VERIFICATION REQUIRED</b></blockquote>\n\n"
                f"To ensure you are a real person, please complete verification.\n\n"
                f"<i>Click the button to verify.</i>")

    @staticmethod
    def verification_success(first_name: str) -> str:
        return (f"<blockquote>{pp('✅')} <b>VERIFICATION COMPLETE</b></blockquote>\n\n"
                f"Welcome, <b>{first_name}</b>! {pp('🎉')}\n\n"
                f"You now have access to the bot panel.")

    @staticmethod
    def subscription_required() -> str:
        return (f"<blockquote>{pp('💎')} <b>SUBSCRIPTION REQUIRED</b></blockquote>\n\n"
                f"You need an active subscription.\n\n"
                f"<b>Plans:</b>\n"
                f"• {pp('💰')} Basic — Rs2599/mo — 1 channel\n"
                f"• {pp('⚡️')} Pro — Rs3999/mo — 5 channels\n\n"
                f"Contact {ADMIN_USERNAME}")

    @staticmethod
    def subscription_details(sub_type: str, expiry, days: int, max_ch: int) -> str:
        if isinstance(expiry, str):
            try:
                expiry = datetime.fromisoformat(expiry.replace('+00:00', ''))
            except:
                expiry = now_aware()
        expiry = make_aware(expiry) if expiry.tzinfo is None else expiry
        status = f"{pp('✅')} Active" if days > 0 else f"{pp('❌')} Expired"
        return (f"<blockquote>{pp('👑')} <b>YOUR SUBSCRIPTION</b></blockquote>\n\n"
                f"{pp('⭐️')} <b>Plan:</b> {sub_type}\n"
                f"{pp('💎')} <b>Max Channels:</b> {max_ch}\n"
                f"{pp('📅')} <b>Expiry:</b> {expiry.strftime('%d %b %Y')}\n"
                f"{pp('⏰')} <b>Days Left:</b> {days}\n"
                f"{pp('🔘')} <b>Status:</b> {status}")

    @staticmethod
    def bot_stats(channels: int, total_users: int, reachable: int, pending: int) -> str:
        return (f"<blockquote>{pp('📊')} <b>BOT STATISTICS</b></blockquote>\n\n"
                f"{pp('📣')} <b>Channels:</b> {channels}\n"
                f"{pp('👀')} <b>Total Users:</b> {total_users}\n"
                f"{pp('✅')} <b>Reachable:</b> {reachable}\n"
                f"{pp('🔔')} <b>Pending:</b> {pending}")

    @staticmethod
    def live_chat_header() -> str:
        return (f"<blockquote>{pp('💬')} <b>LIVE CHAT SUPPORT</b></blockquote>\n\n"
                f"You are now connected to support.\n"
                f"Please type your message.")

    @staticmethod
    def broadcast_confirm(sent: int, failed: int) -> str:
        return (f"<blockquote>{pp('✅')} <b>BROADCAST COMPLETE</b></blockquote>\n\n"
                f"{pp('📤')} Sent: {sent}\n"
                f"{pp('❌')} Failed: {failed}")

    @staticmethod
    def expiry_reminder_3d(sub_type: str, expiry: datetime, days: int) -> str:
        return (f"<blockquote>{pp('🔔')} <b>SUBSCRIPTION EXPIRY REMINDER</b></blockquote>\n\n"
                f"{pp('⭐️')} <b>Plan:</b> {sub_type}\n"
                f"{pp('📅')} <b>Expires:</b> {expiry.strftime('%d %b %Y')}\n"
                f"{pp('⏰')} <b>Days left:</b> {days}\n\n"
                f"Renew now! Contact {ADMIN_USERNAME}")

    @staticmethod
    def expiry_reminder_1d(sub_type: str, expiry: datetime) -> str:
        return (f"<blockquote>{pp('‼️')} <b>LAST DAY REMINDER</b></blockquote>\n\n"
                f"{pp('⭐️')} <b>Plan:</b> {sub_type}\n"
                f"{pp('📅')} <b>Expires TOMORROW:</b> {expiry.strftime('%d %b %Y')}\n\n"
                f"{pp('🚨')} Renew immediately! Contact {ADMIN_USERNAME}")

    @staticmethod
    def subscription_expired() -> str:
        return (f"<blockquote>{pp('❌')} <b>SUBSCRIPTION EXPIRED</b></blockquote>\n\n"
                f"Your bot has been paused. Contact {ADMIN_USERNAME} to renew.")


class UserFlowManager:
    @staticmethod
    def needs_verification(user_id: int) -> bool:
        return not db.is_user_verified(user_id)

    @staticmethod
    def verification_button() -> InlineKeyboardMarkup:
        return verification_kb()


# ================= HELPER FUNCTIONS =================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def is_bot_owner(bot_id: str, user_id: int) -> bool:
    """Check if user is owner of the bot OR is admin"""
    if is_admin(user_id):
        return True
    bot_data = db.get_user_bot(bot_id)
    return bool(bot_data and bot_data.get("user_id") == user_id)


@retry_async(max_retries=3, delay=1, backoff=2)
async def safe_edit_message_text(q, *args, **kwargs):
    """
    Tries the "styled" version first (premium <tg-emoji> text + custom-emoji button
    icons). Telegram's editMessageText only works on messages originally sent as
    plain TEXT, and custom-emoji references (in text or button icons) can be
    rejected by Telegram as invalid/inaccessible "documents" - both cases can
    surface as BadRequest (e.g. "Document_invalid").

    Fallback chain so navigation never breaks:
      1. Styled edit (as requested)
      2. Plain edit (strip premium emoji tags from text + strip button icons)
      3. Delete old message + send a brand new plain text message
    """
    raw_text = None
    if len(args) > 0:
        args = list(args)
        raw_text = args[0]
        args[0] = premiumize_ui_emojis(raw_text)
    elif "text" in kwargs:
        raw_text = kwargs["text"]
        kwargs["text"] = premiumize_ui_emojis(raw_text)

    try:
        return await q.edit_message_text(*args, **kwargs)
    except BadRequest as ex:
        if "Message is not modified" in str(ex):
            return None

        logging.warning(f"styled edit_message_text failed ({ex}); retrying plain")
        plain_text = strip_premium_emojis(raw_text) if raw_text else raw_text
        plain_kwargs = dict(kwargs)
        if "reply_markup" in plain_kwargs:
            plain_kwargs["reply_markup"] = _degrade_markup(plain_kwargs["reply_markup"])
        try:
            if len(args) > 0:
                plain_args = list(args)
                plain_args[0] = plain_text
                return await q.edit_message_text(*plain_args, **plain_kwargs)
            else:
                plain_kwargs["text"] = plain_text
                return await q.edit_message_text(**plain_kwargs)
        except Exception as ex2:
            logging.warning(f"plain edit also failed ({ex2}); falling back to delete+resend")

        try:
            await q.message.delete()
        except Exception:
            pass
        try:
            send_kwargs = {k: v for k, v in plain_kwargs.items() if k != "text"}
            return await q.message.chat.send_message(plain_text or "", **send_kwargs)
        except Exception as send_ex:
            logging.error(f"safe_edit_message_text fallback send also failed: {send_ex}")
            return None


@retry_async(max_retries=2, delay=0.5, backoff=1.5)
async def send_premium_message(bot, chat_id, text, *args, **kwargs):
    """Send message with PREMIUM emojis (for bot UI/admin messages). Falls back to
    a plain (non-premium) version if Telegram rejects the styled one."""
    try:
        premium_text = premiumize_ui_emojis(text)
        return await bot.send_message(chat_id, premium_text, *args, **kwargs)
    except Forbidden:
        logging.warning(f"Cannot send message to {chat_id}: bot blocked or can't initiate")
        return None
    except BadRequest as ex:
        logging.warning(f"styled send_premium_message failed for {chat_id} ({ex}); retrying plain")
        try:
            plain_kwargs = dict(kwargs)
            if "reply_markup" in plain_kwargs:
                plain_kwargs["reply_markup"] = _degrade_markup(plain_kwargs["reply_markup"])
            return await bot.send_message(chat_id, strip_premium_emojis(text), *args, **plain_kwargs)
        except Exception as ex2:
            logging.error(f"plain retry of send_premium_message also failed for {chat_id}: {ex2}")
            return None
    except (NetworkError, TimedOut) as ex:
        logging.warning(f"Network error sending to {chat_id}: {ex}")
        raise
    except Exception as ex:
        logging.error(f"send_premium_message failed: {ex}")
        return None


@retry_async(max_retries=2, delay=0.5, backoff=1.5)
async def reply_premium_message(message, text, *args, **kwargs):
    """Reply with PREMIUM emojis (for bot UI/admin messages). Falls back to a
    plain (non-premium) version if Telegram rejects the styled one."""
    try:
        premium_text = premiumize_ui_emojis(text)
        return await message.reply_text(premium_text, *args, **kwargs)
    except Forbidden:
        logging.warning(f"Cannot reply to {message.chat_id}: bot blocked")
        return None
    except BadRequest as ex:
        logging.warning(f"styled reply_premium_message failed for {message.chat_id} ({ex}); retrying plain")
        try:
            plain_kwargs = dict(kwargs)
            if "reply_markup" in plain_kwargs:
                plain_kwargs["reply_markup"] = _degrade_markup(plain_kwargs["reply_markup"])
            return await message.reply_text(strip_premium_emojis(text), *args, **plain_kwargs)
        except Exception as ex2:
            logging.error(f"plain retry of reply_premium_message also failed for {message.chat_id}: {ex2}")
            return None
    except (NetworkError, TimedOut) as ex:
        logging.warning(f"Network error replying to {message.chat_id}: {ex}")
        raise
    except Exception as ex:
        logging.error(f"reply_premium_message failed: {ex}")
        return None


@retry_async(max_retries=2, delay=0.5, backoff=1.5)
async def send_user_message(bot, chat_id, text, *args, **kwargs):
    """Send user-facing messages without stripping premium custom emoji tags."""
    try:
        return await bot.send_message(chat_id, text, *args, **kwargs)
    except Forbidden:
        logging.warning(f"Cannot send message to {chat_id}: bot blocked or can't initiate")
        return None
    except BadRequest as ex:
        if kwargs.get("parse_mode") == ParseMode.HTML and "can't parse entities" in str(ex).lower():
            logging.warning(f"HTML parse failed for {chat_id}; retrying with escaped text: {ex}")
            safe_kwargs = dict(kwargs)
            safe_kwargs["parse_mode"] = ParseMode.HTML
            safe_text = escape_preserving_premium_emojis(text)
            try:
                return await bot.send_message(chat_id, safe_text, *args, **safe_kwargs)
            except Exception as retry_ex:
                logging.error(f"send_user_message escaped retry failed: {retry_ex}")
                return None
        logging.error(f"send_user_message failed: {ex}")
        return None
    except (NetworkError, TimedOut) as ex:
        logging.warning(f"Network error sending to {chat_id}: {ex}")
        raise
    except Exception as ex:
        logging.error(f"send_user_message failed: {ex}")
        return None


DEFAULT_WELCOME_MESSAGE = "Hello {first_name}, Aapki request mil gayi hai, jaldi hi accept ho jayegi."


def render_dynamic_text(text: Optional[str], user=None, extra: Optional[dict] = None) -> str:
    if not text:
        return ""
    first_name = getattr(user, "first_name", None) or "User"
    last_name = getattr(user, "last_name", None) or ""
    username = getattr(user, "username", None) or ""
    user_id = getattr(user, "id", None) or ""
    values = {"first_name": first_name, "last_name": last_name,
              "full_name": f"{first_name} {last_name}".strip(),
              "username": f"@{username}" if username else "", "user_id": str(user_id)}
    if extra:
        values.update({str(k): "" if v is None else str(v) for k, v in extra.items()})
    rendered = text
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def parse_buttons_text(text: Optional[str]):
    if not text:
        return []
    buttons = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(' || ')]
        row_buttons = []
        for part in parts:
            if '|' not in part:
                continue
            idx = part.index('|')
            label = part[:idx].strip()
            url = part[idx + 1:].strip()
            if label and url:
                icon_id = None
                display_label = label
                for emoji_char, eid in EMOJI_IDS.items():
                    if label.startswith(emoji_char):
                        icon_id = eid
                        display_label = label[len(emoji_char):].strip()
                        break
                if not icon_id:
                    icon_id = EMOJI_IDS.get("🔗", "5042101437237036298")
                row_buttons.append(InlineKeyboardButton(
                    display_label or label, url=url,
                    api_kwargs={"style": "primary", "icon_custom_emoji_id": icon_id}
                ))
        if row_buttons:
            buttons.append(row_buttons)
    return buttons if buttons else []


def buttons_to_markup(buttons_json: Optional[str]):
    if not buttons_json:
        return None
    try:
        data = json.loads(buttons_json)
        if not data:
            return None
        rows = []
        for row in data:
            row_btns = []
            for btn_data in row:
                text = btn_data.get('text', '')
                icon_id = btn_data.get('icon_id') or EMOJI_IDS.get("🔗", "5042101437237036298")
                if btn_data.get('url'):
                    row_btns.append(InlineKeyboardButton(
                        text, url=btn_data['url'],
                        api_kwargs={"style": "primary", "icon_custom_emoji_id": icon_id}
                    ))
                elif btn_data.get('cb'):
                    row_btns.append(InlineKeyboardButton(text, callback_data=btn_data['cb']))
                elif btn_data.get('callback_data'):
                    row_btns.append(InlineKeyboardButton(text, callback_data=btn_data['callback_data']))
            if row_btns:
                rows.append(row_btns)
        return InlineKeyboardMarkup(rows) if rows else None
    except Exception:
        return None


def buttons_json_from_text(text: str):
    if not text:
        return None
    json_rows = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(' || ')]
        json_row = []
        for part in parts:
            if '|' not in part:
                continue
            idx = part.index('|')
            label = part[:idx].strip()
            url = part[idx + 1:].strip()
            if not label or not url:
                continue
            icon_id = None
            display_label = label
            for emoji_char, eid in EMOJI_IDS.items():
                if label.startswith(emoji_char):
                    icon_id = eid
                    display_label = label[len(emoji_char):].strip()
                    break
            if not icon_id:
                icon_id = EMOJI_IDS.get("🔗", "5042101437237036298")
            json_row.append({
                "text": display_label or label,
                "url": url,
                "icon_id": icon_id
            })
        if json_row:
            json_rows.append(json_row)
    return json.dumps(json_rows) if json_rows else None


def add_callback_button_to_json(buttons_json: Optional[str], text: str, cb: str, url: Optional[str] = None) -> str:
    data = []
    if buttons_json:
        try:
            data = json.loads(buttons_json) or []
        except Exception:
            data = []
    for row in data:
        for btn_data in row:
            if (btn_data.get('cb') == cb or btn_data.get('callback_data') == cb or (url and btn_data.get('url') == url)):
                return json.dumps(data)
    if url:
        data.append([{"text": text, "url": url}])
    else:
        data.append([{"text": text, "cb": cb}])
    return json.dumps(data)


async def send_ephemeral_reply(msg, text: str, seconds: int = 2):
    try:
        sent = await reply_premium_message(msg, text, parse_mode=ParseMode.HTML)
        if sent:
            await asyncio.sleep(seconds)
            try:
                await sent.delete()
            except Exception:
                pass
    except Exception:
        pass


@retry_async(max_retries=2, delay=0.5, backoff=1.5)
async def send_media(bot_or_context, chat_id: int, media_id, media_type: str,
                     text: str = "", markup=None, emoji_map: dict = None,
                     entities_json: Optional[str] = None, file_name: Optional[str] = None,
                     mime_type: Optional[str] = None):
    # PTB Bot/ExtBot ya koi bhi duck-typed bot object (tests) — dono chalte hain
    bot = bot_or_context if hasattr(bot_or_context, "send_message") else bot_or_context.bot
    kwargs = {}
    if markup:
        kwargs["reply_markup"] = markup
    if text:
        display_text = MessageManager.prepare_for_sending(text, entities_json, emoji_map)
    else:
        display_text = None
    async def _do_send(send_kwargs):
        if media_type == "photo":
            await bot.send_photo(chat_id, media_id, caption=display_text or None, parse_mode=ParseMode.HTML if display_text else None, **send_kwargs)
        elif media_type == "video":
            await bot.send_video(chat_id, media_id, caption=display_text or None, parse_mode=ParseMode.HTML if display_text else None, **send_kwargs)
        elif media_type == "document":
            doc_kwargs = dict(send_kwargs)
            if file_name:
                doc_kwargs["filename"] = file_name
            await bot.send_document(chat_id, media_id, caption=display_text or None, parse_mode=ParseMode.HTML if display_text else None, **doc_kwargs)
        elif media_type == "animation":
            await bot.send_animation(chat_id, media_id, caption=display_text or None, parse_mode=ParseMode.HTML if display_text else None, **send_kwargs)
        elif media_type == "audio":
            await bot.send_audio(chat_id, media_id, caption=display_text or None, parse_mode=ParseMode.HTML if display_text else None, **send_kwargs)
        elif media_type == "voice":
            await bot.send_voice(chat_id, media_id, caption=display_text or None, parse_mode=ParseMode.HTML if display_text else None, **send_kwargs)
        elif media_type == "video_note":
            await bot.send_video_note(chat_id, media_id, **send_kwargs)
        elif media_type == "sticker":
            await bot.send_sticker(chat_id, media_id, **send_kwargs)
        else:
            if display_text:
                await send_user_message(bot, chat_id, display_text, parse_mode=ParseMode.HTML, **send_kwargs)

    try:
        await _do_send(kwargs)
    except Forbidden:
        logging.warning(f"Cannot send media to {chat_id}: bot blocked")
    except BadRequest as ex:
        # Try again with button icons stripped first - a single bad custom-emoji
        # icon on a button shouldn't cost the whole media delivery.
        logging.warning(f"send_media BadRequest for {chat_id} ({media_type}): {ex}; retrying with plain buttons")
        degraded_kwargs = dict(kwargs)
        if "reply_markup" in degraded_kwargs:
            degraded_kwargs["reply_markup"] = _degrade_markup(degraded_kwargs["reply_markup"])
        try:
            await _do_send(degraded_kwargs)
        except Exception as ex2:
            logging.error(f"send_media plain-button retry also failed for {chat_id} ({media_type}): {ex2}")
            try:
                plain_text = (text or "").strip()
                if plain_text:
                    await send_user_message(bot, chat_id, plain_text, **degraded_kwargs)
            except Exception:
                pass
    except (NetworkError, TimedOut) as ex:
        logging.warning(f"Network error sending to {chat_id}: {ex}")
        raise
    except Exception as ex:
        logging.error(f"send_media failed: {ex}")
        try:
            plain_text = (text or "").strip()
            if plain_text:
                await send_user_message(bot, chat_id, plain_text, **kwargs)
        except Exception:
            pass


# ================= SAFE COPY MESSAGE =================
@retry_async(max_retries=2, delay=0.5, backoff=1.5)
async def safe_copy_message(bot, chat_id: int, from_chat_id: int, message_id: int,
                             fallback_text: str = None) -> Optional[Any]:
    try:
        return await bot.copy_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id)
    except BadRequest as ex:
        err = str(ex)
        if "Document_invalid" in err or "document_invalid" in err.lower() or "DOCUMENT_INVALID" in err:
            try:
                return await bot.forward_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id)
            except Exception as fwd_ex:
                logging.warning(f"forward_message also failed: {fwd_ex}")
                if fallback_text:
                    try:
                        return await bot.send_message(chat_id=chat_id, text=fallback_text,
                                                       parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                    except Exception:
                        pass
                return None
        # Other permanent BadRequest errors - don't retry, just log and give up gracefully.
        logging.warning(f"safe_copy_message BadRequest (not retrying): {ex}")
        if fallback_text:
            try:
                return await bot.send_message(chat_id=chat_id, text=fallback_text,
                                               parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except Exception:
                pass
        return None


# ================= ADD USERBOT WIZARD (login finish) =================
async def _finish_userbot_login(wz: dict, context, msg, session_str: str):
    """Validate the user session before saving it and enabling outbound DMs."""
    try:
        if not session_str or not isinstance(session_str, str):
            raise ValueError("Empty user-account session")
        lc = wz.get("client")
        if lc is None:
            lc = await user_account.get_client(session_str)
        else:
            try:
                connected = lc.is_connected()
            except Exception:
                connected = False
            if not connected:
                lc = await user_account.get_client(session_str)
        if lc is None:
            raise ValueError("User-account session is invalid/expired or could not connect")
        if not await lc.is_user_authorized():
            raise ValueError("User-account session is not authorized")
        me = await lc.get_me()
        if me is None:
            raise ValueError("Could not verify the logged-in user account")
        bot_id = db.add_user_bot(wz["user_id"], wz["bot_token"], wz["bot_username"],
                                 tg_bot_id=wz.get("tg_bot_id"))
        db.set_user_session(bot_id, session_str)
        user_account.attach_client(session_str, lc, bot_id, wz.get("tg_bot_id"))
        context.user_data.pop("ubwiz", None)
        acct = f"<b>{getattr(me, 'first_name', 'User')}</b>"
        uname = getattr(me, "username", None)
        acct += f" (@{uname})" if uname else ""
        await reply_premium_message(msg,
            f"<blockquote>{pp('✅')} <b>USER ACCOUNT LOGIN SUCCESS</b></blockquote>\n\n"
            f"{pp('👤')} Account: {acct}\n"
            f"{pp('🤖')} Bot: @{wz['bot_username']}\n"
            f"{pp('🆔')} Bot ID: <code>{bot_id}</code>\n\n"
            f"{pp('⭐️')} Ab <b>subscription add</b> karo - user account chalu ho jayega:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [btn("Add Subscription", "admin_add_sub", "success", "⭐️")],
                [btn("Admin Panel", "admin_panel", "primary", "🔙")],
            ]))
    except Exception as ex:
        logging.error(f"_finish_userbot_login error: {ex}")
        await reply_premium_message(msg, f"{pe('❌')} Save error: {ex}", parse_mode=ParseMode.HTML)


# ================= USER-ACCOUNT DELIVERY =================
# End-user DMs sirf user_sender.py ke logged-in user account se jaate hain.
# User account fail/offline ho to delivery fail hoti hai; Bot API fallback nahi hai.
async def deliver_text(user_id: int, text: str, bot, reply_markup=None,
                       parse_mode=ParseMode.HTML, use_user_account: bool = True) -> Optional[int]:
    if not use_user_account:
        return None
    if not (text or "").strip():
        return None
    try:
        if not user_account.available(bot=bot):
            logging.warning("User-account unavailable for %s; Bot API fallback disabled", user_id)
            return None
        mid = await user_account.send_text(user_id, text, reply_markup, bot=bot)
        if mid:
            logging.info("user-account text delivered to %s (message_id=%s)", user_id, mid)
        return mid
    except Exception as ex:
        logging.error("user-account text failed for %s; Bot API fallback disabled: %s", user_id, ex)
        return None


async def deliver_media(user_id: int, media_id, media_type, text: str, bot, markup=None,
                        entities_json: Optional[str] = None, file_name: Optional[str] = None,
                        mime_type: Optional[str] = None, use_user_account: bool = True) -> bool:
    if not use_user_account:
        return False
    is_media = bool(media_id) and media_type not in (None, "text")
    if not is_media and not (text or "").strip():
        return False
    try:
        if not user_account.available(bot=bot):
            logging.warning("User-account unavailable for %s; Bot API fallback disabled", user_id)
            return False
        if is_media:
            caption = MessageManager.prepare_for_sending(text, entities_json) if text else ""
            mid = await user_account.send_media(
                user_id, media_id, media_type, caption or "", markup,
                file_name, mime_type, bot=bot,
            )
        else:
            display_text = MessageManager.prepare_for_sending(text, entities_json)
            mid = await user_account.send_text(user_id, display_text, markup, bot=bot)
        if mid:
            logging.info("user-account media/text delivered to %s (message_id=%s)", user_id, mid)
        return bool(mid)
    except Exception as ex:
        logging.error("user-account media failed for %s; Bot API fallback disabled: %s", user_id, ex)
        return False


# ================= SUBSCRIPTION JOBS =================
async def check_expired_subscriptions_job(context: ContextTypes.DEFAULT_TYPE):
    expired_bots = db.get_expired_subscriptions()
    for bot_id in expired_bots:
        bot_data = db.get_user_bot(bot_id)
        if bot_data and bot_data["is_active"] == 1:
            if bot_id in user_bot_applications:
                try:
                    app = user_bot_applications[bot_id]
                    await app.updater.stop()
                    await app.stop()
                    await app.shutdown()
                except Exception:
                    pass
                user_bot_applications.pop(bot_id, None)
            db.set_user_bot_active(bot_id, False)
            await send_premium_message(context.bot, bot_data["user_id"], UIFormatter.subscription_expired(),
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[
                    btn("Renew Now", f"https://t.me/{ADMIN_USERNAME.lstrip('@')}", "danger", "💰")
                ]]))


async def subscription_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    _cleanup_support_maps()
    for days_threshold in [3, 1]:
        expiring = db.get_expiring_subscriptions(days_threshold)
        for sub in expiring:
            bot_id = sub["bot_id"]
            sub_type = sub["subscription_type"]
            bot_data = db.get_user_bot(bot_id)
            if not bot_data:
                continue
            try:
                expiry_dt = sub["expiry_date"]
                if isinstance(expiry_dt, str):
                    expiry_dt = datetime.fromisoformat(expiry_dt.replace('+00:00', ''))
                expiry_dt = make_aware(expiry_dt)
                days_left = (expiry_dt - now_aware()).days
                if days_threshold == 3:
                    msg_text = UIFormatter.expiry_reminder_3d(sub_type, expiry_dt, days_left)
                else:
                    msg_text = UIFormatter.expiry_reminder_1d(sub_type, expiry_dt)
                await send_premium_message(context.bot, bot_data["user_id"], msg_text, parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        btn("Renew", f"https://t.me/{ADMIN_USERNAME.lstrip('@')}", "danger", "💰")
                    ]]))
                db.mark_reminder_sent(bot_id, days_threshold)
            except Exception as ex:
                logging.error(f"Reminder error: {ex}")


# ================= MESSAGE SENDING =================
async def _send_messages_with_media_groups(chat_id: int, msgs: List[dict], context: ContextTypes.DEFAULT_TYPE,
                                            bot_id: str = None, attach_start_button: bool = True, placeholder_user=None) -> bool:
    """Deliver a configured welcome sequence only through its user account."""
    live_chat_markup = None
    if bot_id and attach_start_button:
        try:
            bot_data = db.get_user_bot(bot_id)
            if bot_data and bot_data.get("bot_username"):
                live_chat_url = f"https://t.me/{bot_data['bot_username']}?start=live_chat"
                live_chat_markup = InlineKeyboardMarkup([[btn_url("Live Chat Support", live_chat_url, "success", "💬")]])
        except Exception:
            pass

    any_sent = False
    i = 0
    while i < len(msgs):
        row = msgs[i]
        text = render_dynamic_text(row.get("content_text", ""), placeholder_user)
        media_id = row.get("media_id")
        media_type = row.get("media_type")
        media_group_id = row.get("media_group_id")
        buttons_json = row.get("buttons_json")
        entities_json = row.get("entities_json")
        file_name = row.get("file_name")
        mime_type = row.get("mime_type")

        if media_group_id:
            group_rows = []
            extra_captions = []
            group_buttons_json = None
            group_caption_text = None
            j = i
            while j < len(msgs) and msgs[j].get("media_group_id") == media_group_id:
                group_row = msgs[j]
                group_text = render_dynamic_text(group_row.get("content_text", ""), placeholder_user)
                group_media_id = group_row.get("media_id")
                group_media_type = group_row.get("media_type")
                if not group_buttons_json and group_row.get("buttons_json"):
                    group_buttons_json = group_row.get("buttons_json")
                if group_media_id and group_media_type in ("photo", "video", "document", "audio"):
                    display_text = MessageManager.prepare_for_sending(
                        group_text, group_row.get("entities_json")) if group_text else None
                    if display_text:
                        if not group_caption_text:
                            group_caption_text = display_text
                        else:
                            extra_captions.append(display_text)
                    group_rows.append({
                        "media_id": group_media_id,
                        "media_type": group_media_type,
                        "file_name": group_row.get("file_name"),
                    })
                j += 1

            delivered = False
            if group_rows and user_account.available(bot=context.bot):
                try:
                    message_ids = await user_account.send_media_group(
                        chat_id, group_rows, context.bot,
                        caption=group_caption_text,
                        markup=buttons_to_markup(group_buttons_json))
                    delivered = bool(message_ids)
                except Exception as ex:
                    logging.warning("User-account media group failed for %s: %s", chat_id, ex)
            if group_rows and not delivered:
                logging.warning("Album for %s was not sent; Bot API fallback is disabled", chat_id)
            any_sent = any_sent or delivered

            if delivered:
                for caption in extra_captions:
                    sent_id = await deliver_text(chat_id, strip_premium_emojis(caption), context.bot)
                    any_sent = bool(sent_id) or any_sent
                group_markup = buttons_to_markup(group_buttons_json)
                if group_markup:
                    sent_id = await deliver_text(
                        chat_id, strip_premium_emojis(group_caption_text) or "Open links:",
                        context.bot, reply_markup=group_markup)
                    any_sent = bool(sent_id) or any_sent
            i = j
            continue

        markup = buttons_to_markup(buttons_json)
        if i == len(msgs) - 1 and live_chat_markup:
            if markup:
                combined_rows = markup.inline_keyboard + live_chat_markup.inline_keyboard
                markup = InlineKeyboardMarkup(combined_rows)
            else:
                markup = live_chat_markup
        sent = await deliver_media(
            chat_id, media_id, media_type, text or "", context.bot, markup,
            entities_json=entities_json, file_name=file_name, mime_type=mime_type)
        any_sent = bool(sent) or any_sent
        i += 1

    return any_sent


async def send_saved_welcome(bot_id: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE, user=None) -> bool:
    try:
        channels = db.get_bot_channels(bot_id) or []
        if not channels:
            return bool(await deliver_text(chat_id, render_dynamic_text(DEFAULT_WELCOME_MESSAGE, user), context.bot))
        channel_id = channels[0]["channel_id"]
        msgs = db.get_messages(channel_id, bot_id) or []
        if not msgs:
            return bool(await deliver_text(chat_id, render_dynamic_text(DEFAULT_WELCOME_MESSAGE, user), context.bot))
        return await _send_messages_with_media_groups(
            chat_id, msgs, context, bot_id=bot_id, placeholder_user=user)
    except Exception as ex:
        logging.error("send_saved_welcome failed for %s: %s", chat_id, ex)
        return False


def _runtime_store(context: ContextTypes.DEFAULT_TYPE, key: str) -> dict:
    if key not in context.user_data:
        context.user_data[key] = {}
    return context.user_data[key]


async def sync_pending_join_requests_for_channel(bot_id: str, channel_id: int, bot):
    try:
        if not hasattr(bot, 'get_chat_join_requests'):
            logging.info(f"get_chat_join_requests not available in this PTB version. Skipping sync.")
            return
        count = 0
        async for jr in bot.get_chat_join_requests(channel_id):
            db.add_join_request(bot_id, jr.from_user.id, channel_id, "pending")
            count += 1
        if count > 0:
            logging.info(f"Synced {count} pending join requests for channel {channel_id}")
    except Exception as ex:
        logging.warning(f"sync_pending_join_requests_for_channel: {ex}")


# ================= USER BOT START =================
async def user_bot_start(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    user = update.effective_user
    if not user:
        return
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    # FIX: previously this only checked is_admin(user.id), so the actual bot OWNER
    # (the client who bought/created this userbot) fell through to the regular
    # subscriber welcome flow instead of getting their bot management panel.
    # is_bot_owner() correctly covers "is admin OR is the owner of this bot_id".
    if is_bot_owner(bot_id, user.id):
        bot_data = db.get_user_bot(bot_id)
        bot_username = bot_data.get("bot_username") if bot_data else None
        title = f"@{bot_username}" if bot_username else bot_id
        await send_premium_message(context.bot, user.id,
            f"<blockquote>{pp('🤖')} <b>MANAGE BOT</b></blockquote>\n\nBot: {title}\nBot ID: {bot_id}",
            parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, user.id))
        return
    start_param = context.args[0] if context.args else ""
    if start_param == "live_chat":
        await send_premium_message(context.bot, user.id, UIFormatter.live_chat_header(), parse_mode=ParseMode.HTML)
        return
    await send_saved_welcome(bot_id, user.id, context, user=user)


async def handle_public_userbot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str):
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except Exception:
        pass
    data = q.data
    user = q.from_user
    if data == "live_chat_support":
        await send_premium_message(context.bot, user.id, UIFormatter.live_chat_header(), parse_mode=ParseMode.HTML)


async def user_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    q = update.callback_query
    if not q or not q.from_user:
        return
    try:
        await q.answer()
    except Exception:
        pass
    uid = q.from_user.id
    data = q.data

    # Check if user is owner OR admin
    if not is_bot_owner(bot_id, uid):
        await safe_edit_message_text(q, f"{pe('❌')} You don't have permission to manage this bot.", parse_mode=ParseMode.HTML)
        return

    if data == "main_menu":
        user = q.from_user
        await safe_edit_message_text(q, UIFormatter.main_menu(user.first_name), parse_mode=ParseMode.HTML,
            reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"back_to_manage_{bot_id}" or data == f"manage_bot_{bot_id}":
        await safe_edit_message_text(q, f"<blockquote>{pp('🤖')} <b>MANAGE BOT</b></blockquote>", parse_mode=ParseMode.HTML,
            reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"ub_subscription_{bot_id}":
        sub = db.get_subscription_for_bot(bot_id)
        if sub:
            try:
                expiry = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
                days = (expiry - now_aware()).days
                await safe_edit_message_text(q, UIFormatter.subscription_details(sub["subscription_type"], expiry, days, sub["max_channels"]),
                    parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
            except Exception as ex:
                await safe_edit_message_text(q, f"Error: {ex}", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"ub_stats_{bot_id}":
        channels = db.get_bot_channels(bot_id) or []
        total = db.get_total_requesters_count(bot_id)
        reachable = db.get_reachable_requesters_count(bot_id)
        pending = db.get_pending_count(bot_id)
        await safe_edit_message_text(q, UIFormatter.bot_stats(len(channels), total, reachable, pending),
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        return

    if data == f"ub_add_channel_{bot_id}":
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        ud["adding_channel"] = True
        context.user_data[f"adding_channel_{bot_id}"] = True
        await safe_edit_message_text(q,
            f"<blockquote>{pp('✈️')} <b>ADD CHANNEL</b></blockquote>\n\n"
            "1. Add this bot as admin in your channel\n"
            "2. Forward any message from that channel here\n\n"
            "<i>The bot will auto-detect the channel.</i>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Cancel", f"manage_bot_{bot_id}", "danger", "❌")]]))
        return

    if data == f"ub_set_message_{bot_id}":
        channels = db.get_bot_channels(bot_id)
        if not channels:
            await safe_edit_message_text(q, f"{pe('❌')} Add a channel first.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        ud["setting_message"] = True
        context.user_data[f"setting_message_{bot_id}"] = True
        await safe_edit_message_text(q,
            f"<blockquote>{pp('📝')} <b>SET MESSAGES</b></blockquote>\n\n"
            "Send messages one by one.\n\n"
            "Supported: text, photo, video, document, audio, sticker, media albums\n\n"
            "Placeholders: <code>{{first_name}}</code> <code>{{username}}</code> <code>{{user_id}}</code>\n\n"
            "Type <b>done</b> when finished.",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Cancel", f"setmsg_cancel_{bot_id}", "danger", "❌")]]))
        return

    if data == f"ub_delete_messages_{bot_id}":
        channels = db.get_bot_channels(bot_id)
        if not channels:
            await safe_edit_message_text(q, f"{pe('❌')} No channels found.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return
        channel_id = channels[0]["channel_id"]
        db.clear_messages(bot_id, channel_id)
        await safe_edit_message_text(q, f"{pe('✅')} All messages deleted.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"ub_list_channels_{bot_id}":
        channels = db.get_bot_channels(bot_id)
        if not channels:
            await safe_edit_message_text(q, f"{pe('📋')} No channels added yet.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return
        lines = [f"<blockquote>{pp('📋')} <b>MY CHANNELS</b></blockquote>\n"]
        for ch in channels:
            lines.append(f"• {ch['channel_title']} (<code>{ch['channel_id']}</code>)")
        await safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        return

    if data == f"ub_remove_channel_{bot_id}":
        await prompt_remove_channel(q, bot_id, uid)
        return

    if data.startswith(f"removechan_{bot_id}_"):
        parts = data.split("_")
        channel_id = _extract_last_id(parts)
        if channel_id:
            db.remove_channel(bot_id, channel_id)
            await safe_edit_message_text(q, f"{pe('✅')} Channel removed.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"ub_toggle_auto_{bot_id}":
        channels = db.get_bot_channels(bot_id)
        if not channels:
            await safe_edit_message_text(q, f"{pe('❌')} No channels.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return
        kb = []
        for ch in channels:
            auto = int(ch.get("auto_approve", 0)) == 1
            status = f"{pe('🟢')} ON" if auto else f"{pe('🔴')} OFF"
            kb.append([btn(f"{ch['channel_title'][:20]} — Auto: {status}", f"toggleauto_{bot_id}_{ch['channel_id']}", "primary", "⚙️")])
        kb.append([btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")])
        await safe_edit_message_text(q, f"<blockquote>{pp('⚙️')} <b>AUTO-APPROVE SETTINGS</b></blockquote>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith(f"toggleauto_{bot_id}_"):
        parts = data.split("_")
        channel_id = _extract_last_id(parts)
        ch_data = db.get_channel_owner_data(channel_id, bot_id)
        if ch_data:
            current = int(ch_data.get("auto_approve", 0)) == 1
            db.set_auto_approve(bot_id, channel_id, not current)
            new_status = f"{pe('🟢')} ON" if not current else f"{pe('🔴')} OFF"
            await safe_edit_message_text(q, f"{pe('✅')} Auto-approve set to <b>{new_status}</b>",
                parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"ub_pending_requests_{bot_id}":
        pending = db.get_pending_count(bot_id)
        await safe_edit_message_text(q, f"<blockquote>{pp('📊')} <b>PENDING REQUESTS</b></blockquote>\n\n{pe('🔔')} Total pending: <b>{pending}</b>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        return

    if data == f"ub_accept_all_{bot_id}":
        await accept_all(q, bot_id, uid, context)
        return

    if data == f"ub_broadcast_{bot_id}":
        context.user_data[f"broadcast_stage_{bot_id}"] = "await_message"
        await safe_edit_message_text(q,
            f"<blockquote>{pp('✈️')} <b>BROADCAST</b></blockquote>\n\nSend message to broadcast to all your users:",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Cancel", f"manage_bot_{bot_id}", "danger", "❌")]]))
        return

    if data == f"bcast_add_btns_{bot_id}":
        context.user_data[f"broadcast_stage_{bot_id}"] = "await_buttons"
        await safe_edit_message_text(q,
            f"{pe('🔘')} Send inline buttons (Text|https://link per line):",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        return

    if data == f"bcast_send_{bot_id}":
        await preview_user_broadcast(q, context, bot_id, uid)
        return

    if data == f"bcast_confirm_{bot_id}":
        await send_user_broadcast(q, context, bot_id, uid)
        return

    if data.startswith(f"ub_manage_messages_{bot_id}"):
        channels = db.get_bot_channels(bot_id)
        if not channels:
            await safe_edit_message_text(q, f"{pe('❌')} No channels.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return
        channel_id = channels[0]["channel_id"]
        msgs = db.get_messages(channel_id, bot_id)
        if not msgs:
            await safe_edit_message_text(q, f"{pe('📭')} No messages saved.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return
        kb = []
        for i, m in enumerate(msgs):
            preview = (m.get("content_text") or m.get("media_type") or "Message")[:30]
            kb.append([btn(f"#{i+1} {preview}", f"ubmm_{bot_id}_{channel_id}_{m['id']}", "primary", "📝")])
        kb.append([btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")])
        await safe_edit_message_text(q, f"<blockquote>{pp('👀')} <b>YOUR MESSAGES</b></blockquote>\n\nSelect a message to edit:",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith(f"ubmm_{bot_id}_"):
        parts = data.split("_")
        msg_id = _extract_last_id(parts)
        row = db.get_message_by_id(msg_id)
        if not row:
            await safe_edit_message_text(q, f"{pe('❌')} Message not found.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return
        channel_id = row["channel_id"]
        preview = (row.get("content_text") or "")[:200]
        media_info = f"\nMedia: {row.get('media_type')}" if row.get('media_type') else ""
        btns_info = "\nHas buttons: ✅" if row.get("buttons_json") else ""
        await safe_edit_message_text(q,
            f"<blockquote>{pp('📝')} <b>EDIT MESSAGE #{msg_id}</b></blockquote>\n\n"
            f"<b>Preview:</b>\n{EmojiManager._html_escape(preview)}{media_info}{btns_info}",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [btn("Edit Text", f"ubm_edtext_{bot_id}_{msg_id}", "primary", "📝"),
                 btn("Edit Media", f"ubm_edmed_{bot_id}_{msg_id}", "primary", "🖼️")],
                [btn("Edit Buttons", f"ubm_edbtn_{bot_id}_{msg_id}", "primary", "🔘"),
                 btn("Delete", f"delmsg_{bot_id}_{msg_id}", "danger", "🗑")],
                [btn("Back", f"ub_manage_messages_{bot_id}", "primary", "🔙")],
            ]))
        return

    if data.startswith(f"ubm_edtext_{bot_id}_"):
        parts = data.split("_")
        msg_id = _extract_last_id(parts)
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        ud["editing_text_msg_id"] = msg_id
        await safe_edit_message_text(q, f"{pe('📝')} Send the new text:", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Cancel", f"ub_manage_messages_{bot_id}", "danger", "❌")]]))
        return

    if data.startswith(f"ubm_edmed_{bot_id}_"):
        parts = data.split("_")
        msg_id = _extract_last_id(parts)
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        ud["editing_media_msg_id"] = msg_id
        await safe_edit_message_text(q, f"{pe('🖼️')} Send the new media (photo/video/document):", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Cancel", f"ub_manage_messages_{bot_id}", "danger", "❌")]]))
        return

    if data.startswith(f"ubm_edbtn_{bot_id}_"):
        parts = data.split("_")
        msg_id = _extract_last_id(parts)
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        ud["editing_buttons_msg_id"] = msg_id
        await safe_edit_message_text(q,
            f"{pe('🔘')} <b>Send Inline Buttons</b>\n\n"
            "• 1 button per row:\n  <code>Button Label|https://link</code>\n\n"
            "• 2 buttons per row:\n  <code>Label One|https://link1 || Label Two|https://link2</code>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Cancel", f"ub_manage_messages_{bot_id}", "danger", "❌")]]))
        return

    if data.startswith(f"delmsg_{bot_id}_"):
        parts = data.split("_")
        msg_id = _extract_last_id(parts)
        db.delete_message(msg_id)
        await safe_edit_message_text(q, f"{pe('✅')} Message deleted.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"setmsg_more_{bot_id}":
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        ud["setting_message"] = True
        context.user_data[f"setting_message_{bot_id}"] = True
        await safe_edit_message_text(q, f"{pe('📝')} Send the next message:", parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("Done", f"setmsg_done_{bot_id}", "success", "✅"),
                                                btn("Cancel", f"setmsg_cancel_{bot_id}", "danger", "❌")]]))
        return

    if data == f"setmsg_done_{bot_id}":
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        for key in ["setting_message", "messages", "pending_buttons", "waiting_buttons"]:
            ud.pop(key, None)
            context.user_data.pop(f"{key}_{bot_id}", None)
        await safe_edit_message_text(q, f"{pe('✅')} Messages saved successfully!", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
        return

    if data == f"setmsg_cancel_{bot_id}":
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        for key in ["setting_message", "messages", "pending_buttons", "waiting_buttons"]:
            ud.pop(key, None)
            context.user_data.pop(f"{key}_{bot_id}", None)
        await safe_edit_message_text(q, f"{pe('❌')} Cancelled.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
        return

    if data.startswith(f"setbtn_addmore_{bot_id}_"):
        parts = data.split("_")
        msg_id = _extract_last_id(parts)
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        ud["waiting_buttons"] = {"msg_id": msg_id, "append": True}
        await safe_edit_message_text(q,
            f"{pe('🔘')} Send more inline buttons to append:\n\n"
            "<code>Button Label|https://link</code>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        return

    if data.startswith(f"setbtng_addmore_{bot_id}"):
        ud = _runtime_store(context, f"{uid}_{bot_id}")
        grp = ud.get("pending_buttons_group_addmore")
        if grp:
            ud["waiting_buttons"] = {"msg_ids": grp.get("msg_ids"), "append": True}
        await safe_edit_message_text(q,
            f"{pe('🔘')} Send more buttons to append:",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        return


async def accept_all(q, bot_id: str, owner_id: int, context):
    try:
        channels = db.get_bot_channels(bot_id) or []
        for ch in channels:
            await sync_pending_join_requests_for_channel(bot_id, ch["channel_id"], context.bot)
    except Exception:
        pass
    pending = db.get_pending_requests(bot_id)
    if not pending:
        await safe_edit_message_text(q, f"{pe('‼️')} No pending requests.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
        return
    ok = 0
    cleaned = 0
    for req in pending:
        try:
            await context.bot.approve_chat_join_request(req["channel_id"], req["requester_id"])
            db.mark_request_status(req["id"], 'approved')
            ok += 1
        except Exception as ex:
            if 'User_already_participant' in str(ex):
                db.mark_request_status(req["id"], 'approved')
                cleaned += 1
    await safe_edit_message_text(q, f"<blockquote>{pp('✅')} <b>ACCEPT ALL COMPLETE</b></blockquote>\n\n{pe('✅')} Accepted: {ok}\n{pe('🧹')} Already approved: {cleaned}",
        parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))


async def prompt_remove_channel(q, bot_id: str, owner_id: int):
    channels = db.get_bot_channels(bot_id)
    if not channels:
        await safe_edit_message_text(q, f"{pe('‼️')} No channels to remove.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
        return
    kb = [[btn(f"{pe('❌')} {ch['channel_title'][:25]}", f"removechan_{bot_id}_{ch['channel_id']}", "danger", "❌")] for ch in channels]
    kb.append([btn(f"{pe('🔙')} Back", f"manage_bot_{bot_id}", "primary", "🔙")])
    await safe_edit_message_text(q, "Select channel to remove:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _flush_media_group(bot_id: str, actor_uid: int, managed_uid: int, chat_id, context, media_group_id):
    if not bot_id or not actor_uid or not media_group_id:
        return
    ud = _runtime_store(context, f"{actor_uid}_{bot_id}")
    key = f"mg_{media_group_id}"
    items = ud.get(key, [])
    if not items:
        return
    channels = db.get_bot_channels(bot_id)
    if not channels:
        return
    channel_id = channels[0]["channel_id"]
    saved_ids = []
    for it in items:
        msg_id = db.add_message(bot_id, channel_id, it.get("text", ""), it.get("media_id"), it.get("media_type"),
                                media_group_id, it.get("entities_json"), it.get("file_name"), it.get("mime_type"),
                                it.get("telegram_message_id"))
        if msg_id and it.get("emoji_map"):
            db.save_user_emoji_map(bot_id, msg_id, it["emoji_map"])
        saved_ids.append(msg_id)
    ud.pop(key, None)
    ud["pending_buttons_group"] = {"msg_ids": saved_ids}
    await send_premium_message(context.bot, chat_id, f"{pe('✅')} Media group saved. Choose an option:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
        [btn("Set Inline Button", f"setbtng_{bot_id}", "primary", "🔘")],
        [btn("Set More Messages", f"setmsg_more_{bot_id}", "success", "➕")],
        [btn("Cancel", f"setmsg_cancel_{bot_id}", "danger", "❌")],
        [btn("Done", f"setmsg_done_{bot_id}", "success", "✅")],
    ]))


async def _flush_media_group_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    await _flush_media_group(data.get("bot_id"), data.get("actor_uid"), data.get("managed_uid"),
                             data.get("chat_id"), context, data.get("media_group_id"))


async def handle_user_bot_message(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    user = update.effective_user
    if not user:
        return
    uid = user.id
    msg = update.message
    if not msg:
        return

    # Handle support reply from admin to user
    if msg.reply_to_message and (is_admin(uid) or uid == owner_id):
        # Check both maps for the reply
        key1 = f"{bot_id}:{uid}:{msg.reply_to_message.message_id}"
        target_uid = _get_support_uid(USERBOT_SUPPORT_REPLY_MAP, key1)
        if not target_uid:
            # Try main bot support map
            target_uid = _get_support_uid(SUPPORT_REPLY_MAP, msg.reply_to_message.message_id)

        if target_uid:
            try:
                await context.bot.copy_message(chat_id=target_uid, from_chat_id=msg.chat_id, message_id=msg.message_id)
                await send_ephemeral_reply(msg, f"{pe('✅')} Reply delivered to user {target_uid}", 2)
            except Exception as ex:
                await reply_premium_message(msg, f"{pe('❌')} Reply failed: {ex}", parse_mode=ParseMode.HTML)
            return

    # Handle user message (forward to admin)
    # Only forward if user is NOT the bot owner and NOT admin
    if uid != owner_id and not is_admin(uid):
        try:
            # Auto-start bot if needed
            if bot_id not in user_bot_applications:
                bot_data = db.get_user_bot(bot_id)
                if bot_data:
                    sub = db.get_subscription_for_bot(bot_id)
                    if sub:
                        try:
                            expiry = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
                            if isinstance(expiry, str):
                                expiry = datetime.fromisoformat(expiry.replace('+00:00', ''))
                                expiry = make_aware(expiry)
                            if expiry > now_aware():
                                await start_user_bot(bot_data["bot_token"], bot_id, bot_data["user_id"])
                                db.set_user_bot_active(bot_id, True)
                                logging.info(f"Auto-started userbot {bot_id} for incoming message")
                        except Exception as ex:
                            logging.error(f"Auto-start failed for message: {ex}")

            user_name = user.first_name or "N/A"
            user_username = user.username or "N/A"
            user_id_val = user.id

            # Send to owner AND all admins
            admin_list = list(ADMIN_USER_IDS) + [owner_id]

            if msg.text:
                support_text = format_support_msg(user_name, user_username, user_id_val, msg.text, clickable=True)
                for aid in set(admin_list):
                    try:
                        r = await context.bot.send_message(chat_id=aid, text=support_text,
                                                            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                        _store_support_map(USERBOT_SUPPORT_REPLY_MAP, f"{bot_id}:{aid}:{r.message_id}", uid)
                    except Exception as ex:
                        logging.error(f"Failed to send text to admin {aid}: {ex}")
            else:
                # Non-text message
                header_text = format_support_msg(user_name, user_username, user_id_val, clickable=True)
                media_type_label = getattr(msg, 'content_type', 'media').upper()
                fallback_notice = (
                    f"{header_text}\n\n"
                    f"<i>⚠️ User sent a {media_type_label} — could not forward due to content protection.</i>"
                )
                for aid in set(admin_list):
                    try:
                        await context.bot.send_message(chat_id=aid, text=header_text,
                                                        parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                        r = await safe_copy_message(
                            context.bot, aid, msg.chat_id, msg.message_id,
                            fallback_text=fallback_notice
                        )
                        if r:
                            _store_support_map(USERBOT_SUPPORT_REPLY_MAP, f"{bot_id}:{aid}:{r.message_id}", uid)
                    except Exception as ex:
                        logging.error(f"Failed to send media to admin {aid}: {ex}")

            db.mark_reachable(bot_id, uid)
            await send_ephemeral_reply(msg, f"{pe('✅')} Message sent to support. You will receive a reply here.", 3)
        except Exception as ex:
            logging.error(f"Support message error: {ex}")
            await reply_premium_message(msg, f"{pe('⚠️')} Could not contact support right now. Please try again later.", parse_mode=ParseMode.HTML)
        return

    # Continue with existing message handling for owner/admin...
    ud = _runtime_store(context, f"{uid}_{bot_id}")
    extracted = MessageManager.extract_from_message(msg)

    if ud.get("editing_text_msg_id"):
        mid = ud.pop("editing_text_msg_id")
        row = db.get_message_by_id(mid)
        if row and row["bot_id"] == bot_id:
            db.update_message_text(mid, extracted["text"], extracted["entities_json"])
            if extracted["emoji_map"]:
                db.save_user_emoji_map(bot_id, mid, extracted["emoji_map"])
            await reply_premium_message(msg, f"{pe('✅')} Text updated.", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(f"{pe('🔙')} Back to Messages", f"ubmm_{bot_id}_{row['channel_id']}", "primary", "🔙")]]))
        else:
            await reply_premium_message(msg, f"{pe('❌')} Message not found. It may have been deleted.", parse_mode=ParseMode.HTML)
        return

    if ud.get("editing_buttons_msg_id"):
        mid = ud.pop("editing_buttons_msg_id")
        row = db.get_message_by_id(mid)
        if row and row["bot_id"] == bot_id:
            btn_json = buttons_json_from_text(msg.text or "")
            db.update_message_buttons(mid, btn_json or "[]")
            await reply_premium_message(msg, f"{pe('✅')} Buttons updated.", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(f"{pe('🔙')} Back to Messages", f"ubmm_{bot_id}_{row['channel_id']}", "primary", "🔙")]]))
        else:
            await reply_premium_message(msg, f"{pe('❌')} Message not found. It may have been deleted.", parse_mode=ParseMode.HTML)
        return

    if ud.get("editing_media_msg_id"):
        mid = ud.pop("editing_media_msg_id")
        row = db.get_message_by_id(mid)
        if row and row["bot_id"] == bot_id:
            db.update_message_media(mid, extracted["media_id"], extracted["media_type"], extracted["text"],
                                    extracted["entities_json"], extracted.get("file_name"), extracted.get("mime_type"),
                                    extracted.get("telegram_message_id"))
            if extracted["emoji_map"]:
                db.save_user_emoji_map(bot_id, mid, extracted["emoji_map"])
            await reply_premium_message(msg, f"{pe('✅')} Media updated.", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(f"{pe('🔙')} Back to Messages", f"ubmm_{bot_id}_{row['channel_id']}", "primary", "🔙")]]))
        else:
            await reply_premium_message(msg, f"{pe('❌')} Message not found. It may have been deleted.", parse_mode=ParseMode.HTML)
        return

    if ud.get("adding_channel") or context.user_data.get(f"adding_channel_{bot_id}"):
        channel_chat = None
        if hasattr(msg, 'forward_origin') and msg.forward_origin:
            try:
                if hasattr(msg.forward_origin, 'chat'):
                    channel_chat = msg.forward_origin.chat
            except:
                pass
        elif hasattr(msg, 'forward_from_chat') and msg.forward_from_chat:
            channel_chat = msg.forward_from_chat

        if channel_chat and channel_chat.type in ['channel', 'group', 'supergroup']:
            ch = channel_chat
            try:
                try:
                    member = await context.bot.get_chat_member(ch.id, context.bot.id)
                    if member.status not in ['administrator', 'creator']:
                        await reply_premium_message(msg, f"{pe('❌')} Bot is not an admin in this channel!\n\nPlease add bot as admin first, then try again.", parse_mode=ParseMode.HTML)
                        return
                except Exception as e:
                    await reply_premium_message(msg, f"{pe('❌')} Cannot verify bot admin status: {str(e)}\n\nMake sure bot is admin in the channel.", parse_mode=ParseMode.HTML)
                    return

                sub = db.get_subscription_for_bot(bot_id)
                if sub:
                    max_ch = sub.get("max_channels", 1)
                    current_ch = len(db.get_bot_channels(bot_id))
                    if current_ch >= max_ch:
                        await reply_premium_message(msg, f"{pe('❌')} Channel limit reached ({max_ch}). Upgrade to Pro for more channels.", parse_mode=ParseMode.HTML)
                        return

                db.add_channel(bot_id, ch.id, getattr(ch, 'username', None), ch.title or "Channel")
                await sync_pending_join_requests_for_channel(bot_id, ch.id, context.bot)

                ud["adding_channel"] = False
                context.user_data.pop(f"adding_channel_{bot_id}", None)

                await reply_premium_message(msg, f"{pe('✅')} Channel '{ch.title}' added successfully!\n{pe('✨')} Existing pending requests have been synced.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
            except Exception as ex:
                await reply_premium_message(msg, f"{pe('❌')} Error adding channel: {str(ex)}", parse_mode=ParseMode.HTML)
        else:
            await reply_premium_message(msg, f"{pe('🔽')} <b>How to add a channel:</b>\n\n1. Make sure this bot is <b>admin</b> in your channel\n2. Go to your channel\n3. <b>Forward ANY message</b> from that channel to this bot\n4. The channel will be added automatically\n\n⚠️ The message must be forwarded from the channel!", parse_mode=ParseMode.HTML)
        return

    if ud.get("waiting_buttons"):
        info = ud.get("waiting_buttons")
        msg_id = info.get("msg_id")
        msg_ids = info.get("msg_ids") or ([] if msg_id is None else [msg_id])
        append_mode = info.get("append", False)
        btn_json = buttons_json_from_text(msg.text or "")
        if btn_json:
            for _id in msg_ids:
                if append_mode:
                    db.append_message_buttons(_id, btn_json)
                else:
                    db.update_message_buttons(_id, btn_json)
            preview_markup = buttons_to_markup(btn_json)
            if preview_markup:
                try:
                    await reply_premium_message(msg, f"{pe('👁')} <b>Button Preview</b> — yahi dikhega users ko:", parse_mode=ParseMode.HTML, reply_markup=preview_markup)
                except Exception:
                    pass
            if len(msg_ids) == 1:
                more_btn_cb = f"setbtn_addmore_{bot_id}_{msg_ids[0]}"
            else:
                more_btn_cb = f"setbtng_addmore_{bot_id}"
                ud["pending_buttons_group_addmore"] = {"msg_ids": msg_ids}
            await reply_premium_message(msg, f"{pe('✅')} Inline buttons saved! Choose next action:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [btn("Set More Inline Buttons", more_btn_cb, "primary", "🔘")],
                [btn("Set More Messages", f"setmsg_more_{bot_id}", "success", "➕")],
                [btn("Done", f"setmsg_done_{bot_id}", "success", "✅")],
                [btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")],
            ]))
        else:
            await reply_premium_message(msg, f"{pe('❌')} No valid buttons parsed.\n\nFormat:\n• 1 button: <code>Button Label|https://link</code>\n• 2 per row: <code>Label One|https://link1 || Label Two|https://link2</code>", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
        ud.pop("waiting_buttons", None)
        ud.pop("pending_buttons_group", None)
        return

    if ud.get("setting_message") or context.user_data.get(f"setting_message_{bot_id}"):
        if msg.text and msg.text.strip().lower() in ["done", "/done", "finish", "stop", "complete"]:
            for key in ["setting_message", "messages", "pending_buttons", "waiting_buttons"]:
                ud.pop(key, None)
                context.user_data.pop(f"{key}_{bot_id}", None)
            await reply_premium_message(msg, f"{pe('✅')} Messages saved successfully.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
            return
        channels = db.get_bot_channels(bot_id)
        if not channels:
            await reply_premium_message(msg, f"{pe('❌')} No channel added yet. Add a channel first using 'Add Channel' button.", parse_mode=ParseMode.HTML)
            return
        channel_id = channels[0]["channel_id"]
        media_group_id = extracted["media_group_id"]

        if media_group_id:
            key = f"mg_{media_group_id}"
            arr = ud.get(key, [])
            first_item = len(arr) == 0
            arr.append({"text": extracted["text"], "media_id": extracted["media_id"], "media_type": extracted["media_type"],
                        "entities_json": extracted["entities_json"], "emoji_map": extracted["emoji_map"],
                        "file_name": extracted.get("file_name"), "mime_type": extracted.get("mime_type"),
                        "telegram_message_id": extracted.get("telegram_message_id")})
            ud[key] = arr
            if first_item:
                try:
                    await reply_premium_message(msg, f"{pe('📸')} Album received, processing...", parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            job_key = f"mg_job_{media_group_id}"
            old_job = ud.get(job_key)
            if old_job:
                try:
                    old_job.schedule_removal()
                except Exception:
                    pass
            j = context.job_queue.run_once(_flush_media_group_job, when=1.2,
                data={"bot_id": bot_id, "actor_uid": uid, "managed_uid": owner_id,
                      "chat_id": msg.chat_id, "media_group_id": media_group_id})
            ud[job_key] = j
            return

        msg_id = db.add_message(bot_id, channel_id, extracted["text"], extracted["media_id"],
                                extracted["media_type"], None, extracted["entities_json"],
                                extracted.get("file_name"), extracted.get("mime_type"),
                                extracted.get("telegram_message_id"))
        if msg_id and extracted["emoji_map"]:
            db.save_user_emoji_map(bot_id, msg_id, extracted["emoji_map"])
        ud["pending_buttons"] = {"msg_id": msg_id, "channel_id": channel_id}
        await reply_premium_message(msg, f"{pe('✅')} Message saved! Choose an option:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
            [btn("Set Inline Button", f"setbtn_{bot_id}_{msg_id}", "primary", "🔘")],
            [btn("Set More Messages", f"setmsg_more_{bot_id}", "success", "➕")],
            [btn("Cancel", f"setmsg_cancel_{bot_id}", "danger", "❌")],
            [btn("Done", f"setmsg_done_{bot_id}", "success", "✅")],
        ]))
        return

    if context.user_data.get(f"broadcast_stage_{bot_id}") == "await_message":
        draft = {"text": extracted["text"], "media": extracted["media_id"], "media_type": extracted["media_type"],
                 "emoji_map": extracted["emoji_map"], "entities_json": extracted["entities_json"],
                 "file_name": extracted.get("file_name"), "mime_type": extracted.get("mime_type")}
        context.user_data[f"broadcast_draft_{bot_id}"] = draft
        context.user_data[f"broadcast_stage_{bot_id}"] = "buttons_or_send"
        await reply_premium_message(msg, f"{pe('✅')} Broadcast draft saved. Add inline buttons or send now?", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
            [btn("Add Inline Buttons", f"bcast_add_btns_{bot_id}", "primary", "🔘")],
            [btn("Send Now", f"bcast_send_{bot_id}", "success", "🚀")],
            [btn("Cancel", f"manage_bot_{bot_id}", "danger", "❌")],
        ]))
        return

    if context.user_data.get(f"broadcast_stage_{bot_id}") == "await_buttons":
        draft = context.user_data.get(f"broadcast_draft_{bot_id}", {})
        btn_json = buttons_json_from_text(msg.text or "")
        if btn_json:
            draft["buttons_json"] = btn_json
            context.user_data[f"broadcast_draft_{bot_id}"] = draft
            preview_markup = buttons_to_markup(btn_json)
            if preview_markup:
                try:
                    await reply_premium_message(msg, f"{pe('👁')} <b>Button Preview</b> — yahi dikhega users ko:", parse_mode=ParseMode.HTML, reply_markup=preview_markup)
                except Exception:
                    pass
            await reply_premium_message(msg, f"{pe('✅')} Buttons saved. Ready to send?", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [btn("Send Now", f"bcast_send_{bot_id}", "success", "🚀")],
                [btn("Cancel", f"manage_bot_{bot_id}", "danger", "❌")],
            ]))
        else:
            await reply_premium_message(msg, f"{pe('❌')} No valid buttons.\n\nFormat:\n• 1 button: <code>Button Label|https://link</code>\n• 2 per row: <code>Label One|https://link1 || Label Two|https://link2</code>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        context.user_data[f"broadcast_stage_{bot_id}"] = "buttons_or_send"
        return

    await reply_premium_message(msg, f"{pe('🔽')} Use buttons to manage.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))


async def handle_set_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    q = update.callback_query
    if not q or not q.from_user:
        return
    try:
        await q.answer()
    except Exception:
        pass
    uid = q.from_user.id
    ud = _runtime_store(context, f"{uid}_{bot_id}")
    data = q.data

    if data == f"setbtng_{bot_id}":
        grp = ud.get("pending_buttons_group")
        if not grp or not grp.get("msg_ids"):
            await safe_edit_message_text(q, f"{pe('❌')} Group not found. Send media group again.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
            return
        ud["waiting_buttons"] = {"msg_ids": grp.get("msg_ids")}
        await safe_edit_message_text(q,
            f"{pe('🔘')} <b>Send Inline Buttons</b>\n\n"
            "• 1 button per row:\n  <code>Button Label|https://link</code>\n\n"
            "• 2 buttons per row:\n  <code>Label One|https://link1 || Label Two|https://link2</code>\n\n"
            "Emoji in button text is preserved exactly as you type.",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
        return

    if data.startswith(f"setbtn_{bot_id}_"):
        parts = data.split("_")
        msg_id = _extract_last_id(parts)
        ud["waiting_buttons"] = {"msg_id": msg_id}
        await safe_edit_message_text(q,
            f"{pe('🔘')} <b>Send Inline Buttons</b>\n\n"
            "• 1 button per row:\n  <code>Button Label|https://link</code>\n\n"
            "• 2 buttons per row:\n  <code>Label One|https://link1 || Label Two|https://link2</code>\n\n"
            "Emoji in button text is preserved exactly as you type.",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))


async def delete_pending_leave_recovery_messages(bot_id: str, user_id: int, target_channel_id: int, bot: Bot) -> int:
    deleted = 0
    for row_id, message_id in db.get_pending_leave_recovery_messages(bot_id, user_id, target_channel_id):
        try:
            # These DMs were sent by the user account. Do not try to manage them via Bot API.
            if not user_account.available(bot=bot):
                logging.warning("Cannot delete leave-recovery DM %s: matching user account is unavailable", message_id)
                continue
            ok = await user_account.delete_message(user_id, message_id, bot=bot)
            if ok:
                db.mark_leave_recovery_deleted(row_id)
                deleted += 1
            else:
                logging.warning("Leave-recovery DM %s was not deleted; it remains pending", message_id)
        except Exception as ex:
            logging.warning("Leave-recovery DM delete failed for %s: %s", message_id, ex)
    return deleted


async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    jr = update.chat_join_request
    if not jr:
        return
    requester = jr.from_user
    chat = jr.chat

    leave_cfg = db.get_leave_recovery_config()
    if (leave_cfg.get("enabled") and leave_cfg.get("target_channel_id")
            and int(leave_cfg["target_channel_id"]) == int(chat.id)):
        channel_configs = leave_cfg.get("channel_configs", {})
        chan_enabled = channel_configs.get(str(chat.id), True)
        if chan_enabled:
            await delete_pending_leave_recovery_messages(bot_id, requester.id, int(chat.id), context.bot)
        try:
            await jr.approve()
        except Exception as ex:
            if "User_already_participant" not in str(ex):
                logging.error("Leave recovery target approve error: %s", ex)
        return

    channel_row = db.get_channel_owner_data(chat.id, bot_id)
    if not channel_row:
        try:
            member = await context.bot.get_chat_member(chat.id, context.bot.id)
            if member.status in ["administrator", "creator"]:
                db.add_channel(bot_id, chat.id, getattr(chat, "username", None), chat.title or "Channel")
                channel_row = db.get_channel_owner_data(chat.id, bot_id)
        except Exception as ex:
            logging.warning("Could not verify managed channel %s: %s", chat.id, ex)
        if not channel_row:
            return

    # Resolve the Telegram entity with the same user account that will send the DM.
    # This is required because Bot API user ids do not include Telethon access_hashes.
    try:
        if not user_account.available(bot=context.bot):
            logging.error("Join request %s: no user-account sender is configured; no Bot API fallback", requester.id)
        else:
            await user_account.prime_requester(chat.id, requester.id, bot=context.bot)
    except Exception as ex:
        logging.warning("Could not prime user-account entity for %s: %s", requester.id, ex)

    auto = int(channel_row.get("auto_approve", 0)) == 1
    delivery_succeeded = False
    try:
        default_msg_text = render_dynamic_text(db.get_default_first_message(), requester)
        sent_id = await deliver_text(requester.id, default_msg_text, context.bot)
        delivery_succeeded = bool(sent_id)
    except Exception as ex:
        logging.error("Default first message send failed for %s: %s", requester.id, ex)

    try:
        msgs = db.get_messages(chat.id, bot_id) or []
        if msgs:
            welcome_sent = await _send_messages_with_media_groups(
                requester.id, msgs, context, bot_id=bot_id,
                attach_start_button=True, placeholder_user=requester)
        else:
            welcome_text = render_dynamic_text(
                channel_row.get("welcome_message") or DEFAULT_WELCOME_MESSAGE, requester)
            welcome_media_id = channel_row.get("welcome_media_id")
            welcome_media_type = channel_row.get("welcome_media_type")
            markup = buttons_to_markup(buttons_json_from_text(welcome_text) or None)
            if welcome_media_id and welcome_media_type:
                welcome_sent = await deliver_media(
                    requester.id, welcome_media_id, welcome_media_type,
                    welcome_text, context.bot, markup)
            elif welcome_text:
                welcome_sent = bool(await deliver_text(
                    requester.id, welcome_text, context.bot, reply_markup=markup))
            else:
                welcome_sent = False
        delivery_succeeded = bool(welcome_sent) or delivery_succeeded
    except Exception as ex:
        logging.error("Welcome delivery failed for %s: %s", requester.id, ex)

    if delivery_succeeded:
        db.mark_reachable(bot_id, requester.id)
    else:
        logging.error("Join request %s was recorded, but the user-account DM was not delivered", requester.id)

    db.add_join_request(bot_id, requester.id, chat.id, 'approved' if auto else 'pending')
    if auto:
        try:
            await jr.approve()
        except Exception as ex:
            if 'User_already_participant' not in str(ex):
                logging.error(f"Approve error: {ex}")


async def handle_channel_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    cmu = update.chat_member
    if not cmu or not cmu.chat or not cmu.new_chat_member:
        return
    new_status = getattr(cmu.new_chat_member, "status", "")
    old_status = getattr(cmu.old_chat_member, "status", "") if cmu.old_chat_member else ""

    if new_status in {"member", "administrator", "creator"} and old_status in {"left", "kicked", "restricted", ""}:
        leave_cfg = db.get_leave_recovery_config()
        target_channel_id = leave_cfg.get("target_channel_id")
        if leave_cfg.get("enabled") and target_channel_id and int(target_channel_id) == int(cmu.chat.id):
            joined_user = getattr(cmu.new_chat_member, "user", None)
            if joined_user and not getattr(joined_user, "is_bot", False):
                await delete_pending_leave_recovery_messages(bot_id, joined_user.id, int(cmu.chat.id), context.bot)
        return

    if new_status not in {"left", "kicked"} or old_status in {"left", "kicked"}:
        return

    channel_row = db.get_channel_owner_data(cmu.chat.id, bot_id)
    if not channel_row:
        return
    member_user = getattr(cmu.new_chat_member, "user", None)
    if not member_user or getattr(member_user, "is_bot", False):
        return

    db.mark_unreachable(bot_id, member_user.id)
    leave_cfg = db.get_leave_recovery_config()
    target_channel_id = leave_cfg.get("target_channel_id")
    target_link = (leave_cfg.get("target_channel_link") or "").strip()

    if not leave_cfg.get("enabled") or not target_channel_id or not target_link or int(target_channel_id) == int(cmu.chat.id):
        return

    channel_configs = leave_cfg.get("channel_configs", {})
    source_chan_key = str(cmu.chat.id)
    if not channel_configs.get(source_chan_key, True):
        logging.info(f"Leave recovery disabled for channel {cmu.chat.id}, skipping.")
        return

    try:
        await delete_pending_leave_recovery_messages(bot_id, member_user.id, int(target_channel_id), context.bot)
        extra = {
            "source_channel_title": cmu.chat.title or str(cmu.chat.id),
            "source_channel_id": cmu.chat.id,
            "target_channel_link": target_link,
            "target_channel_id": target_channel_id
        }

        leave_messages = leave_cfg.get("messages", [])

        if not leave_messages and leave_cfg.get("message"):
            leave_messages = [{"text": leave_cfg["message"], "buttons_json": leave_cfg.get("buttons_json", "")}]

        if not leave_messages:
            leave_messages = [{"text": "Hello {first_name}, aap channel se leave ho gaye. Wapas access ke liye neeche wale channel par request bheje.", "buttons_json": ""}]

        for lm in leave_messages:
            text = render_dynamic_text(lm.get("text", ""), member_user, extra)
            lm_buttons = lm.get("buttons_json") or ""
            if lm_buttons:
                leave_markup = buttons_to_markup(lm_buttons)
            else:
                leave_markup = InlineKeyboardMarkup([[btn_url("Join Channel", target_link, "success", "🔔")]])

            sent_mid = await deliver_text(member_user.id, text, context.bot, reply_markup=leave_markup)
            if sent_mid:
                db.add_leave_recovery_message(bot_id, member_user.id, cmu.chat.id, int(target_channel_id), sent_mid)

    except Exception as ex:
        logging.error(f"Leave recovery DM failed: {ex}")


# ================= USER BOT LIFECYCLE =================
async def start_user_bot(token: str, bot_id: str, owner_id: int):
    app = ApplicationBuilder().token(token).concurrent_updates(True).build()
    app.bot_data["bot_id"] = bot_id
    app.bot_data["owner_id"] = owner_id
    app.add_handler(CommandHandler("start", lambda u, c: user_bot_start(u, c, bot_id, owner_id)))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_public_userbot_callback(u, c, bot_id), pattern=r'^(start_now|live_chat_support)$'))
    app.add_handler(CallbackQueryHandler(lambda u, c: user_bot_callback(u, c, bot_id, owner_id), pattern=f"^(ub_|ubm_|ubmm_|delmsg_|setbtn_|setbtng|setmsg_|bcast_|removechan_|back_to_manage_|manage_bot_|toggleauto_|setbtn_addmore_|setbtng_addmore_).*{bot_id}|^main_menu$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: handle_set_buttons_callback(u, c, bot_id, owner_id), pattern=f"^setbtn_{bot_id}_"))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.Sticker.ALL, lambda u, c: handle_user_bot_message(u, c, bot_id, owner_id)))
    app.add_handler(ChatJoinRequestHandler(lambda u, c: handle_join_request(u, c, bot_id, owner_id)))
    app.add_handler(ChatMemberHandler(lambda u, c: handle_channel_member_update(u, c, bot_id, owner_id), ChatMemberHandler.CHAT_MEMBER))
    await app.initialize()
    bot_info = await app.bot.get_me()
    if getattr(bot_info, "id", None) is not None:
        db.set_user_bot_telegram_id(bot_id, bot_info.id)
        bot_record = db.get_user_bot(bot_id) or {}
        if bot_record.get("user_session"):
            user_account.register_session(bot_id, bot_record["user_session"], bot_info.id)
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query", "chat_member", "chat_join_request", "inline_query"])
    user_bot_applications[bot_id] = app
    try:
        for ch in db.get_bot_channels(bot_id) or []:
            await sync_pending_join_requests_for_channel(bot_id, ch["channel_id"], app.bot)
    except Exception as ex:
        logging.error(f"Startup pending sync failed: {ex}")
    return True


async def stop_user_bot(bot_id: str):
    if bot_id in user_bot_applications:
        try:
            app = user_bot_applications[bot_id]
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            pass
        user_bot_applications.pop(bot_id, None)
        db.set_user_bot_active(bot_id, False)


# ================= BROADCAST FUNCTIONS =================
async def preview_user_broadcast(q, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    draft = context.user_data.get(f"broadcast_draft_{bot_id}", {})
    if not draft:
        await safe_edit_message_text(q, f"{pe('❌')} No draft found.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
        return
    try:
        await send_media(context, owner_id, draft.get("media"), draft.get("media_type") or "text",
                         draft.get("text", ""), buttons_to_markup(draft.get("buttons_json")),
                         entities_json=draft.get("entities_json"), file_name=draft.get("file_name"), mime_type=draft.get("mime_type"))
    except Exception as ex:
        await safe_edit_message_text(q, f"{pe('❌')} Preview failed: {str(ex)}", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
        return
    await safe_edit_message_text(q, f"{pe('✅')} Preview sent above. Confirm to broadcast?", parse_mode=ParseMode.HTML, reply_markup=confirm_kb(f"bcast_confirm_{bot_id}", f"manage_bot_{bot_id}"))


async def send_user_broadcast(q, context: ContextTypes.DEFAULT_TYPE, bot_id: str, owner_id: int):
    draft = context.user_data.get(f"broadcast_draft_{bot_id}", {})
    context.user_data.pop(f"broadcast_stage_{bot_id}", None)
    if not draft:
        await safe_edit_message_text(q, f"{pe('❌')} No draft to send.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
        return
    reqs = db.get_requesters_for_bot(bot_id)
    if not reqs:
        await safe_edit_message_text(q, f"{pe('❌')} No users to broadcast to.", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
        return
    await safe_edit_message_text(q, f"{pe('✈️')} Broadcasting...", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", f"manage_bot_{bot_id}", "primary", "🔙")]]))
    sent = 0
    fail = 0
    for r in reqs:
        try:
            ok = await deliver_media(r, draft.get("media"), draft.get("media_type") or "text",
                                     draft.get("text", ""), context.bot, buttons_to_markup(draft.get("buttons_json")),
                                     entities_json=draft.get("entities_json"), file_name=draft.get("file_name"), mime_type=draft.get("mime_type"))
            if ok:
                db.mark_reachable(bot_id, r)
                sent += 1
            else:
                # A transient session/network error is not proof that the user is
                # unreachable; keep them eligible for the next user-account send.
                fail += 1
        except Forbidden:
            fail += 1
        except Exception as ex:
            logging.warning("User-account broadcast failed for %s: %s", r, ex)
            fail += 1
        if (sent + fail) % 30 == 0:
            try:
                await q.message.edit_text(f"{pe('✈️')} Broadcasting... Sent: {sent}, Failed: {fail}", parse_mode=ParseMode.HTML)
            except Exception:
                pass
    await safe_edit_message_text(q, UIFormatter.broadcast_confirm(sent, fail), parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, owner_id))
    context.user_data.pop(f"broadcast_draft_{bot_id}", None)


# ================= ADMIN PANEL FUNCTIONS =================
async def show_admin_userbot_control(q, context: ContextTypes.DEFAULT_TYPE):
    bots = db.get_all_user_bots()
    if not bots:
        await safe_edit_message_text(q, f"{pe('‼️')} No user bots found.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
        return

    running_count = sum(1 for bot in bots if bot["bot_id"] in user_bot_applications)
    premium_count = sum(1 for bot in bots if db.get_subscription_for_bot(bot["bot_id"]))
    stopped_count = max(len(bots) - running_count, 0)

    lines = [f"<blockquote>{pp('💎')} <b>USERBOT CONTROL CENTER</b></blockquote>", "",
             f"{pp('📊')} <b>Total Bots:</b> {len(bots)} | {pp('🟢')} <b>Running:</b> {running_count}",
             f"{pp('⭐️')} <b>Premium:</b> {premium_count} | {pp('🔴')} <b>Stopped:</b> {stopped_count}", "",
             f"{pp('📌')} <b>Bot List</b>"]

    for bot in bots:
        is_running = bot["bot_id"] in user_bot_applications
        sub = db.get_subscription_for_bot(bot["bot_id"])
        status_icon = pe('🟢') if is_running else pe('🔴')
        plan_text = sub["subscription_type"] if sub else "No active plan"
        plan_icon = pe('⭐️') if sub else pe('❌')
        lines.append(f"{status_icon} <b>@{bot['bot_username'] or 'N/A'}</b>\n   <code>{bot['bot_id']}</code> • {'Running' if is_running else 'Stopped'} • {plan_icon} {plan_text}")

    kb = []
    for bot in bots:
        is_running = bot["bot_id"] in user_bot_applications
        row = []
        if is_running:
            row.append(btn(f"Stop @{bot['bot_username'] or bot['bot_id']}", f"admin_ub_stop_{bot['bot_id']}", "danger", "🛑"))
        else:
            row.append(btn(f"Start @{bot['bot_username'] or bot['bot_id']}", f"admin_ub_start_{bot['bot_id']}", "success", "🚀"))
        row.append(btn("Info", f"admin_ub_info_{bot['bot_id']}", "primary", "📊"))
        kb.append(row)
    kb.append([btn("Start All", "admin_start_all", "success", "🚀"), btn("Stop All", "admin_stop_all", "danger", "🛑")])
    kb.append([btn("Refresh", "admin_userbots", "primary", "🔄"), btn("Back to Admin", "admin_panel", "primary", "🔙")])
    await safe_edit_message_text(q, "\n".join(lines)[:4000], parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def show_admin_ub_info(q, bot_id_target: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        bot_data = db.get_user_bot(bot_id_target)
        if not bot_data:
            await safe_edit_message_text(q, f"{pe('❌')} UserBot not found.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return
        sub = db.get_subscription_for_bot(bot_id_target)
        user_doc = db.get_user(bot_data["user_id"]) or {}
        is_running = bot_id_target in user_bot_applications
        lines = [f"<blockquote>{pp('🔎')} <b>USERBOT INFO</b></blockquote>\n"]
        lines.append(f"{pp('👤')} <b>User:</b> {user_doc.get('first_name', '')} @{user_doc.get('username', '') or 'N/A'} ({bot_data['user_id']})")
        lines.append(f"{pp('🤖')} <b>Bot:</b> @{bot_data['bot_username'] or 'N/A'}")
        lines.append(f"{pp('⚡️')} <b>Running:</b> {'🟢 Yes' if is_running else '🔴 No'}")
        if sub:
            try:
                exp = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
                days_left = (exp - now_aware()).days
                lines.append(f"{pp('⭐️')} <b>Plan:</b> {sub['subscription_type']}")
                lines.append(f"{pp('📅')} <b>Expiry:</b> {exp.strftime('%d %b %Y')} ({days_left}d left)")
            except Exception:
                lines.append(f"{pp('⭐️')} <b>Plan:</b> {sub['subscription_type'] if sub else 'None'}")
        else:
            lines.append(f"{pp('⭐️')} <b>Subscription:</b> None")
        channels = db.get_bot_channels(bot_id_target) or []
        total_users = db.get_total_requesters_count(bot_id_target)
        reachable = db.get_reachable_requesters_count(bot_id_target)
        lines.append(f"{pp('✈️')} <b>Channels:</b> {len(channels)}")
        lines.append(f"{pp('👥')} <b>Total Users:</b> {total_users} | <b>Reachable:</b> {reachable}")
        kb = []
        if is_running:
            kb.append([btn("Stop Bot", f"admin_ub_stop_{bot_id_target}", "danger", "🛑")])
        else:
            kb.append([btn("Start Bot", f"admin_ub_start_{bot_id_target}", "success", "🚀")])
        kb.append([btn("Remove Bot", f"admin_remove_bot_{bot_id_target}", "danger", "🗑"), btn("Back", "admin_userbots", "primary", "🔙")])
        await safe_edit_message_text(q, "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as ex:
        # Never let a data/formatting issue for one bot crash the whole admin panel.
        logging.error(f"show_admin_ub_info error for {bot_id_target}: {ex}")
        await safe_edit_message_text(q, f"{pe('❌')} Could not load info: {str(ex)[:150]}", parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def show_all_users(q):
    users = db.get_all_users()
    lines = [f"<blockquote>{pp('📇')} <b>ALL USERS ({len(users)})</b></blockquote>\n"]
    for u in users:
        lines.append(f"• {u['user_id']} @{u.get('username', 'N/A')} {u.get('first_name', '')}")
    await safe_edit_message_text(q, "\n".join(lines)[:4000], parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def check_expiry(q):
    subs = db.get_all_subscriptions()
    now = now_aware()
    expiring = []
    for s in subs:
        if s.get("expiry_date"):
            exp = make_aware(s["expiry_date"]) if isinstance(s["expiry_date"], datetime) else s["expiry_date"]
            if isinstance(exp, str):
                try:
                    exp = datetime.fromisoformat(exp.replace('+00:00', ''))
                    exp = make_aware(exp)
                except:
                    continue
            days = (exp - now).days
            if 0 <= days <= 7:
                expiring.append(f"• {s['bot_username']} — {days} days left")
    text = f"<blockquote>{pp('⏰')} <b>EXPIRING SOON</b></blockquote>\n\n" + ("\n".join(expiring) if expiring else "None expiring within 7 days.")
    await safe_edit_message_text(q, text, parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def show_stats(q):
    users = db.get_all_users()
    bots = db.get_all_user_bots()
    subs = db.get_all_subscriptions()
    running = len(user_bot_applications)
    userbot_counts = db.get_userbot_user_counts()
    total_userbot_users = sum(row["users"] for row in userbot_counts)
    count_lines = []
    for row in userbot_counts[:25]:
        status = pe('🟢') if row["bot_id"] in user_bot_applications else pe('🔴')
        count_lines.append(f"{status} <b>@{row['bot_username'] or 'N/A'}</b> — <code>{row['bot_id']}</code> — <b>{row['users']}</b> users")
    if len(userbot_counts) > 25:
        count_lines.append(f"<i>…and {len(userbot_counts) - 25} more userbots</i>")
    per_bot_text = "\n".join(count_lines) if count_lines else "No userbots found."
    text = (f"<blockquote>{pp('📊')} <b>SYSTEM STATS</b></blockquote>\n\n"
            f"{pp('👀')} <b>Main Bot Users:</b> {len(users)}\n"
            f"{pp('💎')} <b>Total UserBots:</b> {len(bots)}\n"
            f"{pp('🟢')} <b>Running UserBots:</b> {running}\n"
            f"{pp('⭐️')} <b>Active Subscriptions:</b> {len(subs)}\n"
            f"{pp('📌')} <b>Total UserBot Users:</b> {total_userbot_users}\n\n"
            f"<b>UserBot Wise Users</b>\n{per_bot_text}")
    await safe_edit_message_text(q, text[:4000], parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def show_admin_sub_list(q, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    subs = db.get_all_subscriptions()
    page_size = 10
    total_pages = max(1, (len(subs) + page_size - 1) // page_size)
    page_subs = subs[page * page_size:(page + 1) * page_size]
    lines = [f"<blockquote>{pp('📋')} <b>SUBSCRIPTION LIST</b> (Page {page+1}/{total_pages})</blockquote>\n"]
    for s in page_subs:
        exp = s["expiry_date"].strftime("%d %b %Y") if s["expiry_date"] else "N/A"
        lines.append(f"• {s['bot_id']} @{s['bot_username'] or 'N/A'} — {s['subscription_type']} — {exp} — {'🟢' if s['bot_active'] else '🔴'}")
    kb = pagination_kb(page+1, total_pages, "admin_sublist")
    kb.append([btn("Back to Admin", "admin_panel", "primary", "🔙")])
    await safe_edit_message_text(q, "\n".join(lines)[:4000], parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb) if kb else None)


async def start_all_userbots(q):
    bots = db.get_all_user_bots()
    started = 0
    failed = 0
    skipped = 0
    for bot in bots:
        bot_id = bot["bot_id"]
        if bot_id in user_bot_applications:
            skipped += 1
            continue
        sub = db.get_subscription_for_bot(bot_id)
        if not sub:
            continue
        try:
            exp = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
            if exp < now_aware():
                continue
        except Exception:
            continue
        try:
            await start_user_bot(bot["bot_token"], bot_id, bot["user_id"])
            db.set_user_bot_active(bot_id, True)
            started += 1
        except Exception:
            failed += 1
    await safe_edit_message_text(q, f"<blockquote>{pp('🚀')} <b>START ALL COMPLETE</b></blockquote>\n\n{pe('✅')} Started: {started}\n{pe('⏭️')} Already running: {skipped}\n{pe('❌')} Failed: {failed}", parse_mode=ParseMode.HTML, reply_markup=admin_kb())


# ================= LEAVE RECOVERY =================

def leave_recovery_status_text() -> str:
    cfg = db.get_leave_recovery_config()
    status = f"{pp('🟢')} ON" if cfg.get("enabled") else f"{pp('🔴')} OFF"
    target_id = cfg.get("target_channel_id") or "Not set"
    link = cfg.get("target_channel_link") or "Not set"
    messages = cfg.get("messages", [])
    msg_count = len(messages)

    channel_configs = cfg.get("channel_configs", {})
    chan_lines = []
    for cid, enabled in channel_configs.items():
        chan_lines.append(f"  • <code>{cid}</code> → {'🟢 ON' if enabled else '🔴 OFF'}")
    chan_text = "\n".join(chan_lines) if chan_lines else "  (Global setting applies to all)"

    msgs_preview = ""
    for i, m in enumerate(messages[:3]):
        preview = (m.get("text") or "")[:60]
        has_btns = "✅ buttons" if m.get("buttons_json") else "no buttons"
        msgs_preview += f"\n  #{i+1} {EmojiManager._html_escape(preview)}… [{has_btns}]"
    if msg_count > 3:
        msgs_preview += f"\n  …and {msg_count - 3} more"
    if not msgs_preview:
        msgs_preview = "\n  (No messages set)"

    return (
        f"<blockquote>{pp('🔔')} <b>LEAVE RECOVERY</b></blockquote>\n\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Target Channel ID:</b> <code>{target_id}</code>\n"
        f"<b>Target Link:</b> {EmojiManager._html_escape(str(link))}\n\n"
        f"<b>Messages ({msg_count}):</b>{msgs_preview}\n\n"
        f"<b>Per-Channel Config:</b>\n{chan_text}\n\n"
        f"<i>Global setting. All userbots use this config.</i>"
    )


def leave_recovery_kb() -> InlineKeyboardMarkup:
    cfg = db.get_leave_recovery_config()
    toggle_text = f"{pp('🛑')} Disable" if cfg.get("enabled") else f"{pp('✅')} Enable"
    toggle_style = "danger" if cfg.get("enabled") else "success"
    return InlineKeyboardMarkup([
        [btn(toggle_text, "admin_leave_toggle", toggle_style, "🔔")],
        [btn("Set Target Channel", "admin_leave_set_target", "primary", "🎯")],
        [btn("Manage Messages", "admin_leave_msgs", "primary", "💬")],
        [btn("Per-Channel Settings", "admin_leave_channels", "primary", "⚙️")],
        [btn("Clear Pending Records", "admin_leave_clear_pending", "danger", "🗑")],
        [btn("Back", "admin_panel", "primary", "🔙")],
    ])


def leave_recovery_msgs_kb() -> InlineKeyboardMarkup:
    cfg = db.get_leave_recovery_config()
    messages = cfg.get("messages", [])
    rows = []
    for i, m in enumerate(messages):
        preview = (m.get("text") or f"Message #{i+1}")[:20]
        has_btns = "🔘" if m.get("buttons_json") else "📄"
        rows.append([
            btn(f"{has_btns} #{i+1} {preview}", f"admin_leave_view_msg_{i}", "primary", "📝"),
            btn("🗑 Del", f"admin_leave_del_msg_{i}", "danger", "🗑"),
        ])
    rows.append([btn("➕ Add New Message", "admin_leave_add_msg", "success", "➕")])
    rows.append([btn("Back", "admin_leave_recovery", "primary", "🔙")])
    return InlineKeyboardMarkup(rows)


def leave_recovery_channels_kb() -> InlineKeyboardMarkup:
    cfg = db.get_leave_recovery_config()
    channel_configs = cfg.get("channel_configs", {})
    all_channels = {}
    for bot in db.get_all_user_bots():
        for ch in db.get_bot_channels(bot["bot_id"]):
            cid = str(ch["channel_id"])
            all_channels[cid] = ch.get("channel_title", cid)

    rows = []
    for cid, title in list(all_channels.items())[:20]:
        enabled = channel_configs.get(cid, True)
        status_icon = "🟢" if enabled else "🔴"
        rows.append([btn(f"{status_icon} {title[:25]}", f"admin_leave_chan_toggle_{cid}", "primary", "⚙️")])

    if not rows:
        rows.append([btn("No channels found", "admin_leave_channels", "primary", "❌")])

    rows.append([btn("Back", "admin_leave_recovery", "primary", "🔙")])
    return InlineKeyboardMarkup(rows)


async def show_leave_recovery_panel(q):
    await safe_edit_message_text(q, leave_recovery_status_text(), parse_mode=ParseMode.HTML, reply_markup=leave_recovery_kb())


# ================= MAIN CALLBACK HANDLER =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.from_user:
        return
    try:
        await q.answer()
    except Exception:
        pass
    uid = q.from_user.id
    data = q.data

    try:
        if data == "main_menu":
            user = q.from_user
            await safe_edit_message_text(q, UIFormatter.main_menu(user.first_name), parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(uid))
            for key in list(context.user_data.keys()):
                if key.startswith(("broadcast_stage_", "broadcast_draft_", "adding_channel_", "setting_message_")):
                    context.user_data.pop(key, None)
            return

        if data == "add_new_bot":
            await safe_edit_message_text(q, f"<blockquote>{pp('🔐')} <b>ADD YOUR BOT</b></blockquote>\n\nSend your BotFather API token to link your bot:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "main_menu", "primary", "🔙")]]))
            context.user_data["waiting_token"] = True
            return

        if data.startswith("manage_bot_"):
            bot_id = data.replace("manage_bot_", "")
            bot_data = db.get_user_bot(bot_id)
            if not bot_data:
                await safe_edit_message_text(q, f"{pe('❌')} Bot not found!", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(uid))
                return
            # Check if user is owner OR admin
            if bot_data.get("user_id") != uid and not is_admin(uid):
                await safe_edit_message_text(q, f"{pe('❌')} You don't have permission to manage this bot.", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(uid))
                return
            await safe_edit_message_text(q, f"<blockquote>{pp('🤖')} <b>MANAGE BOT</b></blockquote>\n\nBot: @{bot_data['bot_username']}\nBot ID: {bot_id}", parse_mode=ParseMode.HTML, reply_markup=bot_management_kb(bot_id, uid))
            return

        if data.startswith("sub_for_bot_"):
            bot_id = data.replace("sub_for_bot_", "")
            await safe_edit_message_text(q, UIFormatter.subscription_required(), parse_mode=ParseMode.HTML, reply_markup=subscription_plans_kb(bot_id))
            return

        if data.startswith("sub_basic_") or data.startswith("sub_pro_"):
            parts = data.split("_")
            bot_id = parts[2] if len(parts) > 2 and parts[2] not in ["basic", "pro"] else None
            plan = "Basic" if "basic" in data else "Pro"
            await safe_edit_message_text(q, f"<blockquote>{pp('💰') if plan == 'Basic' else pp('⚡️')} <b>{plan} PLAN SELECTED</b></blockquote>\n\n{'Rs2599/month — 1 channel' if plan == 'Basic' else 'Rs3999/month — 5 channels'}\n\n{pp('📞')} Contact {ADMIN_USERNAME} to complete payment.", parse_mode=ParseMode.HTML, reply_markup=subscription_plans_kb(bot_id))
            return

        if data == "admin_panel":
            if not is_admin(uid):
                await safe_edit_message_text(q, f"{pe('❌')} Not authorized", parse_mode=ParseMode.HTML)
                return
            await safe_edit_message_text(q, f"<blockquote>{pp('👑')} <b>ADMIN PANEL</b></blockquote>", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return

        if data == "admin_all_users":
            if not is_admin(uid):
                return
            await show_all_users(q)
            return

        if data == "admin_userbots":
            if not is_admin(uid):
                return
            await show_admin_userbot_control(q, context)
            return

        if data == "admin_add_userbot":
            if not is_admin(uid):
                return
            context.user_data["ubwiz"] = {"step": "ask_user_id"}
            await safe_edit_message_text(q,
                f"<blockquote>{pp('🚀')} <b>ADD USERBOT - STEP 1/4</b></blockquote>\n\n"
                f"Client ka <b>Telegram User ID</b> bhejo (sirf numbers):\n"
                f"<i>Example:</i> <code>7251902885</code>\n\n"
                f"{pp('💡')} User ID: @userinfobot se pata karo\n\n"
                f"{pp('🛑')} Cancel karne ke liye <code>cancel</code> likho",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn("Cancel", "admin_panel", "danger", "❌")]]))
            return

        if data == "admin_add_sub":
            if not is_admin(uid):
                return
            await safe_edit_message_text(q, f"<blockquote>{pp('⭐️')} <b>ADD SUBSCRIPTION</b></blockquote>\n\nSend: <code>@bot_username days Plan</code>\nExample: <code>@KALAKAAR_xBOT 30 Basic</code>\n\nOr: <code>bot_id days Plan</code>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_panel", "primary", "🔙")]]))
            context.user_data["admin_add_sub"] = True
            return

        if data == "admin_sub_list":
            if not is_admin(uid):
                return
            await show_admin_sub_list(q, context, page=0)
            return

        if data.startswith("admin_sublist_pg_"):
            if not is_admin(uid):
                return
            page = int(data.split("_")[-1])
            await show_admin_sub_list(q, context, page=page)
            return

        if data == "admin_check_expiry":
            if not is_admin(uid):
                return
            await check_expiry(q)
            return

        if data == "admin_stats":
            if not is_admin(uid):
                return
            await show_stats(q)
            return

        if data == "admin_start_all":
            if not is_admin(uid):
                return
            await start_all_userbots(q)
            return

        if data == "admin_stop_all":
            if not is_admin(uid):
                return
            stopped = 0
            for bot_id in list(user_bot_applications.keys()):
                await stop_user_bot(bot_id)
                stopped += 1
            await safe_edit_message_text(q, f"<blockquote>{pp('🛑')} <b>STOP ALL COMPLETE</b></blockquote>\n\n{pe('✅')} Stopped: {stopped}", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return

        # LEAVE RECOVERY CALLBACKS
        if data == "admin_leave_recovery":
            if not is_admin(uid):
                return
            await show_leave_recovery_panel(q)
            return

        if data == "admin_leave_toggle":
            if not is_admin(uid):
                return
            cfg = db.get_leave_recovery_config()
            cfg["enabled"] = not bool(cfg.get("enabled"))
            db.set_leave_recovery_config(cfg)
            await show_leave_recovery_panel(q)
            return

        if data == "admin_leave_set_target":
            if not is_admin(uid):
                return
            context.user_data["admin_set_leave_target"] = True
            await safe_edit_message_text(q, f"<blockquote>{pp('🎯')} <b>SET LEAVE TARGET CHANNEL</b></blockquote>\n\nSend target as:\n<code>-1001234567890 https://t.me/+invite_or_public_link</code>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_leave_recovery", "primary", "🔙")]]))
            return

        if data == "admin_leave_msgs":
            if not is_admin(uid):
                return
            cfg = db.get_leave_recovery_config()
            msgs = cfg.get("messages", [])
            count = len(msgs)
            await safe_edit_message_text(q,
                f"<blockquote>{pp('💬')} <b>LEAVE RECOVERY MESSAGES ({count})</b></blockquote>\n\n"
                f"Jab user channel se leave kare tab ye messages DM mein jayenge.\n\n"
                f"Multiple messages add kar sakte hain — sab ek ke baad ek bheje jayenge.\n"
                f"Har message ke liye alag buttons set kar sakte hain.\n\n"
                f"Placeholders: <code>{{first_name}}</code> <code>{{username}}</code> <code>{{user_id}}</code>\n"
                f"<code>{{source_channel_title}}</code> <code>{{target_channel_link}}</code>",
                parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
            return

        if data == "admin_leave_add_msg":
            if not is_admin(uid):
                return
            context.user_data["admin_set_leave_msg"] = True
            await safe_edit_message_text(q,
                f"<blockquote>{pp('💬')} <b>ADD LEAVE RECOVERY MESSAGE</b></blockquote>\n\n"
                "Send text for DM. Supported placeholders:\n"
                "<code>{{first_name}}</code> <code>{{username}}</code> <code>{{user_id}}</code>\n"
                "<code>{{source_channel_title}}</code> <code>{{target_channel_link}}</code>",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_leave_msgs", "primary", "🔙")]]))
            return

        if data.startswith("admin_leave_view_msg_"):
            if not is_admin(uid):
                return
            idx = int(data.replace("admin_leave_view_msg_", ""))
            cfg = db.get_leave_recovery_config()
            messages = cfg.get("messages", [])
            if idx >= len(messages):
                await show_leave_recovery_panel(q)
                return
            m = messages[idx]
            text_preview = EmojiManager._html_escape((m.get("text") or "")[:300])
            has_buttons = "✅ Yes" if m.get("buttons_json") else "❌ No"
            await safe_edit_message_text(q,
                f"<blockquote>{pp('📝')} <b>MESSAGE #{idx+1}</b></blockquote>\n\n"
                f"<b>Text:</b>\n{text_preview}\n\n"
                f"<b>Buttons:</b> {has_buttons}",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                    [btn("Edit Text", f"admin_leave_edit_text_{idx}", "primary", "📝"),
                     btn("Set Buttons", f"admin_leave_set_btns_{idx}", "primary", "🔘")],
                    [btn("🗑 Delete Message", f"admin_leave_del_msg_{idx}", "danger", "🗑")],
                    [btn("Back", "admin_leave_msgs", "primary", "🔙")],
                ]))
            return

        if data.startswith("admin_leave_del_msg_"):
            if not is_admin(uid):
                return
            idx = int(data.replace("admin_leave_del_msg_", ""))
            cfg = db.get_leave_recovery_config()
            messages = cfg.get("messages", [])
            if 0 <= idx < len(messages):
                messages.pop(idx)
                cfg["messages"] = messages
                db.set_leave_recovery_config(cfg)
            cfg2 = db.get_leave_recovery_config()
            count = len(cfg2.get("messages", []))
            await safe_edit_message_text(q, f"{pe('✅')} Message deleted. Total: {count}",
                parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
            return

        if data.startswith("admin_leave_edit_text_"):
            if not is_admin(uid):
                return
            idx = int(data.replace("admin_leave_edit_text_", ""))
            context.user_data["admin_edit_leave_msg_idx"] = idx
            await safe_edit_message_text(q,
                f"<blockquote>{pp('📝')} <b>EDIT MESSAGE #{idx+1} TEXT</b></blockquote>\n\nSend new text:",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_leave_msgs", "primary", "🔙")]]))
            return

        if data.startswith("admin_leave_set_btns_"):
            if not is_admin(uid):
                return
            idx = int(data.replace("admin_leave_set_btns_", ""))
            context.user_data["admin_set_leave_btns_idx"] = idx
            await safe_edit_message_text(q,
                f"<blockquote>{pp('🔘')} <b>SET BUTTONS FOR MESSAGE #{idx+1}</b></blockquote>\n\n"
                "Send button lines.\n\nFormat:\n"
                "• <code>Button Label|https://link</code>\n"
                "• <code>Label1|https://url1 || Label2|https://url2</code>\n\n"
                "Multiple buttons per row use <code> || </code> separator.",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_leave_msgs", "primary", "🔙")]]))
            return

        if data == "admin_leave_channels":
            if not is_admin(uid):
                return
            await safe_edit_message_text(q,
                f"<blockquote>{pp('⚙️')} <b>PER-CHANNEL LEAVE RECOVERY</b></blockquote>\n\n"
                "Toggle leave recovery ON/OFF for each channel.\n"
                "🟢 = Leave recovery active for this channel\n"
                "🔴 = Leave recovery disabled for this channel\n\n"
                "<i>By default all channels are ON.</i>",
                parse_mode=ParseMode.HTML, reply_markup=leave_recovery_channels_kb())
            return

        if data.startswith("admin_leave_chan_toggle_"):
            if not is_admin(uid):
                return
            chan_id = data.replace("admin_leave_chan_toggle_", "")
            cfg = db.get_leave_recovery_config()
            channel_configs = cfg.get("channel_configs", {})
            current = channel_configs.get(chan_id, True)
            channel_configs[chan_id] = not current
            cfg["channel_configs"] = channel_configs
            db.set_leave_recovery_config(cfg)
            new_status = "🟢 ON" if not current else "🔴 OFF"
            await safe_edit_message_text(q,
                f"{pe('✅')} Channel <code>{chan_id}</code> leave recovery set to <b>{new_status}</b>",
                parse_mode=ParseMode.HTML, reply_markup=leave_recovery_channels_kb())
            return

        if data == "admin_leave_clear_pending":
            if not is_admin(uid):
                return
            db.clear_pending_leave_recovery()
            await show_leave_recovery_panel(q)
            return

        if data == "admin_default_first_msg":
            if not is_admin(uid):
                return
            current = db.get_default_first_message()
            context.user_data["admin_set_default_first_msg"] = True
            await safe_edit_message_text(q,
                f"<blockquote>{pp('💬')} <b>DEFAULT FIRST MESSAGE</b></blockquote>\n\n"
                f"Ye message har user ko <b>sabse pehle</b> jaata hai jab wo join request bhejta hai.\n\n"
                f"<b>Current message:</b>\n<blockquote>{EmojiManager._html_escape(current)}</blockquote>\n\n"
                f"Naya message bhejo:\n\n"
                f"Supported placeholders:\n<code>{{first_name}}</code> <code>{{username}}</code> <code>{{user_id}}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_panel", "primary", "🔙")]]))
            return

        if data == "admin_broadcast":
            if not is_admin(uid):
                return
            await safe_edit_message_text(q, f"<blockquote>{pp('✈️')} <b>BROADCAST</b></blockquote>\n\nChoose target:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [btn("Specific UserBot", "admin_bcast_target_select", "primary", "🎯")],
                [btn("All UserBots", "admin_bcast_target_all", "success", "🌐")],
                [btn("Back", "admin_panel", "primary", "🔙")],
            ]))
            return

        if data == "admin_bcast_target_all":
            if not is_admin(uid):
                return
            context.user_data["admin_broadcast"] = True
            context.user_data["admin_broadcast_target"] = None
            await safe_edit_message_text(q, f"<blockquote>{pp('✈️')} <b>BROADCAST TO ALL</b></blockquote>\n\nSend text or media to broadcast to all userbots' users.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_panel", "primary", "🔙")]]))
            return

        if data == "admin_bcast_target_select":
            if not is_admin(uid):
                return
            bots = db.get_all_user_bots() or []
            if not bots:
                await safe_edit_message_text(q, f"{pe('❌')} No userbots found.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
                return
            kb = [[btn(f"@{bot['bot_username']} ({bot['bot_id']})", f"admin_bcast_pick_{bot['bot_id']}", "primary", "🤖")] for bot in bots]
            kb.append([btn("Back", "admin_broadcast", "primary", "🔙")])
            await safe_edit_message_text(q, f"{pe('🤖')} Select userbot:", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
            return

        if data.startswith("admin_bcast_pick_"):
            if not is_admin(uid):
                return
            bot_id = data.replace("admin_bcast_pick_", "")
            context.user_data["admin_broadcast"] = True
            context.user_data["admin_broadcast_target"] = bot_id
            await safe_edit_message_text(q, f"<blockquote>{pp('✈️')} <b>BROADCAST TO USERBOT {bot_id}</b></blockquote>\n\nSend text or media to broadcast.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_panel", "primary", "🔙")]]))
            return

        if data == "admin_bcast_add_btns":
            if not is_admin(uid):
                return
            draft = context.user_data.get("admin_broadcast_draft", {})
            if draft:
                context.user_data["admin_broadcast_stage"] = "await_buttons"
                await safe_edit_message_text(q, f"{pe('🔘')} Send button lines (Text|https://link per line):", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_panel", "primary", "🔙")]]))
            else:
                await safe_edit_message_text(q, f"{pe('❌')} No draft found.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return

        if data == "admin_bcast_send":
            if not is_admin(uid):
                return
            await preview_admin_broadcast(q, context)
            return

        if data == "admin_bcast_confirm":
            if not is_admin(uid):
                return
            await send_admin_broadcast(q, context)
            return

        if data == "admin_send_reminders":
            if not is_admin(uid):
                return
            sent_count = 0
            for days_threshold in [3, 1]:
                expiring = db.get_expiring_subscriptions(days_threshold)
                for sub in expiring:
                    bot_id = sub["bot_id"]
                    sub_type = sub["subscription_type"]
                    bot_data = db.get_user_bot(bot_id)
                    if bot_data:
                        try:
                            expiry_dt = sub["expiry_date"]
                            if isinstance(expiry_dt, str):
                                expiry_dt = datetime.fromisoformat(expiry_dt.replace('+00:00', ''))
                            expiry_dt = make_aware(expiry_dt)
                            days_left = (expiry_dt - now_aware()).days
                            if days_threshold == 3:
                                msg_text = UIFormatter.expiry_reminder_3d(sub_type, expiry_dt, days_left)
                            else:
                                msg_text = UIFormatter.expiry_reminder_1d(sub_type, expiry_dt)
                            await send_premium_message(context.bot, bot_data["user_id"], msg_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[
                                btn("Renew", f"https://t.me/{ADMIN_USERNAME.lstrip('@')}", "danger", "💰")
                            ]]))
                            db.mark_reminder_sent(bot_id, days_threshold)
                            sent_count += 1
                        except Exception as ex:
                            logging.error(f"Manual reminder failed: {ex}")
            await safe_edit_message_text(q, f"{pe('✅')} Reminders sent to {sent_count} users.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return

        if data.startswith("admin_ub_start_"):
            if not is_admin(uid):
                return
            bot_id = data.replace("admin_ub_start_", "")
            bot_data = db.get_user_bot(bot_id)
            if bot_data:
                sub = db.get_subscription_for_bot(bot_id)
                if sub:
                    try:
                        exp = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
                        if exp > now_aware():
                            await start_user_bot(bot_data["bot_token"], bot_id, bot_data["user_id"])
                            await safe_edit_message_text(q, f"{pe('✅')} Bot @{bot_data['bot_username']} started.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
                        else:
                            await safe_edit_message_text(q, f"{pe('❌')} Subscription expired.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
                    except Exception as e:
                        await safe_edit_message_text(q, f"{pe('❌')} Failed to start: {e}", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return

        if data.startswith("admin_ub_stop_"):
            if not is_admin(uid):
                return
            bot_id = data.replace("admin_ub_stop_", "")
            await stop_user_bot(bot_id)
            await safe_edit_message_text(q, f"{pe('🛑')} Bot stopped.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return

        if data.startswith("admin_ub_info_"):
            if not is_admin(uid):
                return
            bot_id = data.replace("admin_ub_info_", "")
            await show_admin_ub_info(q, bot_id, context)
            return

        if data.startswith("admin_remove_bot_"):
            if not is_admin(uid):
                return
            if data.startswith("admin_remove_bot_confirm_"):
                bot_id = data.replace("admin_remove_bot_confirm_", "")
                await stop_user_bot(bot_id)
                await user_account.unregister_session(bot_id)
                db.remove_user_bot(bot_id)
                await safe_edit_message_text(q, f"{pe('✅')} UserBot removed.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            else:
                bot_id = data.replace("admin_remove_bot_", "")
                await safe_edit_message_text(q, f"{pe('⚠️')} Confirm remove userbot {bot_id}?", parse_mode=ParseMode.HTML, reply_markup=confirm_kb(f"admin_remove_bot_confirm_{bot_id}", "admin_userbots"))
            return

    except Exception as ex:
        logging.error(f"Callback error: {ex}")
        try:
            fallback_kb = admin_kb() if is_admin(uid) else main_menu_kb(uid)
            await safe_edit_message_text(q, f"{pe('❌')} Error: {str(ex)[:100]}", parse_mode=ParseMode.HTML, reply_markup=fallback_kb)
        except Exception:
            pass


async def preview_admin_broadcast(q, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("admin_broadcast_draft", {})
    if not draft:
        await safe_edit_message_text(q, f"{pe('❌')} No draft found.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
        return
    uid = q.from_user.id
    try:
        await send_media(context, uid, draft.get("media"), draft.get("media_type") or "text",
                         draft.get("text", ""), buttons_to_markup(draft.get("buttons_json")),
                         entities_json=draft.get("entities_json"), file_name=draft.get("file_name"), mime_type=draft.get("mime_type"))
    except Exception as ex:
        await safe_edit_message_text(q, f"{pe('❌')} Preview failed: {str(ex)}", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
        return
    target_bot = draft.get("target_bot")
    target_label = f"userbot {target_bot}" if target_bot else "ALL userbots"
    await safe_edit_message_text(q, f"{pe('✅')} Preview sent above.\n{pe('✈️')} Confirm broadcast to <b>{target_label}</b>?", parse_mode=ParseMode.HTML, reply_markup=confirm_kb("admin_bcast_confirm", "admin_panel"))


async def send_admin_broadcast(q, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("admin_broadcast_draft", {})
    for key in ["admin_broadcast", "admin_broadcast_stage", "admin_broadcast_target"]:
        context.user_data.pop(key, None)
    if not draft:
        await safe_edit_message_text(q, f"{pe('❌')} No draft to send.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
        return
    bots = db.get_all_user_bots()
    if draft.get("target_bot"):
        bots = [b for b in bots if b["bot_id"] == draft["target_bot"]]
    await safe_edit_message_text(q, f"{pe('✈️')} Admin broadcast started...", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
    total_sent = 0
    total_fail = 0
    for bot in bots:
        bot_id = bot["bot_id"]
        sub = db.get_subscription_for_bot(bot_id)
        if not sub:
            continue
        try:
            exp = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
            if exp < now_aware():
                continue
        except Exception:
            continue
        if bot_id in user_bot_applications:
            bot_instance = user_bot_applications[bot_id].bot
        else:
            try:
                bot_instance = Bot(token=bot["bot_token"])
            except Exception:
                continue
        recipients = db.get_requesters_for_bot(bot_id)
        for r in recipients:
            try:
                ok = await deliver_media(r, draft.get("media"), draft.get("media_type") or "text",
                                         draft.get("text", ""), bot_instance, buttons_to_markup(draft.get("buttons_json")),
                                         entities_json=draft.get("entities_json"), file_name=draft.get("file_name"), mime_type=draft.get("mime_type"))
                if ok:
                    total_sent += 1
                else:
                    total_fail += 1
            except Forbidden:
                total_fail += 1
            except Exception:
                total_fail += 1
    target_label = f"userbot {draft['target_bot']}" if draft.get("target_bot") else "ALL userbots"
    await safe_edit_message_text(q, f"<blockquote>{pp('✅')} <b>ADMIN BROADCAST COMPLETE</b></blockquote>\n\n{pp('📤')} Target: {target_label}\n{pp('✅')} Sent: {total_sent}\n{pp('❌')} Failed: {total_fail}", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
    context.user_data.pop("admin_broadcast_draft", None)


# ================= MAIN MESSAGE HANDLER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    msg = update.message
    if not msg:
        return

    if context.user_data.get("waiting_token") and not is_admin(user.id):
        token = msg.text.strip()
        if ":" in token and len(token) > 10:
            try:
                test_bot = Bot(token=token)
                bot_info = await test_bot.get_me()
                bot_id = db.add_user_bot(user.id, token, bot_info.username)
                context.user_data.pop("waiting_token", None)
                await reply_premium_message(msg, f"<blockquote>{pp('✅')} <b>BOT ADDED SUCCESSFULLY</b></blockquote>\n\n{pp('🤖')} @{bot_info.username}\nBot ID: <code>{bot_id}</code>\n\n{pp('⚠️')} You need a subscription to activate your bot.\n{pp('📞')} Contact {ADMIN_USERNAME} to subscribe.", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(user.id))
            except Exception as ex:
                await reply_premium_message(msg, f"{pe('❌')} Invalid token or bot error: {ex}\n\nPlease try again.", parse_mode=ParseMode.HTML)
        else:
            await reply_premium_message(msg, f"{pe('❌')} Invalid token format. Please send the correct BotFather token.", parse_mode=ParseMode.HTML)
        return

    wz = context.user_data.get("ubwiz")
    if wz and is_admin(user.id):
        step = wz.get("step")
        text_in = (msg.text or "").strip()
        if text_in.lower() in ("cancel", "/cancel"):
            lc = wz.get("client")
            if lc:
                try:
                    await lc.disconnect()
                except Exception:
                    pass
            context.user_data.pop("ubwiz", None)
            await reply_premium_message(msg, f"{pe('❌')} Add UserBot cancel kar diya.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            return

        if step == "ask_user_id":
            if not text_in.lstrip("-").isdigit():
                await reply_premium_message(msg, f"{pe('❌')} Sirf numbers bhejo. Example: <code>7251902885</code>", parse_mode=ParseMode.HTML)
                return
            wz["user_id"] = int(text_in)
            wz["step"] = "ask_token"
            await reply_premium_message(msg,
                f"<blockquote>{pp('🤖')} <b>ADD USERBOT - STEP 2/4</b></blockquote>\n\n"
                f"Client ke <b>BOT ka BotFather token</b> bhejo:\n"
                f"<i>Example:</i> <code>123456789:ABCdef...</code>",
                parse_mode=ParseMode.HTML)
            return

        if step == "ask_token":
            token = text_in
            if ":" not in token or len(token) < 10:
                await reply_premium_message(msg, f"{pe('❌')} Token format galat hai.", parse_mode=ParseMode.HTML)
                return
            try:
                test_bot = Bot(token=token)
                bot_info = await test_bot.get_me()
            except Exception as ex:
                await reply_premium_message(msg, f"{pe('❌')} Token invalid: {ex}", parse_mode=ParseMode.HTML)
                return
            wz["bot_token"] = token
            wz["bot_username"] = bot_info.username
            wz["tg_bot_id"] = bot_info.id
            wz["step"] = "ask_phone"
            await reply_premium_message(msg,
                f"<blockquote>{pp('📱')} <b>ADD USERBOT - STEP 3/4</b></blockquote>\n\n"
                f"{pe('✅')} Bot: <b>@{bot_info.username}</b>\n\n"
                f"Ab client ke <b>USER ACCOUNT ka phone number</b> bhejo\n"
                f"(jis account se DM jaayenge, country code ke saath):\n"
                f"<i>Example:</i> <code>+919876543210</code>",
                parse_mode=ParseMode.HTML)
            return

        if step == "ask_phone":
            sess_in = text_in.replace(' ', '').replace('\n', '')
            if len(sess_in) > 150:
                wz['phone'] = wz.get('phone') or '(session)'
                wz['client'] = wz.get('client')
                try:
                    await _finish_userbot_login(wz, context, msg, sess_in)
                except Exception as exx:
                    await reply_premium_message(msg, f"{pe('❌')} Session login problem: {exx}", parse_mode=ParseMode.HTML)
                return
            phone = text_in.replace(" ", "")
            if not phone.startswith("+") or not phone[1:].isdigit():
                await reply_premium_message(msg, f"{pe('❌')} Format: <code>+919876543210</code>", parse_mode=ParseMode.HTML)
                return
            try:
                lc = await user_account.start_login(phone)
            except Exception as ex:
                if 'CAPTCHA' in str(ex).upper():
                    await reply_premium_message(msg, f"{pe('🛡')} Telegram ne is VM ke IP par captcha laga diya hai - OTP VM se nahi ja sakta.\n\n{pe('✅')} Fix: phone (Termux) ya PC par make_session.py chalao, wahan phone + OTP do, SESSION STRING yahin STEP 3 me paste karo.\n\n{pe('🔁')} Ya phone dobara bhejo.", parse_mode=ParseMode.HTML)
                    return
                await reply_premium_message(msg, f"{pe('❌')} Login start nahi hua: {ex}", parse_mode=ParseMode.HTML)
                return
            wz["client"] = lc
            wz["phone"] = phone
            wz["step"] = "ask_code"
            await reply_premium_message(msg,
                f"<blockquote>{pp('🔐')} <b>ADD USERBOT - STEP 4/4</b></blockquote>\n\n"
                f"{pe('📩')} <b>{phone}</b> par <b>OTP</b> bheja gaya hai (Telegram app me).\n\n"
                f"Woh <b>OTP code</b> bhejo:",
                parse_mode=ParseMode.HTML)
            return

        if step == "ask_code":
            code = text_in.replace(" ", "")
            lc = wz.get("client")
            if not lc:
                context.user_data.pop("ubwiz", None)
                await reply_premium_message(msg, f"{pe('❌')} Session lost. Dobara Add UserBot chalao.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
                return
            if not code.isdigit():
                await reply_premium_message(msg, f"{pe('❌')} OTP sirf digits hote hain.", parse_mode=ParseMode.HTML)
                return
            session_str, need_2fa, err = await user_account.complete_login(lc, wz["phone"], code)
            if err:
                await reply_premium_message(msg, f"{pe('❌')} {err}", parse_mode=ParseMode.HTML)
                return
            if need_2fa:
                wz["step"] = "ask_2fa"
                await reply_premium_message(msg,
                    f"<blockquote>{pp('🔒')} <b>2FA REQUIRED</b></blockquote>\n\n"
                    f"Client ke account ka <b>2FA password</b> bhejo:",
                    parse_mode=ParseMode.HTML)
                return
            await _finish_userbot_login(wz, context, msg, session_str)
            return

        if step == "ask_2fa":
            lc = wz.get("client")
            if not lc:
                context.user_data.pop("ubwiz", None)
                await reply_premium_message(msg, f"{pe('❌')} Session lost. Dobara Add UserBot chalao.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
                return
            session_str, err = await user_account.complete_2fa(lc, text_in)
            if err:
                await reply_premium_message(msg, f"{pe('❌')} {err}", parse_mode=ParseMode.HTML)
                return
            await _finish_userbot_login(wz, context, msg, session_str)
            return
        return

    if context.user_data.get("admin_add_sub") and is_admin(user.id):
        parts = msg.text.strip().split()
        if len(parts) >= 3:
            bot_identifier = parts[0]
            days = int(parts[1])
            plan = parts[2].capitalize()
            if plan not in ("Basic", "Pro"):
                await reply_premium_message(msg, f"{pe('❌')} Plan must be Basic or Pro.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
                context.user_data.pop("admin_add_sub", None)
                return
            bot = None
            if bot_identifier.startswith("@"):
                bot = db.get_bot_by_username(bot_identifier.lstrip("@"))
            else:
                bot = db.get_user_bot(bot_identifier)
            if not bot:
                await reply_premium_message(msg, f"{pe('❌')} Bot {bot_identifier} not found!", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
                context.user_data.pop("admin_add_sub", None)
                return
            db.add_subscription_for_bot(bot["bot_id"], plan, days)
            context.user_data.pop("admin_add_sub", None)
            await reply_premium_message(msg, f"{pe('✅')} Subscription added!\n{pp('🤖')} @{bot['bot_username']}\n{pp('⭐️')} {plan}\n{pp('📅')} {days} days", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
            try:
                await start_user_bot(bot["bot_token"], bot["bot_id"], bot["user_id"])
                db.set_user_bot_active(bot["bot_id"], True)
                await send_premium_message(context.bot, bot["user_id"], f"<blockquote>{pp('✅')} <b>BOT ACTIVATED</b></blockquote>\n\n{pp('🤖')} @{bot['bot_username']}\n{pp('⭐️')} {plan}\n{pp('📅')} {days} days\n\nYour bot is now running!", parse_mode=ParseMode.HTML)
            except Exception as e:
                logging.error(f"Auto-start failed: {e}")
                await send_premium_message(context.bot, bot["user_id"], f"<blockquote>{pp('✅')} <b>SUBSCRIPTION ACTIVATED</b></blockquote>\n\n{pp('🤖')} @{bot['bot_username']}\n{pp('⭐️')} {plan}\n{pp('📅')} {days} days\n\nUse /start to access your bot panel.", parse_mode=ParseMode.HTML)
        else:
            await reply_premium_message(msg, f"{pe('❌')} Format: <code>@bot_username days Plan</code> or <code>bot_id days Plan</code>", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
        return

    if context.user_data.get("admin_set_leave_target") and is_admin(user.id):
        parts = msg.text.strip().split()
        if len(parts) >= 2 and parts[0].lstrip("-").isdigit():
            cfg = db.get_leave_recovery_config()
            cfg["target_channel_id"] = int(parts[0])
            cfg["target_channel_link"] = parts[1]
            context.user_data.pop("admin_set_leave_target", None)
            db.set_leave_recovery_config(cfg)
            await reply_premium_message(msg, f"{pe('✅')} Leave target channel saved.", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_kb())
        else:
            await reply_premium_message(msg, f"{pe('❌')} Format: <code>-1001234567890 https://t.me/+invite_or_public_link</code>", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_kb())
        return

    if context.user_data.get("admin_set_leave_msg") and is_admin(user.id):
        text = msg.text or msg.caption or ""
        if text:
            cfg = db.get_leave_recovery_config()
            messages = cfg.get("messages", [])
            messages.append({"text": text, "buttons_json": ""})
            cfg["messages"] = messages
            context.user_data.pop("admin_set_leave_msg", None)
            db.set_leave_recovery_config(cfg)
            new_idx = len(messages) - 1
            await reply_premium_message(msg,
                f"{pe('✅')} Leave message #{new_idx+1} saved!\n\n"
                f"Ab is message ke liye buttons set karna chahte ho?",
                parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                    [btn(f"Set Buttons for #{new_idx+1}", f"admin_leave_set_btns_{new_idx}", "primary", "🔘")],
                    [btn("Add Another Message", "admin_leave_add_msg", "success", "➕")],
                    [btn("Done", "admin_leave_msgs", "primary", "✅")],
                ]))
        else:
            await reply_premium_message(msg, f"{pe('❌')} Please send text message.", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_kb())
        return

    if context.user_data.get("admin_edit_leave_msg_idx") is not None and is_admin(user.id):
        idx = context.user_data.pop("admin_edit_leave_msg_idx")
        text = msg.text or msg.caption or ""
        if text:
            cfg = db.get_leave_recovery_config()
            messages = cfg.get("messages", [])
            if 0 <= idx < len(messages):
                messages[idx]["text"] = text
                cfg["messages"] = messages
                db.set_leave_recovery_config(cfg)
                await reply_premium_message(msg, f"{pe('✅')} Message #{idx+1} text updated!", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
            else:
                await reply_premium_message(msg, f"{pe('❌')} Message not found.", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
        else:
            await reply_premium_message(msg, f"{pe('❌')} Please send text message.", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
        return

    if context.user_data.get("admin_set_leave_btns_idx") is not None and is_admin(user.id):
        idx = context.user_data.pop("admin_set_leave_btns_idx")
        btn_json = buttons_json_from_text(msg.text or "")
        if btn_json:
            cfg = db.get_leave_recovery_config()
            messages = cfg.get("messages", [])
            if 0 <= idx < len(messages):
                messages[idx]["buttons_json"] = btn_json
                cfg["messages"] = messages
                db.set_leave_recovery_config(cfg)
                preview_markup = buttons_to_markup(btn_json)
                if preview_markup:
                    try:
                        await reply_premium_message(msg, f"{pe('👁')} <b>Button Preview:</b>", parse_mode=ParseMode.HTML, reply_markup=preview_markup)
                    except Exception:
                        pass
                await reply_premium_message(msg, f"{pe('✅')} Buttons for message #{idx+1} saved!", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
            else:
                await reply_premium_message(msg, f"{pe('❌')} Message not found.", parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
        else:
            await reply_premium_message(msg,
                f"{pe('❌')} No valid buttons.\n\nFormat:\n"
                "• <code>Button Label|https://link</code>\n"
                "• <code>Label1|https://url1 || Label2|https://url2</code>",
                parse_mode=ParseMode.HTML, reply_markup=leave_recovery_msgs_kb())
        return

    if context.user_data.get("admin_set_default_first_msg") and is_admin(user.id):
        text = msg.text or msg.caption or ""
        if text.strip():
            db.set_default_first_message(text.strip())
            context.user_data.pop("admin_set_default_first_msg", None)
            await reply_premium_message(msg, f"{pe('✅')} Default first message saved!\n\n<blockquote>{EmojiManager._html_escape(text.strip())}</blockquote>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back to Admin", "admin_panel", "primary", "🔙")]]))
        else:
            await reply_premium_message(msg, f"{pe('❌')} Please send a text message.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("Back", "admin_default_first_msg", "primary", "🔙")]]))
        return

    if context.user_data.get("admin_broadcast") and is_admin(user.id):
        extracted = MessageManager.extract_from_message(msg)
        draft = {
            "text": msg.text or msg.caption or "",
            "media": msg.photo[-1].file_id if msg.photo else (msg.video.file_id if msg.video else (msg.document.file_id if msg.document else None)),
            "media_type": "photo" if msg.photo else ("video" if msg.video else ("document" if msg.document else "text")),
            "entities_json": extracted["entities_json"],
            "file_name": extracted.get("file_name"),
            "mime_type": extracted.get("mime_type"),
            "target_bot": context.user_data.get("admin_broadcast_target"),
        }
        context.user_data["admin_broadcast_draft"] = draft
        context.user_data["admin_broadcast_stage"] = "await_buttons"
        await reply_premium_message(msg, f"{pe('✅')} Broadcast draft saved. Add buttons or send now?", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
            [btn("Add Inline Buttons", "admin_bcast_add_btns", "primary", "🔘")],
            [btn("Send Now", "admin_bcast_send", "success", "🚀")],
            [btn("Cancel", "admin_panel", "danger", "❌")],
        ]))
        return

    if context.user_data.get("admin_broadcast_stage") == "await_buttons" and is_admin(user.id):
        draft = context.user_data.get("admin_broadcast_draft", {})
        btn_json = buttons_json_from_text(msg.text or "")
        if btn_json:
            draft["buttons_json"] = btn_json
            context.user_data["admin_broadcast_draft"] = draft
            preview_markup = buttons_to_markup(btn_json)
            if preview_markup:
                try:
                    await reply_premium_message(msg, f"{pe('👁')} <b>Button Preview</b> — yahi dikhega users ko:", parse_mode=ParseMode.HTML, reply_markup=preview_markup)
                except Exception:
                    pass
            await reply_premium_message(msg, f"{pe('✅')} Buttons saved. Ready to send?", parse_mode=ParseMode.HTML, reply_markup=confirm_kb("admin_bcast_send", "admin_panel"))
        else:
            await reply_premium_message(msg, f"{pe('❌')} No valid buttons.\n\nFormat:\n• <code>Button Label|https://link</code>\n• <code>Label One|https://link1 || Label Two|https://link2</code>", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
        context.user_data["admin_broadcast_stage"] = "await_send"
        return

    # Admin reply to support message
    if is_admin(user.id) and msg.reply_to_message:
        target_uid = _get_support_uid(SUPPORT_REPLY_MAP, msg.reply_to_message.message_id)
        if target_uid:
            try:
                await context.bot.copy_message(chat_id=target_uid, from_chat_id=msg.chat_id, message_id=msg.message_id)
                await send_ephemeral_reply(msg, f"{pe('✅')} Reply delivered to user {target_uid}", 2)
            except Exception as ex:
                await reply_premium_message(msg, f"{pe('❌')} Reply failed: {ex}", parse_mode=ParseMode.HTML)
            return

    # Non-admin user sending message to MAIN bot (support)
    if not is_admin(user.id):
        try:
            delivered = 0
            user_name = user.first_name or "N/A"
            user_username = user.username or "N/A"

            if msg.text:
                support_text = format_support_msg(user_name, user_username, user.id, msg.text, clickable=True)
                for admin_id in ADMIN_USER_IDS:
                    try:
                        relayed = await context.bot.send_message(chat_id=admin_id, text=support_text,
                                                                  parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                        _store_support_map(SUPPORT_REPLY_MAP, relayed.message_id, user.id)
                        delivered += 1
                    except Exception:
                        pass
            else:
                header_text = format_support_msg(user_name, user_username, user.id, clickable=True)
                media_type_label = getattr(msg, 'content_type', 'media').upper()
                fallback_notice = (
                    f"{header_text}\n\n"
                    f"<i>⚠️ User sent a {media_type_label} — could not forward due to content protection.</i>"
                )
                for admin_id in ADMIN_USER_IDS:
                    try:
                        await context.bot.send_message(chat_id=admin_id, text=header_text,
                                                        parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                        relayed = await safe_copy_message(
                            context.bot, admin_id, msg.chat_id, msg.message_id,
                            fallback_text=fallback_notice
                        )
                        if relayed:
                            _store_support_map(SUPPORT_REPLY_MAP, relayed.message_id, user.id)
                            delivered += 1
                    except Exception:
                        pass

            if delivered > 0:
                await send_ephemeral_reply(msg, f"{pe('✅')} Message sent to support. You will receive a reply here.", 3)
            else:
                await reply_premium_message(msg, f"{pe('⚠️')} Support is temporarily unavailable. Please try again later.", parse_mode=ParseMode.HTML)
        except Exception as ex:
            logging.error(f"Support message error: {ex}")
            await reply_premium_message(msg, f"{pe('⚠️')} Could not send to support right now. Please try again.", parse_mode=ParseMode.HTML)
        return

    await reply_premium_message(msg, f"{pe('🔽')} Use menu.", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(user.id))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err_str = str(context.error)
    logging.error(f"Update {update} caused error {err_str}")
    if "Message is not modified" in err_str:
        return
    if "Query is too old" in err_str:
        return
    if "Forbidden" in err_str:
        return
    if "NetworkError" in err_str or "ReadError" in err_str:
        logging.warning(f"Network error (will retry later): {err_str}")
        return


# ================= START / ADMIN COMMANDS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    # Admin gets admin panel directly, no welcome message
    if is_admin(user.id):
        await reply_premium_message(update.message, f"<blockquote>{pp('👑')} <b>ADMIN PANEL</b></blockquote>", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
        return
    await reply_premium_message(update.message, UIFormatter.main_menu(user.first_name), parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(user.id))


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    await reply_premium_message(update.message, f"<blockquote>{pp('👑')} <b>ADMIN PANEL</b></blockquote>", parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def proof_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    await reply_premium_message(update.message, f"{pp('✅')} Bot is running fine.", parse_mode=ParseMode.HTML)


# ================= MAIN =================
async def main():
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    logging.info(f"{pp('🚀')} Starting Premium Bot System...")

    # ── Config checks ──────────────────────────────────────────────────
    if not MAIN_BOT_TOKEN:
        raise SystemExit("MAIN_BOT_TOKEN set nahi hai. .env.example dekhein aur .env banayein.")

    # ── MongoDB connect (new database setup) ───────────────────────────
    init_database()

    # ── User-account sender start (all recipient DMs use these sessions) ─
    await user_account.start()
    for bot_record in db.get_all_user_bots():
        if bot_record.get("user_session"):
            user_account.register_session(
                bot_record["bot_id"], bot_record["user_session"], bot_record.get("tg_bot_id"))
            logging.info("Registered user-account session for %s", bot_record["bot_id"])
    if not user_account.configured():
        logging.error("No user-account session configured; recipient DMs will fail. Bot API fallback is disabled.")
    else:
        connected_accounts = await user_account.warm_up_sessions()
        logging.info("User-account session warm-up complete: %s account(s) connected", connected_accounts)

    expired_bots = db.get_expired_subscriptions()
    for bot_id in expired_bots:
        bot_data = db.get_user_bot(bot_id)
        if bot_data and bot_data["is_active"] == 1:
            if bot_id in user_bot_applications:
                try:
                    app = user_bot_applications[bot_id]
                    await app.updater.stop()
                    await app.stop()
                    await app.shutdown()
                    logging.info(f"{pp('🛑')} Stopped expired bot on startup: {bot_id}")
                except Exception as ex:
                    logging.error(f"Error stopping expired bot {bot_id}: {ex}")
                user_bot_applications.pop(bot_id, None)
            db.set_user_bot_active(bot_id, False)

    bots = db.get_all_user_bots()
    if bots:
        logging.info(f"Found {len(bots)} user bots to start")
        for bot in bots:
            sub = db.get_subscription_for_bot(bot["bot_id"])
            if not sub:
                logging.info(f"Skipping {bot['bot_id']} - no subscription")
                db.set_user_bot_active(bot["bot_id"], False)
                continue
            try:
                expiry = make_aware(sub["expiry_date"]) if isinstance(sub["expiry_date"], datetime) else sub["expiry_date"]
                if expiry < now_aware():
                    logging.info(f"Skipping {bot['bot_id']} - subscription expired")
                    db.set_user_bot_active(bot["bot_id"], False)
                    continue
            except Exception as ex:
                logging.error(f"Error checking expiry for {bot['bot_id']}: {ex}")
                continue
            try:
                await start_user_bot(bot["bot_token"], bot["bot_id"], bot["user_id"])
                db.set_user_bot_active(bot["bot_id"], True)
                logging.info(f"{pp('✅')} Started user bot @{bot['bot_username']} for {bot['bot_id']}")
            except Exception as ex:
                logging.error(f"{pp('❌')} Failed to start user bot {bot['bot_id']}: {ex}")
    else:
        logging.info("No user bots found in database")

    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connection_pool_size=100,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )

    app = ApplicationBuilder().token(MAIN_BOT_TOKEN).concurrent_updates(True).request(request).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("proof", proof_text_command))
    app.add_handler(CommandHandler("prooftext", proof_text_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.Sticker.ALL, handle_message))
    app.add_error_handler(error_handler)

    app.job_queue.run_repeating(subscription_reminder_job, interval=43200, first=60, name="subscription_reminders")
    app.job_queue.run_repeating(check_expired_subscriptions_job, interval=3600, first=120, name="expired_subscriptions_check")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query", "chat_member", "chat_join_request", "inline_query"])
    logging.info(f"{pp('✅')} Main bot started successfully")

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logging.info(f"{pp('🛑')} Stopping bots...")
        for bot_id, user_app in user_bot_applications.items():
            try:
                await user_app.updater.stop()
                await user_app.stop()
                await user_app.shutdown()
                logging.info(f"{pp('✅')} Stopped user bot {bot_id}")
            except Exception:
                pass
        await user_account.stop()
        logging.info(f"{pp('✅')} All bots stopped")


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info(f"{pp('🛑')} Stopped by user")
            break
        except Exception as ex:
            logging.error(f"{pp('❌')} Fatal error: {ex}")
            logging.info(f"{pp('🔄')} Restarting in 10 seconds...")
            time.sleep(10)