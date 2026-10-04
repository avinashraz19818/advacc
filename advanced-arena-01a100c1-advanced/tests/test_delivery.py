"""
tests/test_delivery.py — User-account-only delivery routing tests.

Verify karta hai ki recipient DMs sirf USER ACCOUNT se jaate hain. User account
unavailable/fail ho to delivery fail hoti hai; Bot API fallback nahi hota.
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
        self.primed = []

    def available(self, session=None, bot=None):
        return self._available

    async def prime_requester(self, channel_id, user_id, **kw):
        self.primed.append((channel_id, user_id))
        return self._available

    async def send_text(self, user_id, text, markup=None, **kw):
        self.calls.append(("send_text", user_id, text))
        return None if self.fail else 42

    async def send_media(self, user_id, media_id, media_type, text="", markup=None,
                         file_name=None, mime_type=None, bot=None):
        self.calls.append(("send_media", user_id, media_id, media_type))
        return None if self.fail else 43

    async def send_media_group(self, user_id, items, bot, caption=None, markup=None):
        self.calls.append(("send_media_group", user_id, [i.get("media_id") for i in items]))
        return None if self.fail else [51, 52]

    async def delete_message(self, user_id, message_id, **kw):
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

    def test_no_bot_fallback_when_user_account_off(self):
        fake = FakeUserSender(available=False)
        advanced.user_account = fake
        mid = run(advanced.deliver_text(55, "hello there", self.bot))
        self.assertIsNone(mid)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.bot.calls, [])

    def test_no_bot_fallback_when_user_account_fails(self):
        fake = FakeUserSender(available=True, fail=True)
        advanced.user_account = fake
        mid = run(advanced.deliver_text(55, "hello there", self.bot))
        self.assertIsNone(mid)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.bot.calls, [])

    def test_user_account_flag_disabled_does_not_send(self):
        fake = FakeUserSender(available=True)
        advanced.user_account = fake
        mid = run(advanced.deliver_text(55, "hello", self.bot, use_user_account=False))
        self.assertIsNone(mid)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.bot.calls, [])


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

    def test_media_does_not_fall_back_to_bot_when_user_account_off(self):
        fake = FakeUserSender(available=False)
        advanced.user_account = fake
        ok = run(advanced.deliver_media(55, "FILEID", "photo", "cap", self.bot))
        self.assertFalse(ok)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.bot.calls, [])

    def test_media_does_not_fall_back_to_bot_when_user_account_fails(self):
        fake = FakeUserSender(available=True, fail=True)
        advanced.user_account = fake
        ok = run(advanced.deliver_media(55, "FILEID", "photo", "cap", self.bot))
        self.assertFalse(ok)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.bot.calls, [])

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

    def test_does_not_delete_user_account_message_via_bot_api(self):
        import mongomock
        advanced.init_database(mongomock.MongoClient())
        advanced.db.add_leave_recovery_message("b1", 5, -1001, -1009, 777)
        fake = FakeUserSender(available=False)
        advanced.user_account = fake
        deleted = run(advanced.delete_pending_leave_recovery_messages("b1", 5, -1009, self.bot))
        self.assertEqual(deleted, 0)
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(len(advanced.db.get_pending_leave_recovery_messages("b1", 5, -1009)), 1)


class TestUserAccountSessionRouting(unittest.TestCase):
    class BotIdentity:
        def __init__(self, bot_id):
            self.id = bot_id

    def test_routes_to_exact_per_bot_session(self):
        from user_sender import UserAccountSender

        sender = UserAccountSender()
        sender.register_session("owner_a_1", "session-a", tg_bot_id=111)
        sender.register_session("owner_b_1", "session-b", tg_bot_id=222)
        self.assertEqual(sender.session_for(self.BotIdentity(222)), "session-b")
        self.assertTrue(sender.available(bot=self.BotIdentity(111)))

    def test_ambiguous_bot_mapping_fails_closed(self):
        from user_sender import UserAccountSender

        sender = UserAccountSender()
        sender.register_session("owner_a_1", "session-a", tg_bot_id=111)
        sender.register_session("owner_b_1", "session-b", tg_bot_id=222)
        sender.client = type("Connected", (), {"is_connected": lambda self: True})()
        unknown_bot = self.BotIdentity(333)
        self.assertIsNone(sender.session_for(unknown_bot))
        self.assertFalse(sender.available(bot=unknown_bot))
        self.assertIsNone(run(sender._resolve(bot=unknown_bot)))

    def test_unknown_bot_does_not_borrow_only_explicitly_mapped_session(self):
        from user_sender import UserAccountSender

        sender = UserAccountSender()
        sender.register_session("owner_a_1", "session-a", tg_bot_id=111)
        unknown_bot = self.BotIdentity(333)
        self.assertIsNone(sender.session_for(unknown_bot))
        self.assertFalse(sender.available(bot=unknown_bot))

    def test_media_group_uses_the_matching_per_bot_session(self):
        from types import SimpleNamespace
        from user_sender import UserAccountSender

        class UploadBot:
            id = 222

            async def get_file(self, file_id):
                class File:
                    async def download_to_memory(self, out):
                        out.write(file_id.encode())
                return File()

        class Client:
            def __init__(self):
                self.sent_to = None
                self.calls = 0

            def is_connected(self):
                return True

            async def send_file(self, recipient, files, **kwargs):
                self.sent_to = recipient
                self.calls += 1
                return [SimpleNamespace(id=11), SimpleNamespace(id=12)]

        sender = UserAccountSender()
        client = Client()
        sender.register_session("owner_b_1", "session-b", tg_bot_id=222)
        sender._clients["session-b"] = client

        ids = run(sender.send_media_group(
            55, [{"media_id": "FILE-A", "media_type": "photo"}], UploadBot()))

        self.assertEqual(ids, [11, 12])
        self.assertEqual(client.sent_to, 55)
        self.assertEqual(client.calls, 1)

    def test_primed_entity_cache_is_scoped_to_its_user_account(self):
        from user_sender import UserAccountSender

        sender = UserAccountSender()
        account_a = object()
        account_b = object()
        entity_a = object()
        sender._primed_by_client[id(account_a)] = {55: entity_a}

        self.assertIs(sender._recipient_for_client(account_a, 55), entity_a)
        self.assertEqual(sender._recipient_for_client(account_b, 55), 55)

    def test_pending_request_entity_is_retried_if_mtproto_list_lags(self):
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from user_sender import UserAccountSender

        class Session:
            def __init__(self):
                self.cached = []

            def cache_entity(self, user):
                self.cached.append(user)

        class Client:
            def __init__(self):
                self.session = Session()
                self.calls = 0

            def is_connected(self):
                return True

            async def get_input_entity(self, entity_id):
                if entity_id == 55:
                    raise ValueError("user not cached yet")
                return "channel-peer"

            async def __call__(self, _request):
                self.calls += 1
                if self.calls == 1:
                    return SimpleNamespace(users=[], importers=[])
                user = SimpleNamespace(id=55, access_hash=987)
                importer = SimpleNamespace(user_id=55, date=datetime.utcnow())
                return SimpleNamespace(users=[user], importers=[importer])

        sender = UserAccountSender()
        client = Client()
        sender.register_session("owner_a_1", "session-a", tg_bot_id=111)
        sender._clients["session-a"] = client
        with patch("user_sender.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            found = run(sender.prime_requester(-1001, 55, bot=self.BotIdentity(111)))

        self.assertTrue(found)
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.session.cached[0].id, 55)
        sleep_mock.assert_awaited_once_with(0.35)

    def test_warm_session_connect_is_singleton_under_concurrency(self):
        from unittest.mock import patch
        from user_sender import UserAccountSender

        calls = []

        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                self.connected = False
                calls.append(self)

            async def connect(self):
                await asyncio.sleep(0)
                self.connected = True

            def is_connected(self):
                return self.connected

            async def is_user_authorized(self):
                return True

            async def disconnect(self):
                self.connected = False

        sender = UserAccountSender()
        with patch("user_sender.TelegramClient", FakeClient), \
             patch("user_sender.StringSession", side_effect=lambda value: value):
            async def run_concurrent():
                return await asyncio.gather(sender.get_client("session-a"), sender.get_client("session-a"))

            clients = run(run_concurrent())

        self.assertEqual(len(calls), 1)
        self.assertIs(clients[0], clients[1])

    def test_transient_transport_error_retries(self):
        from unittest.mock import AsyncMock, patch
        from user_sender import UserAccountSender

        sender = UserAccountSender()
        calls = 0

        async def do_send():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("temporary disconnect")
            return "sent"

        with patch("user_sender.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = run(sender._retry_flood(do_send, "test DM"))

        self.assertEqual(result, "sent")
        self.assertEqual(calls, 2)
        sleep_mock.assert_awaited_once_with(0.5)

    def test_flood_wait_is_not_capped_shorter_than_telegram_requirement(self):
        from unittest.mock import AsyncMock, patch
        from telethon.errors import FloodWaitError
        from user_sender import UserAccountSender

        sender = UserAccountSender()
        calls = 0

        async def do_send():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise FloodWaitError(request=None, capture=90)
            return "sent"

        with patch("user_sender.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            result = run(sender._retry_flood(do_send, "test DM"))

        self.assertEqual(result, "sent")
        sleep_mock.assert_awaited_once_with(91)


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
