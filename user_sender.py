"""
user_sender.py - USER ACCOUNT delivery (Telethon) - per-client sessions
Har client ka apna user account: admin Add UserBot wizard se login karta hai,
session MongoDB me save hota hai, DMs usi account se jaate hain.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from telethon import TelegramClient, Button
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, UserIsBlockedError, InputUserDeactivatedError,
    ChatWriteForbiddenError, UserPrivacyRestrictedError, PeerFloodError,
    RPCError, SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError,
)

log = logging.getLogger("user_sender")

API_ID = int(os.getenv("API_ID", "6").strip() or 6)
API_HASH = os.getenv("API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()
USERBOT_SESSION = os.getenv("USERBOT_SESSION", "").strip()

MEDIA_CACHE_MAX = 40
FLOOD_SLEEP_CAP = 60
SEND_GAP = 0.10

_TG_EMOJI_RE = re.compile(r"</?tg-emoji(?:\s+emoji-id=\"\d+\")?>")

_DEFAULT_NAMES = {
    "photo": "photo.jpg", "video": "video.mp4", "document": "document.bin",
    "animation": "animation.mp4", "audio": "audio.mp3", "voice": "voice.ogg",
    "video_note": "video_note.mp4", "sticker": "sticker.webp",
}


def clean_user_html(text):
    if not text:
        return ""
    return _TG_EMOJI_RE.sub("", text)


def markup_to_telethon_buttons(markup):
    if not markup:
        return None
    rows = []
    try:
        for row in markup.inline_keyboard:
            new_row = []
            for b in row:
                if getattr(b, "url", None):
                    new_row.append(Button.url(b.text, b.url))
            if new_row:
                rows.append(new_row)
    except Exception as ex:
        log.warning("markup_to_telethon_buttons failed: %s", ex)
        return None
    return rows or None


class UserAccountSender:
    """Session-keyed user-account clients (har client ka apna account)."""

    def __init__(self):
        self.client = None                              # global fallback (USERBOT_SESSION)
        self._clients = {}                              # session_str -> TelegramClient
        self._session_by_tgid = {}                      # telegram bot id -> session
        self._session_by_bot = {}                       # bot_id -> session
        self._media_cache = OrderedDict()

    # ---------- registry ----------
    def register_session(self, bot_id, session_str, tg_bot_id=None):
        if not session_str:
            return
        self._session_by_bot[str(bot_id)] = session_str
        if tg_bot_id:
            self._session_by_tgid[int(tg_bot_id)] = session_str

    def attach_client(self, session_str, client, bot_id=None, tg_bot_id=None):
        """Login wizard ka live client cache me daalo (dobara connect mat karo)."""
        if session_str and client is not None:
            self._clients[session_str] = client
        if bot_id is not None:
            self.register_session(bot_id, session_str, tg_bot_id)

    def session_for(self, bot=None):
        tg = getattr(bot, "id", None)
        try:
            tg = int(tg) if tg is not None else None
        except Exception:
            tg = None
        if tg and tg in self._session_by_tgid:
            return self._session_by_tgid[tg]
        if len(self._session_by_tgid) == 1:
            return next(iter(self._session_by_tgid.values()))
        if bot is not None:
            log.warning('session_for: bot %s ke liye session nahi mila', tg)
        return None

    # ---------- clients ----------
    async def get_client(self, session_str):
        if not session_str:
            return None
        c = self._clients.get(session_str)
        if c is not None and c.is_connected():
            return c
        try:
            c = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await c.connect()
            if not await c.is_user_authorized():
                await c.disconnect()
                return None
            self._clients[session_str] = c
            return c
        except Exception as ex:
            log.error("session connect failed: %s", ex)
            return None

    async def _resolve(self, session=None, bot=None):
        s = session or self.session_for(bot)
        if s:
            return await self.get_client(s)
        if self.client is not None and self.client.is_connected():
            return self.client
        return None

    def available(self, session=None, bot=None) -> bool:
        if session or self.session_for(bot):
            return True
        return self.client is not None and self.client.is_connected()

    # ---------- lifecycle ----------
    def configured(self) -> bool:
        return bool(USERBOT_SESSION) or bool(self._session_by_bot)

    async def start(self) -> bool:
        if not USERBOT_SESSION:
            log.info("Global USERBOT_SESSION set nahi hai (koi baat nahi - per-client sessions chalte hain)")
            return False
        try:
            self.client = TelegramClient(StringSession(USERBOT_SESSION), API_ID, API_HASH)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                log.error("USERBOT_SESSION invalid/expired - global fallback OFF")
                await self.client.disconnect()
                self.client = None
                return False
            me = await self.client.get_me()
            log.info("Global user-account online: %s", getattr(me, "username", None) or me.first_name)
            return True
        except Exception as ex:
            log.error("Global user-account start failed: %s", ex)
            self.client = None
            return False

    async def stop(self) -> None:
        for c in list(self._clients.values()) + ([self.client] if self.client else []):
            try:
                await c.disconnect()
            except Exception:
                pass
        self._clients = {}
        self.client = None

    # ---------- login wizard ----------
    async def start_login(self, phone: str):
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        sent_code = await client.send_code_request(phone)
        # phone_code_hash ko explicitly capture karo — Telethon internally pop()
        # karta hai sign_in mein, isliye pehli failed attempt ke baad hash gayab
        # ho jaata hai aur agle attempt par PhoneCodeExpiredError aata hai.
        phone_code_hash = sent_code.phone_code_hash
        return client, phone_code_hash

    async def complete_login(self, client, phone, code, phone_code_hash=None):
        """Return (session_str|None, need_2fa: bool, error|None)."""
        try:
            # Explicit phone_code_hash pass karo taaki retry par bhi kaam kare
            sign_kwargs = {}
            if phone_code_hash:
                sign_kwargs['phone_code_hash'] = phone_code_hash
            await client.sign_in(phone=phone, code=code, **sign_kwargs)
        except SessionPasswordNeededError:
            return None, True, None
        except PhoneCodeInvalidError:
            return None, False, "OTP galat hai. Dobara bhejo:"
        except PhoneCodeExpiredError:
            return None, False, "OTP expire ho gaya. Dobara phone number bhejo:"
        except Exception as e:
            return None, False, f"Login fail: {e}"
        return client.session.save(), False, None

    async def complete_2fa(self, client, password):
        try:
            await client.sign_in(password=password)
        except Exception as e:
            return None, f"2FA fail: {e}"
        return client.session.save(), None

    # ---------- delivery ----------
    async def _retry_flood(self, do, description):
        for attempt in range(3):
            try:
                return await do()
            except FloodWaitError as e:
                wait = min(int(e.seconds) + 1, FLOOD_SLEEP_CAP)
                log.warning("FloodWait %ss during %s", wait, description)
                await asyncio.sleep(wait)
            except (UserIsBlockedError, InputUserDeactivatedError, ChatWriteForbiddenError,
                    UserPrivacyRestrictedError, PeerFloodError) as e:
                log.warning("Cannot deliver %s: %s", description, type(e).__name__)
                return None
            except RPCError as e:
                log.warning("RPC error during %s: %s", description, e)
                return None
        return None

    async def _get_media_bytes(self, media_id, file_name, media_type, bot):
        cached = self._media_cache.get(media_id)
        if cached is not None:
            self._media_cache.move_to_end(media_id)
            return cached["data"], cached["name"]
        try:
            tg_file = await bot.get_file(media_id)
            buf = io.BytesIO()
            await tg_file.download_to_memory(buf)
            data = buf.getvalue()
        except Exception as ex:
            log.error("media download failed for %s: %s", media_id, ex)
            return None, None
        name = file_name or _DEFAULT_NAMES.get(media_type or "", "file.bin")
        self._media_cache[media_id] = {"data": data, "name": name}
        while len(self._media_cache) > MEDIA_CACHE_MAX:
            self._media_cache.popitem(last=False)
        return data, name

    @staticmethod
    def _as_file_obj(data, name):
        f = io.BytesIO(data)
        f.name = name
        return f

    async def prime_requester(self, channel_id, user_id, bot=None, session=None):
        # Join-requester ko entity cache me lao (access_hash ke saath)
        if not hasattr(self, "_primed"):
            self._primed = {}
        if not channel_id or not user_id:
            return False
        client = await self._resolve(session, bot)
        if not client:
            log.warning("prime_requester: client nahi mila")
            return False
        try:
            from datetime import datetime
            from telethon import functions, types
            try:
                peer = await client.get_input_entity(int(channel_id))
            except Exception:
                await client.get_dialogs()
                peer = await client.get_input_entity(int(channel_id))
            target = int(user_id)
            log.info("prime_requester: channel %s user %s search", channel_id, target)
            for requested in (True, False):
                for start in (datetime.utcnow(), datetime(2015, 1, 1)):
                    offset_user = types.InputUserEmpty()
                    offset_date = start
                    for _ in range(6):
                        try:
                            res = await client(functions.messages.GetChatInviteImportersRequest(
                                peer=peer, requested=requested, offset_date=offset_date,
                                offset_user=offset_user, limit=100))
                        except Exception as pex:
                            log.info('prime page skip: %s', pex)
                            break
                        users = {u.id: u for u in (res.users or [])}
                        importers = res.importers or []
                        log.info("prime_requester: requested=%s -> %s users / %s importers",
                                 requested, len(users), len(importers))
                        if target in users:
                            u = users[target]
                            try:
                                client.session.cache_entity(u)
                            except Exception:
                                pass
                            self._primed[target] = u
                            log.info("prime_requester: user %s MIL GAYA - entity ready", target)
                            return True
                        if len(importers) < 100:
                            break
                        last = importers[-1]
                        lu = users.get(last.user_id)
                        if not lu:
                            break
                        offset_user = types.InputUser(lu.id, lu.access_hash)
                        offset_date = last.date
            log.warning("prime_requester: user %s list me NAHI mila", target)
            return False
        except Exception as ex:
            log.warning("prime_requester failed: %s", ex)
            return False

    async def send_text(self, user_id, text, markup=None, bot=None, session=None):
        client = await self._resolve(session, bot)
        if not client:
            return None
        if isinstance(user_id, int) and getattr(self, '_primed', {}).get(user_id):
            user_id = self._primed[user_id]
        html = clean_user_html(text)
        if not html.strip():
            return None
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            try:
                return await client.send_message(user_id, html, buttons=buttons,
                                                 parse_mode="html", link_preview=False)
            except ValueError:
                return await client.send_message(user_id, html, buttons=buttons,
                                                 parse_mode=None, link_preview=False)

        msg = await self._retry_flood(_do, f"text to {user_id}")
        await asyncio.sleep(SEND_GAP)
        return msg.id if msg else None

    async def send_media(self, user_id, media_id, media_type, text="", markup=None,
                         file_name=None, mime_type=None, bot=None, session=None):
        client = await self._resolve(session, bot)
        if not client or not media_id or bot is None:
            return None
        if isinstance(user_id, int) and getattr(self, '_primed', {}).get(user_id):
            user_id = self._primed[user_id]
        data, name = await self._get_media_bytes(media_id, file_name, media_type, bot)
        if not data:
            return None
        html = clean_user_html(text)
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            f = self._as_file_obj(data, name)
            try:
                return await client.send_file(user_id, f, caption=html or None, buttons=buttons,
                                              parse_mode="html" if html else None)
            except ValueError:
                f.seek(0)
                return await client.send_file(user_id, f, caption=html or None, buttons=buttons,
                                              parse_mode=None)

        msg = await self._retry_flood(_do, f"media to {user_id}")
        await asyncio.sleep(SEND_GAP)
        return msg.id if msg else None

    async def send_media_group(self, user_id, items, bot, caption=None, markup=None, session=None):
        client = await self._resolve(session, bot)
        if not client or not items or bot is None:
            return None
        if isinstance(user_id, int) and getattr(self, '_primed', {}).get(user_id):
            user_id = self._primed[user_id]
        files = []
        for it in items:
            data, name = await self._get_media_bytes(it.get("media_id"), it.get("file_name"),
                                                     it.get("media_type"), bot)
            if data:
                files.append(self._as_file_obj(data, name))
        if not files:
            return None
        html = clean_user_html(caption)
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            try:
                return await client.send_file(user_id, files, caption=html or None, buttons=buttons,
                                              parse_mode="html" if html else None)
            except ValueError:
                for f in files:
                    f.seek(0)
                return await client.send_file(user_id, files, caption=html or None, buttons=buttons,
                                              parse_mode=None)

        msgs = await self._retry_flood(_do, f"album to {user_id}")
        await asyncio.sleep(SEND_GAP)
        if not msgs:
            return None
        if not isinstance(msgs, list):
            msgs = [msgs]
        return [m.id for m in msgs if m is not None]

    async def delete_message(self, user_id, message_id, bot=None, session=None):
        client = await self._resolve(session, bot)
        if not client:
            return False
        try:
            await client.delete_messages(user_id, [message_id])
            return True
        except Exception as ex:
            log.warning("user-account delete failed (%s/%s): %s", user_id, message_id, ex)
            return False


user_sender = UserAccountSender()
