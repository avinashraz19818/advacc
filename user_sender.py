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
    RPCError, ServerError, TimedOutError, SessionPasswordNeededError,
    PhoneCodeInvalidError, PhoneCodeExpiredError,
)

log = logging.getLogger("user_sender")

API_ID = int(os.getenv("API_ID", "6").strip() or 6)
API_HASH = os.getenv("API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()
USERBOT_SESSION = os.getenv("USERBOT_SESSION", "").strip()

MEDIA_CACHE_MAX = 40
MAX_DELIVERY_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5
SEND_GAP = 0.10
JOIN_REQUEST_LOOKUP_MAX_PAGES = 6
JOIN_REQUEST_LOOKUP_ATTEMPTS = 3
JOIN_REQUEST_LOOKUP_RETRY_DELAY = 0.35

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
        self.client = None                              # optional global USERBOT_SESSION
        self._clients = {}                              # session_str -> TelegramClient
        self._session_by_tgid = {}                      # Telegram bot id -> session
        self._session_by_bot = {}                       # internal bot_id -> session
        self._tgid_by_bot = {}                          # internal bot_id -> Telegram bot id
        self._client_locks = {}                         # serialize connection per session
        self._send_locks = {}                           # serialize sends per Telegram account
        self._primed_by_client = {}                     # client id -> user id -> Telethon entity
        self._media_cache = OrderedDict()

    # ---------- registry ----------
    def register_session(self, bot_id, session_str, tg_bot_id=None):
        if not session_str:
            return
        key = str(bot_id)
        old_tgid = self._tgid_by_bot.get(key)
        old_session = self._session_by_bot.get(key)
        if old_tgid and old_tgid != tg_bot_id and self._session_by_tgid.get(old_tgid) == old_session:
            self._session_by_tgid.pop(old_tgid, None)
        self._session_by_bot[key] = session_str
        if tg_bot_id is not None:
            tg_bot_id = int(tg_bot_id)
            self._tgid_by_bot[key] = tg_bot_id
            self._session_by_tgid[tg_bot_id] = session_str

    def attach_client(self, session_str, client, bot_id=None, tg_bot_id=None):
        """Login wizard ka live client cache me daalo (dobara connect mat karo)."""
        if session_str and client is not None:
            self._clients[session_str] = client
        if bot_id is not None:
            self.register_session(bot_id, session_str, tg_bot_id)

    async def unregister_session(self, bot_id):
        """Removed bot ki session mapping hatao; shared/global session disconnect na ho."""
        key = str(bot_id)
        session_str = self._session_by_bot.pop(key, None)
        tg_bot_id = self._tgid_by_bot.pop(key, None)
        if tg_bot_id and self._session_by_tgid.get(tg_bot_id) == session_str:
            self._session_by_tgid.pop(tg_bot_id, None)
        if not session_str or session_str in self._session_by_bot.values() or session_str == USERBOT_SESSION:
            return
        client = self._clients.pop(session_str, None)
        if client is not None:
            self._primed_by_client.pop(id(client), None)
            self._send_locks.pop(id(client), None)
            try:
                await client.disconnect()
            except Exception:
                pass
        self._client_locks.pop(session_str, None)

    def session_for(self, bot=None):
        tg = getattr(bot, "id", None)
        try:
            tg = int(tg) if tg is not None else None
        except (TypeError, ValueError):
            tg = None
        if tg is not None and tg in self._session_by_tgid:
            return self._session_by_tgid[tg]

        # Older database rows may not have tg_bot_id. A single registered account
        # is unambiguous; with multiple accounts, fail closed instead of sending
        # from another client's user account.
        sessions = set(self._session_by_bot.values())
        # Fall back to a lone legacy session only when no explicit Telegram-bot
        # mappings exist (or the caller has no bot identity). Never borrow another
        # bot's mapped account just because this is the only registered session.
        if len(sessions) == 1 and (tg is None or not self._session_by_tgid):
            return next(iter(sessions))
        if sessions and bot is not None:
            log.warning("No user-account session is mapped to Telegram bot %s", tg)
        return None

    # ---------- clients ----------
    async def get_client(self, session_str):
        if not session_str:
            return None
        lock = self._client_locks.setdefault(session_str, asyncio.Lock())
        async with lock:
            client = self._clients.get(session_str)
            if client is not None:
                try:
                    if client.is_connected():
                        return client
                except Exception:
                    pass
                old_client_id = id(client)
                self._primed_by_client.pop(old_client_id, None)
                self._send_locks.pop(old_client_id, None)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                self._clients.pop(session_str, None)

            client = None
            try:
                client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
                await client.connect()
                if not await client.is_user_authorized():
                    log.error("User-account session is invalid or logged out")
                    await client.disconnect()
                    return None
                self._clients[session_str] = client
                return client
            except Exception as ex:
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                log.error("User-account session connect failed: %s", ex)
                return None

    async def _resolve(self, session=None, bot=None):
        if session:
            return await self.get_client(session)

        # Once per-client sessions are registered, never silently use a different
        # account (or the global session) for a bot whose mapping is ambiguous.
        if self._session_by_bot:
            selected = self.session_for(bot)
            return await self.get_client(selected) if selected else None

        if self.client is not None:
            try:
                if self.client.is_connected():
                    return self.client
            except Exception:
                pass
        if USERBOT_SESSION:
            client = await self.get_client(USERBOT_SESSION)
            if client is not None:
                self.client = client
            return client
        return None

    def available(self, session=None, bot=None) -> bool:
        """True when a sender is configured for this bot (not a health guarantee)."""
        if session:
            return True
        if self._session_by_bot:
            return self.session_for(bot) is not None
        if USERBOT_SESSION:
            return True
        try:
            return self.client is not None and self.client.is_connected()
        except Exception:
            return False

    # ---------- lifecycle ----------
    def configured(self) -> bool:
        return bool(USERBOT_SESSION) or bool(self._session_by_bot)

    async def warm_up_sessions(self) -> int:
        """Connect and validate stored sessions before accepting join requests."""
        sessions = list(dict.fromkeys(self._session_by_bot.values()))
        if USERBOT_SESSION and USERBOT_SESSION not in sessions:
            sessions.append(USERBOT_SESSION)
        connected = 0
        for session_str in sessions:
            client = await self.get_client(session_str)
            if client is None:
                log.error("A registered user-account session could not be started")
                continue
            if session_str == USERBOT_SESSION:
                self.client = client
            connected += 1
        return connected

    async def start(self) -> bool:
        if not USERBOT_SESSION:
            log.info("Global USERBOT_SESSION not set; using registered per-client sessions")
            return False
        client = await self.get_client(USERBOT_SESSION)
        if client is None:
            log.error("Global USERBOT_SESSION is invalid or unavailable")
            return False
        self.client = client
        try:
            me = await client.get_me()
            log.info("Global user-account online: %s", getattr(me, "username", None) or me.first_name)
            return True
        except Exception as ex:
            log.error("Global user-account identity check failed: %s", ex)
            return False

    async def stop(self) -> None:
        clients = {id(c): c for c in self._clients.values() if c is not None}
        if self.client is not None:
            clients[id(self.client)] = self.client
        for client in clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients = {}
        self.client = None
        self._client_locks = {}
        self._send_locks = {}
        self._primed_by_client = {}

    # ---------- login wizard ----------
    async def start_login(self, phone: str):
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        await client.send_code_request(phone)
        return client

    async def complete_login(self, client, phone, code):
        """Return (session_str|None, need_2fa: bool, error|None)."""
        try:
            await client.sign_in(phone=phone, code=code)
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
        """Retry rate limits and transient transport failures; never change sender."""
        for attempt in range(MAX_DELIVERY_ATTEMPTS):
            try:
                return await do()
            except FloodWaitError as ex:
                if attempt + 1 >= MAX_DELIVERY_ATTEMPTS:
                    log.error("FloodWait persists after %s attempts during %s", MAX_DELIVERY_ATTEMPTS, description)
                    return None
                # Telegram specifies the minimum wait. Do not cap it and retry early.
                wait = max(1, int(ex.seconds) + 1)
                log.warning("FloodWait %ss during %s; retrying after the required wait", wait, description)
                await asyncio.sleep(wait)
            except (UserIsBlockedError, InputUserDeactivatedError, ChatWriteForbiddenError,
                    UserPrivacyRestrictedError, PeerFloodError) as ex:
                log.warning("Cannot deliver %s: %s", description, type(ex).__name__)
                return None
            except (asyncio.TimeoutError, OSError, ServerError, TimedOutError) as ex:
                if attempt + 1 >= MAX_DELIVERY_ATTEMPTS:
                    log.error("Transient send failure after %s attempts during %s: %s",
                              MAX_DELIVERY_ATTEMPTS, description, ex)
                    return None
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log.warning("Transient send failure during %s; retry %s/%s in %.1fs: %s",
                            description, attempt + 1, MAX_DELIVERY_ATTEMPTS, delay, ex)
                await asyncio.sleep(delay)
            except RPCError as ex:
                log.warning("Non-retryable Telegram RPC error during %s: %s", description, ex)
                return None
        return None

    def _recipient_for_client(self, client, user_id):
        if isinstance(user_id, int):
            return self._primed_by_client.get(id(client), {}).get(user_id, user_id)
        return user_id

    async def _send_serialized(self, client, do, description):
        lock = self._send_locks.setdefault(id(client), asyncio.Lock())
        async with lock:
            result = await self._retry_flood(do, description)
            if result is not None:
                await asyncio.sleep(SEND_GAP)
            return result

    async def _get_media_bytes(self, media_id, file_name, media_type, bot):
        # Telegram file ids belong to a bot token; include it to avoid cross-bot cache hits.
        cache_key = (getattr(bot, "id", None), str(media_id))
        cached = self._media_cache.get(cache_key)
        if cached is not None:
            self._media_cache.move_to_end(cache_key)
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
        self._media_cache[cache_key] = {"data": data, "name": name}
        while len(self._media_cache) > MEDIA_CACHE_MAX:
            self._media_cache.popitem(last=False)
        return data, name

    @staticmethod
    def _as_file_obj(data, name):
        f = io.BytesIO(data)
        f.name = name
        return f

    async def prime_requester(self, channel_id, user_id, bot=None, session=None):
        """Resolve a join requester using this account's own Telethon entity cache."""
        if not channel_id or not user_id:
            return False
        client = await self._resolve(session, bot)
        if not client:
            log.warning("prime_requester: no user-account session is available")
            return False

        target = int(user_id)
        cache = self._primed_by_client.setdefault(id(client), {})
        try:
            # Fast path: the user may already be known to this account.
            try:
                entity = await client.get_input_entity(target)
                cache[target] = entity
                return True
            except Exception:
                pass

            from datetime import datetime
            from telethon import functions, types

            try:
                peer = await client.get_input_entity(int(channel_id))
            except Exception:
                # A logged-in account that was just added as channel admin may not
                # have cached the channel yet. Load dialogs once, then resolve it.
                await client.get_dialogs()
                peer = await client.get_input_entity(int(channel_id))

            for lookup_attempt in range(JOIN_REQUEST_LOOKUP_ATTEMPTS):
                offset_user = types.InputUserEmpty()
                offset_date = datetime.utcnow()
                for _page in range(JOIN_REQUEST_LOOKUP_MAX_PAGES):
                    try:
                        result = await client(functions.messages.GetChatInviteImportersRequest(
                            peer=peer, requested=True, offset_date=offset_date,
                            offset_user=offset_user, limit=100))
                    except Exception as ex:
                        log.warning("Could not look up pending join requester %s: %s", target, ex)
                        return False

                    users = {user.id: user for user in (result.users or [])}
                    importers = result.importers or []
                    user = users.get(target)
                    if user is not None:
                        try:
                            client.session.cache_entity(user)
                        except Exception:
                            pass
                        cache[target] = user
                        log.info("Resolved join requester %s through the user account", target)
                        return True

                    if len(importers) < 100:
                        break
                    last = importers[-1]
                    last_user = users.get(last.user_id)
                    if last_user is None or getattr(last_user, "access_hash", None) is None:
                        break
                    offset_user = types.InputUser(last_user.id, last_user.access_hash)
                    offset_date = last.date

                if lookup_attempt + 1 < JOIN_REQUEST_LOOKUP_ATTEMPTS:
                    # Bot API and MTProto updates can arrive a fraction apart. Give
                    # Telegram time to publish the pending-request entity, then retry.
                    await asyncio.sleep(JOIN_REQUEST_LOOKUP_RETRY_DELAY * (lookup_attempt + 1))

            log.warning("Join requester %s was not found in the account's pending requests", target)
            return False
        except Exception as ex:
            log.warning("prime_requester failed for user %s: %s", target, ex)
            return False

    async def send_text(self, user_id, text, markup=None, bot=None, session=None):
        client = await self._resolve(session, bot)
        if not client:
            log.error("Text not sent to %s: no matching user-account session", user_id)
            return None
        recipient = self._recipient_for_client(client, user_id)
        html = clean_user_html(text)
        if not html.strip():
            return None
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            try:
                return await client.send_message(recipient, html, buttons=buttons,
                                                 parse_mode="html", link_preview=False)
            except ValueError:
                return await client.send_message(recipient, html, buttons=buttons,
                                                 parse_mode=None, link_preview=False)

        msg = await self._send_serialized(client, _do, f"text to {user_id}")
        return getattr(msg, "id", None) if msg else None

    async def send_media(self, user_id, media_id, media_type, text="", markup=None,
                         file_name=None, mime_type=None, bot=None, session=None):
        client = await self._resolve(session, bot)
        if not client or not media_id or bot is None:
            log.error("Media not sent to %s: user account or source bot is unavailable", user_id)
            return None
        recipient = self._recipient_for_client(client, user_id)
        data, name = await self._get_media_bytes(media_id, file_name, media_type, bot)
        if not data:
            return None
        html = clean_user_html(text)
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            f = self._as_file_obj(data, name)
            try:
                return await client.send_file(recipient, f, caption=html or None, buttons=buttons,
                                              parse_mode="html" if html else None)
            except ValueError:
                f.seek(0)
                return await client.send_file(recipient, f, caption=html or None, buttons=buttons,
                                              parse_mode=None)

        msg = await self._send_serialized(client, _do, f"media to {user_id}")
        return getattr(msg, "id", None) if msg else None

    async def send_media_group(self, user_id, items, bot, caption=None, markup=None, session=None):
        client = await self._resolve(session, bot)
        if not client or not items or bot is None:
            log.error("Media group not sent to %s: user account or source bot is unavailable", user_id)
            return None
        recipient = self._recipient_for_client(client, user_id)
        files = []
        for item in items:
            data, name = await self._get_media_bytes(item.get("media_id"), item.get("file_name"),
                                                     item.get("media_type"), bot)
            if data:
                files.append(self._as_file_obj(data, name))
        if not files:
            return None
        html = clean_user_html(caption)
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            # Telethon consumes file-like objects; rewind on every retry.
            for file_obj in files:
                file_obj.seek(0)
            try:
                return await client.send_file(recipient, files, caption=html or None, buttons=buttons,
                                              parse_mode="html" if html else None)
            except ValueError:
                for file_obj in files:
                    file_obj.seek(0)
                return await client.send_file(recipient, files, caption=html or None, buttons=buttons,
                                              parse_mode=None)

        messages = await self._send_serialized(client, _do, f"album to {user_id}")
        if not messages:
            return None
        if not isinstance(messages, list):
            messages = [messages]
        return [message_id for message in messages
                if (message_id := getattr(message, "id", None)) is not None]

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
