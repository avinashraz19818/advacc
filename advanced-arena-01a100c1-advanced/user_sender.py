"""
user_sender.py — USER-ACCOUNT message delivery (Telethon)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Is modified version me bot ki jagah saari USER-FACING delivery ek real
USER ACCOUNT (Telethon StringSession) se hoti hai:

  • Join request aane par default-first-message + saved welcome messages
  • Owner/admin broadcast
  • Leave-recovery DMs
  • /start deep-link welcome

Bot sirf panel/UI, join-request events aur fallback ke liye rehta hai.
Media BOT file_id me store hoti hai isliye user-account se bhejne ke liye
bot se download karke user-account se re-upload kiya jata hai (cache ke saath).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from collections import OrderedDict
from typing import Any, List, Optional

from telethon import TelegramClient, Button
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    UserIsBlockedError,
    InputUserDeactivatedError,
    ChatWriteForbiddenError,
    UserPrivacyRestrictedError,
    PeerFloodError,
    RPCError,
)

log = logging.getLogger("user_sender")

# ── Telegram API credentials (Telethon user login) ──────────────────────────
# Default values are the public Telegram Desktop API credentials (same as the
# forward bot). Override in .env with your own https://my.telegram.org values.
API_ID = int(os.getenv("API_ID", "6").strip() or 6)
API_HASH = os.getenv("API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e").strip()
USERBOT_SESSION = os.getenv("USERBOT_SESSION", "").strip()

MEDIA_CACHE_MAX = 40          # kitni media files bytes-cache me rahein
FLOOD_SLEEP_CAP = 60          # max seconds to sleep on FloodWait
SEND_GAP = 0.10               # har user-account send ke beech chhota gap

_TG_EMOJI_RE = re.compile(r"</?tg-emoji(?:\s+emoji-id=\"\d+\")?>")

# media_type -> default file name (jab original file_name na ho)
_DEFAULT_NAMES = {
    "photo": "photo.jpg",
    "video": "video.mp4",
    "document": "document.bin",
    "animation": "animation.mp4",
    "audio": "audio.mp3",
    "voice": "voice.ogg",
    "video_note": "video_note.mp4",
    "sticker": "sticker.webp",
}


def clean_user_html(text: Optional[str]) -> str:
    """Strip <tg-emoji> premium tags — user account plain emoji bhejta hai."""
    if not text:
        return ""
    return _TG_EMOJI_RE.sub("", text)


def markup_to_telethon_buttons(markup) -> Optional[List[List[Any]]]:
    """
    PTB InlineKeyboardMarkup -> Telethon URL buttons.

    User accounts callback buttons nahi bhej sakte, isliye sirf URL buttons
    rakhe jate hain. Agar koi URL button nahi mila to None return hota hai.
    """
    if not markup:
        return None
    rows: List[List[Any]] = []
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
    """Real user account ke through messages bhejta hai."""

    def __init__(self) -> None:
        self.client: Optional[TelegramClient] = None
        self._media_cache: "OrderedDict[str, dict]" = OrderedDict()

    # ── lifecycle ──────────────────────────────────────────────────────────
    def configured(self) -> bool:
        return bool(USERBOT_SESSION)

    def available(self) -> bool:
        return self.client is not None and self.client.is_connected()

    async def start(self) -> bool:
        if not self.configured():
            log.warning(
                "USERBOT_SESSION set nahi hai — user-account delivery DISABLED "
                "(bot fallback use hoga). Setup ke liye: python3 login_userbot.py"
            )
            return False
        if self.available():
            return True
        try:
            self.client = TelegramClient(StringSession(USERBOT_SESSION), API_ID, API_HASH)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                log.error("USERBOT_SESSION invalid/expired — user-account delivery OFF.")
                await self.client.disconnect()
                self.client = None
                return False
            me = await self.client.get_me()
            log.info(
                "User-account sender online: %s (id=%s)",
                getattr(me, "username", None) or me.first_name,
                me.id,
            )
            return True
        except Exception as ex:
            log.error("User-account sender start failed: %s", ex)
            self.client = None
            return False

    async def stop(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    # ── internals ──────────────────────────────────────────────────────────
    async def _retry_flood(self, do, description: str):
        for attempt in range(3):
            try:
                return await do()
            except FloodWaitError as e:
                wait = min(int(e.seconds) + 1, FLOOD_SLEEP_CAP)
                log.warning("FloodWait %ss during %s (attempt %d)", wait, description, attempt + 1)
                await asyncio.sleep(wait)
            except (
                UserIsBlockedError,
                InputUserDeactivatedError,
                ChatWriteForbiddenError,
                UserPrivacyRestrictedError,
                PeerFloodError,
            ) as e:
                log.warning("Cannot deliver %s via user account: %s", description, type(e).__name__)
                return None
            except RPCError as e:
                log.warning("RPC error during %s: %s", description, e)
                return None
        return None

    async def _get_media_bytes(self, media_id: str, file_name: Optional[str], media_type: Optional[str], bot):
        """Bot file_id -> bytes (bot se download, cache me store)."""
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
    def _as_file_obj(data: bytes, name: str):
        f = io.BytesIO(data)
        f.name = name
        return f

    # ── public delivery API ────────────────────────────────────────────────
    async def send_text(self, user_id: int, text: str, markup=None) -> Optional[int]:
        """Text message user-account se. Return: message_id ya None."""
        if not self.available():
            return None
        html = clean_user_html(text)
        if not html.strip():
            return None
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            try:
                return await self.client.send_message(
                    user_id, html, buttons=buttons, parse_mode="html", link_preview=False
                )
            except ValueError:
                # HTML parse fail -> plain text
                return await self.client.send_message(
                    user_id, html, buttons=buttons, parse_mode=None, link_preview=False
                )

        msg = await self._retry_flood(_do, f"text to {user_id}")
        await asyncio.sleep(SEND_GAP)
        return msg.message_id if msg else None

    async def send_media(
        self,
        user_id: int,
        media_id: str,
        media_type: str,
        text: str = "",
        markup=None,
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        bot=None,
    ) -> Optional[int]:
        """Media user-account se (bot se download karke re-upload)."""
        if not self.available() or not media_id or bot is None:
            return None
        data, name = await self._get_media_bytes(media_id, file_name, media_type, bot)
        if not data:
            return None
        html = clean_user_html(text)
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            f = self._as_file_obj(data, name)
            try:
                return await self.client.send_file(
                    user_id,
                    f,
                    caption=html or None,
                    buttons=buttons,
                    parse_mode="html" if html else None,
                )
            except ValueError:
                f.seek(0)
                return await self.client.send_file(
                    user_id,
                    f,
                    caption=html or None,
                    buttons=buttons,
                    parse_mode=None,
                )

        msg = await self._retry_flood(_do, f"media to {user_id}")
        await asyncio.sleep(SEND_GAP)
        return msg.message_id if msg else None

    async def send_media_group(
        self,
        user_id: int,
        items: List[dict],
        bot,
        caption: Optional[str] = None,
        markup=None,
    ) -> Optional[List[int]]:
        """Album user-account se. items: [{media_id, media_type, file_name}]"""
        if not self.available() or not items or bot is None:
            return None
        files = []
        for it in items:
            data, name = await self._get_media_bytes(
                it.get("media_id"), it.get("file_name"), it.get("media_type"), bot
            )
            if data:
                files.append(self._as_file_obj(data, name))
        if not files:
            return None
        html = clean_user_html(caption)
        buttons = markup_to_telethon_buttons(markup)

        async def _do():
            try:
                return await self.client.send_file(
                    user_id,
                    files,
                    caption=html or None,
                    buttons=buttons,
                    parse_mode="html" if html else None,
                )
            except ValueError:
                for f in files:
                    f.seek(0)
                return await self.client.send_file(
                    user_id,
                    files,
                    caption=html or None,
                    buttons=buttons,
                    parse_mode=None,
                )

        msgs = await self._retry_flood(_do, f"album to {user_id}")
        await asyncio.sleep(SEND_GAP)
        if not msgs:
            return None
        if not isinstance(msgs, list):
            msgs = [msgs]
        return [m.message_id for m in msgs if m is not None]

    async def delete_message(self, user_id: int, message_id: int) -> bool:
        if not self.available():
            return False
        try:
            await self.client.delete_messages(user_id, [message_id])
            return True
        except Exception as ex:
            log.warning("user-account delete failed (%s/%s): %s", user_id, message_id, ex)
            return False


# Module-level singleton — advanced.py isi ko use karta hai
user_sender = UserAccountSender()
