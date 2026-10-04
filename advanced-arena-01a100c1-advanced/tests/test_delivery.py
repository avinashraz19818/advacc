"""
tests/test_delivery.py — User-account delivery routing ke tests.

Verify karta hai ki user-facing messages USER ACCOUNT se jaate hain
(bot se nahi), aur user-account unavailable hone par bot fallback chalta hai.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import advanced
from user_sender import markup_to_telethon_buttons, clean_user_html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ── Fakes ──────────────────────────────────────────────────────────────────

class FakeMsg:
    def __init__(self, message_id=1):
        self.message_id = message_id


class FakeBot:
    """Records every call. Koi bhi 'bot se gaya message' yahan dikhega."""

    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id, text, **kw):
        self.calls.append(("send_message", chat_id, text))
        return FakeMsg(100)

    async def send_photo(self, chat_id, photo, **kw):
        self.calls.append(("send_photo", chat_id, photo))
        return FakeMsg(101)

    async def send_video(self, chat_id, video, **kw):
        self.calls.append(("send_video", chat_id, video))
        return FakeMsg(102)

    async def send_document(self, chat_id, document, **kw):
        self.calls.append(("send_document", chat_id, document))
        return FakeMsg(103)

    async def send_media_group(self, chat_id=None, media=None, **kw):
        self.calls.append(("send_media_group", chat_id, media))
        return []

    async def delete_message(self, chat_id=None, message_id=None, **kw):
        self.calls.append(("delete_message", chat_id, message_id))
        return True

    async def get_file(self, file_id):
        raise RuntimeError("network should not be touched in this test")


class FakeUserSender:
    """User-account sender ka fake — 'user account se gaya message' record karta hai."""

    def __init__(self, available=True, fail=False):
        self._available = available
        self.fail = fail
        self.calls = []

    def available(self):
        return self._available

    async def send_text(self, user_id, text, markup=None):
        self.calls.append(("send_text", user_id, text))
        return None if self.fail else 42

    async def send_media(self, user_id, media_id, media_type, text="", markup=None,
                         file_name=None, mime_type=None, bot=None):
        self.calls.append(("send_media", user_id, media_id, media_type))
        return None if self.fail else 43

    async def send_media_group(self, user_id, items, bot, caption=None, markup=None):
        self.calls.append(("send_media_group", user_id, [i.get("media_id") for i in items]))
        return None if self.fail else [51, 52]

    async def delete_message(self, user_id, message_id):
        self.calls.append(("delete_message", user_id, message_id))
        return not self.fail


def run(coro):
    return asyncio.run(coro)


class TestButtonConversion(unittest.TestCase):
    def test_url_buttons_kept_callback_dropped(self):
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Site", url="https://x.com"),
             InlineKeyboardButton("Cb", callback_data="cb_1")],
            [InlineKeyboardButton("OnlyCb", callback_data="cb_2")],
        ])
        rows = markup_to_telethon_buttons(markup)
        self.assertEqual(len(rows), 1)          # sirf ek row (jisme URL tha)
        self.assertEqual(len(rows[0]), 1)       # sirf URL button
        b = rows[0][0]
        url = getattr(b, "url", None) or getattr(getattr(b, "type", None), "url", None)
        self.assertEqual(url, "https://x.com")  # telethon version ke hisaab se attr alag ho sakta hai

    def test_none_markup(self):
        self.assertIsNone(markup_to_telethon_buttons(None))

    def test_all_callback_gives_none(self):
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("Cb", callback_data="x")]])
        self.assertIsNone(markup_to_telethon_buttons(markup))

    def test_clean_user_html_strips_premium_tags(self):
        text = '<tg-emoji emoji-id="123">🔥</tg-emoji> Hello <b>world</b>'
        self.assertEqual(clean_user_html(text), "🔥 Hello <b>world</b>")
        self.assertEqual(clean_user_html(None), "")


class TestDeliverText(unittest.TestCase):
    def setUp(self):
        self.bot = FakeBot()
        self._old = advanced.user_account

    def tearDown(self):
        advanced.user_account = self._old

    def test_uses_user_account_when_available(self):
        fake = FakeUserSender(available=True)
        advanced.user_account = fake
        mid = run(advanced.deliver_text(55, "hello there", self.bot))
        self.assertEqual(mid, 42)
        self.assertEqual(len(fake.calls), 1)          # user account se gaya
        self.assertEqual(self.bot.calls, [])          # bot se NAHI gaya

    def test_bot_fallback_when_user_account_off(self):
        fake = FakeUserSender(available=False)
        advanced.user_account = fake
        mid = run(advanced.deliver_text(55, "hello there", self.bot))
        self.assertEqual(mid, 100)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.bot.calls[0][0], "send_message")

    def test_bot_fallback_when_user_account_fails(self):
        fake = FakeUserSender(available=True, fail=True)
        advanced.user_account = fake
        mid = run(advanced.deliver_text(55, "hello there", self.bot))
        self.assertEqual(mid, 100)                    # fallback ne deliver kiya
        self.assertEqual(len(fake.calls), 1)          # pehle user-account try hua
        self.assertEqual(self.bot.calls[0][0], "send_message")

    def test_force_bot_flag(self):
        fake = FakeUserSender(available=True)
        advanced.user_account = fake
        mid = run(advanced.deliver_text(55, "hello", self.bot, use_user_account=False))
        self.assertEqual(mid, 100)
        self.assertEqual(fake.calls, [])


class TestDeliverMedia(unittest.TestCase):
    def setUp(self):
        self.bot = FakeBot()
        self._old = advanced.user_account

    def tearDown(self):
        advanced.user_account = self._old

    def test_media_via_user_account(self):
        fake = FakeUserSender(available=True)
        advanced.user_account = fake
        ok = run(advanced.deliver_media(55, "FILEID", "photo", "cap", self.bot))
        self.assertTrue(ok)
        self.assertEqual(fake.calls[0], ("send_media", 55, "FILEID", "photo"))
        self.assertEqual(self.bot.calls, [])          # bot se NAHI gaya

    def test_media_bot_fallback(self):
        fake = FakeUserSender(available=False)
        advanced.user_account = fake
        ok = run(advanced.deliver_media(55, "FILEID", "photo", "cap", self.bot))
        self.assertTrue(ok)
        self.assertEqual(self.bot.calls[0][0], "send_photo")

    def test_media_user_account_failure_falls_back_to_bot(self):
        fake = FakeUserSender(available=True, fail=True)
        advanced.user_account = fake
        ok = run(advanced.deliver_media(55, "FILEID", "photo", "cap", self.bot))
        self.assertTrue(ok)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.bot.calls[0][0], "send_photo")

    def test_text_only_row_uses_user_account(self):
        fake = FakeUserSender(available=True)
        advanced.user_account = fake
        ok = run(advanced.deliver_media(55, None, "text", "just text", self.bot))
        self.assertTrue(ok)
        self.assertEqual(fake.calls[0][0], "send_text")   # text bhi user account se
        self.assertEqual(self.bot.calls, [])


class TestLeaveRecoveryDelete(unittest.TestCase):
    def setUp(self):
        self.bot = FakeBot()
        self._old = advanced.user_account
        self._old_db = advanced.db

    def tearDown(self):
        advanced.user_account = self._old
        advanced.db = self._old_db

    def test_delete_via_user_account_first(self):
        import mongomock
        advanced.init_database(mongomock.MongoClient())
        advanced.db.add_leave_recovery_message("b1", 5, -1001, -1009, 777)
        fake = FakeUserSender(available=True)
        advanced.user_account = fake
        deleted = run(advanced.delete_pending_leave_recovery_messages("b1", 5, -1009, self.bot))
        self.assertEqual(deleted, 1)
        self.assertEqual(fake.calls[0], ("delete_message", 5, 777))
        self.assertEqual(self.bot.calls, [])              # bot se delete NAHI hua

    def test_delete_bot_fallback(self):
        import mongomock
        advanced.init_database(mongomock.MongoClient())
        advanced.db.add_leave_recovery_message("b1", 5, -1001, -1009, 777)
        fake = FakeUserSender(available=False)
        advanced.user_account = fake
        deleted = run(advanced.delete_pending_leave_recovery_messages("b1", 5, -1009, self.bot))
        self.assertEqual(deleted, 1)
        self.assertEqual(self.bot.calls[0][0], "delete_message")


class TestMongoConfig(unittest.TestCase):
    def test_database_class_is_mongo(self):
        """New setup me Database class MongoDB use karta hai (PostgreSQL nahi)."""
        import inspect
        import advanced as a
        src = inspect.getsource(a.Database)
        self.assertIn("pymongo", src)
        self.assertNotIn("psycopg", src)

    def test_init_database_injectable(self):
        import mongomock
        db = advanced.init_database(mongomock.MongoClient())
        self.assertIs(advanced.db, db)
        db.add_user(1, "u", "U", None)
        self.assertEqual(db.get_user(1)["username"], "u")


if __name__ == "__main__":
    unittest.main()
