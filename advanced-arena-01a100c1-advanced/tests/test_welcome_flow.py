"""
tests/test_welcome_flow.py — Integration test: welcome message flow.

Scenario (user ki requirement): "koi bhi channel me req dalega to usko
message useraccount se jayega" — yani saved welcome messages USER ACCOUNT se
DM hone chahiye, bot se nahi.

Ye test full _send_messages_with_media_groups() flow ko fake user-account +
mongomock DB ke saath chalata hai aur verify karta hai ki delivery user
account se hui.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock

import advanced

try:
    from tests.test_delivery import FakeBot, FakeUserSender
except ImportError:
    from test_delivery import FakeBot, FakeUserSender


class FakeContext:
    def __init__(self, bot):
        self.bot = bot


class TestWelcomeFlowViaUserAccount(unittest.TestCase):
    def setUp(self):
        self._old_ua = advanced.user_account
        self._old_db = advanced.db
        advanced.init_database(mongomock.MongoClient())
        self.bot = FakeBot()
        self.context = FakeContext(self.bot)
        self.fake_ua = FakeUserSender(available=True)
        advanced.user_account = self.fake_ua

    def tearDown(self):
        advanced.user_account = self._old_ua
        advanced.db = self._old_db

    def test_saved_welcome_messages_go_from_user_account(self):
        b1 = advanced.db.add_user_bot(1, "tok", "clientbot")
        advanced.db.add_channel(b1, -1001, "pub", "Public")
        advanced.db.add_message(b1, -1001, "Welcome {first_name}!", None, None)
        advanced.db.add_message(b1, -1001, "Ye video dekho", "FILEID1", "video")
        msgs = advanced.db.get_messages(-1001, b1)

        asyncio.run(advanced._send_messages_with_media_groups(
            55, msgs, self.context, bot_id=b1,
            attach_start_button=True, placeholder_user=None))

        # Text message user account se gayi
        self.assertEqual(self.fake_ua.calls[0][0], "send_text")
        self.assertIn("Welcome User!", self.fake_ua.calls[0][2])
        # Media bhi user account se gayi (bot se download + re-upload hoga)
        self.assertEqual(self.fake_ua.calls[1][0], "send_media")
        self.assertEqual(self.fake_ua.calls[1][2], "FILEID1")
        # Bot se koi user-facing message NAHI gaya (sirf fallback hota)
        user_facing = [c for c in self.bot.calls if c[0].startswith("send_")]
        self.assertEqual(user_facing, [], "bot se message nahi jana chahiye")

    def test_media_group_album_from_user_account(self):
        b1 = advanced.db.add_user_bot(1, "tok", "clientbot")
        advanced.db.add_channel(b1, -1001, "pub", "Public")
        advanced.db.add_message(b1, -1001, "Album pic 1", "M1", "photo", media_group_id="g1")
        advanced.db.add_message(b1, -1001, "Album pic 2", "M2", "photo", media_group_id="g1")
        msgs = advanced.db.get_messages(-1001, b1)

        asyncio.run(advanced._send_messages_with_media_groups(
            55, msgs, self.context, bot_id=b1, attach_start_button=True))

        kinds = [c[0] for c in self.fake_ua.calls]
        self.assertIn("send_media_group", kinds)     # album user account se
        self.assertEqual([c for c in self.bot.calls if c[0].startswith("send_")], [])

    def test_fallback_to_bot_when_user_account_off(self):
        advanced.user_account = FakeUserSender(available=False)
        b1 = advanced.db.add_user_bot(1, "tok", "clientbot")
        advanced.db.add_channel(b1, -1001, "pub", "Public")
        advanced.db.add_message(b1, -1001, "Hello there", None, None)
        msgs = advanced.db.get_messages(-1001, b1)

        asyncio.run(advanced._send_messages_with_media_groups(
            55, msgs, self.context, bot_id=b1, attach_start_button=True))

        self.assertTrue(any(c[0] == "send_message" for c in self.bot.calls))

    def test_media_download_uses_bot_then_user_account_sends(self):
        """Media file_id bot ka hota hai: download bot se, bhejna user account se."""

        class DownloadFakeBot(FakeBot):
            async def get_file(self, file_id):
                class F:
                    async def download_to_memory(self, out):
                        out.write(b"FAKEBYTES")
                return F()

        bot = DownloadFakeBot()
        ctx = FakeContext(bot)
        b1 = advanced.db.add_user_bot(1, "tok", "clientbot")
        advanced.db.add_channel(b1, -1001, "pub", "Public")
        advanced.db.add_message(b1, -1001, "Doc caption", "DOCID", "document",
                                file_name="report.pdf")
        msgs = advanced.db.get_messages(-1001, b1)

        asyncio.run(advanced._send_messages_with_media_groups(
            55, msgs, ctx, bot_id=b1, attach_start_button=True))

        # User-account sender ko media mili (download hoke)
        self.assertEqual(self.fake_ua.calls[0][0], "send_media")
        self.assertEqual(self.fake_ua.calls[0][2], "DOCID")
        # User-facing send bot se NAHI hua
        self.assertEqual([c for c in bot.calls if c[0].startswith("send_")], [])


class TestUserAccountMediaPipeline(unittest.TestCase):
    """Real UserAccountSender: bot se download -> bytes cache -> user client se send."""

    def test_download_from_bot_upload_via_user_account(self):
        from user_sender import UserAccountSender

        class FakeTelethonClient:
            def __init__(self):
                self.sent = []

            def is_connected(self):
                return True

            async def send_file(self, user_id, f, caption=None, buttons=None, parse_mode=None):
                self.sent.append({"user_id": user_id, "name": f.name,
                                  "data": f.read(), "caption": caption})
                class M:  # noqa
                    message_id = 9
                return M()

        class DLBot:
            def __init__(self):
                self.downloads = 0

            async def get_file(self, file_id):
                outer = self

                class F:
                    async def download_to_memory(self, out):
                        outer.downloads += 1
                        out.write(b"PDFBYTES")

                return F()

        sender = UserAccountSender()
        sender.client = FakeTelethonClient()
        bot = DLBot()

        mid = asyncio.run(sender.send_media(
            55, "DOCID", "document", "caption", None, "report.pdf", None, bot))
        self.assertEqual(mid, 9)
        self.assertEqual(len(sender.client.sent), 1)
        self.assertEqual(sender.client.sent[0]["name"], "report.pdf")
        self.assertEqual(sender.client.sent[0]["data"], b"PDFBYTES")
        self.assertEqual(sender.client.sent[0]["caption"], "caption")

        # Doosri baar same media -> cache se, dubara download NAHI
        asyncio.run(sender.send_media(
            66, "DOCID", "document", "", None, "report.pdf", None, bot))
        self.assertEqual(bot.downloads, 1)
        self.assertEqual(len(sender.client.sent), 2)
        self.assertEqual(sender.client.sent[1]["user_id"], 66)

    def test_send_text_strips_premium_emoji_tags(self):
        from user_sender import UserAccountSender

        class FakeTelethonClient:
            def __init__(self):
                self.sent = []

            def is_connected(self):
                return True

            async def send_message(self, user_id, text, **kw):
                self.sent.append((user_id, text, kw.get("parse_mode")))
                class M:  # noqa
                    message_id = 7
                return M()

        sender = UserAccountSender()
        sender.client = FakeTelethonClient()
        mid = asyncio.run(sender.send_text(
            55, '<tg-emoji emoji-id="1">🔥</tg-emoji> Hello <b>World</b>'))
        self.assertEqual(mid, 7)
        self.assertEqual(sender.client.sent[0][1], "🔥 Hello <b>World</b>")
        self.assertEqual(sender.client.sent[0][2], "html")


if __name__ == "__main__":
    unittest.main()
