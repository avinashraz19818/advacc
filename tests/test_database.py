"""
tests/test_database.py — MongoDB Database layer ke tests (mongomock ke saath).

Ye verify karte hain ki naya MongoDB database layer sahi kaam karta hai:
users, bots, subscriptions, channels, messages, join requests, reachability,
settings, emoji maps, leave-recovery — sab kuch.
"""

import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock

from advanced import Database, now_aware


def make_db() -> Database:
    return Database(client=mongomock.MongoClient())


class TestUsers(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_add_and_get_user(self):
        self.db.add_user(101, "alice", "Alice", "Sharma")
        u = self.db.get_user(101)
        self.assertEqual(u["username"], "alice")
        self.assertEqual(u["first_name"], "Alice")
        self.assertEqual(u["last_name"], "Sharma")
        self.assertFalse(u["verified"])

    def test_add_user_keeps_old_values_when_none(self):
        self.db.add_user(101, "alice", "Alice", "Sharma")
        self.db.add_user(101, None, None, "Verma")   # None fields preserve old
        u = self.db.get_user(101)
        self.assertEqual(u["username"], "alice")
        self.assertEqual(u["first_name"], "Alice")
        self.assertEqual(u["last_name"], "Verma")

    def test_get_missing_user_returns_empty_dict(self):
        self.assertEqual(self.db.get_user(999), {})

    def test_verification(self):
        self.db.add_user(101, "alice", "Alice", None)
        self.assertFalse(self.db.is_user_verified(101))
        self.db.mark_user_verified(101)
        self.assertTrue(self.db.is_user_verified(101))

    def test_get_all_users_sorted(self):
        self.db.add_user(300, "c", "C", None)
        self.db.add_user(100, "a", "A", None)
        ids = [u["user_id"] for u in self.db.get_all_users()]
        self.assertEqual(ids, [100, 300])


class TestUserBots(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_bot_id_increments_per_user(self):
        b1 = self.db.add_user_bot(1, "token1", "botone")
        b2 = self.db.add_user_bot(1, "token2", "bottwo")
        self.assertEqual(b1, "1_1")
        self.assertEqual(b2, "1_2")

    def test_add_user_bot_creates_owner_user(self):
        self.db.add_user_bot(5, "tok", "mybot")
        u = self.db.get_user(5)
        self.assertEqual(u["first_name"], "User5")

    def test_token_conflict_updates_username_keeps_id(self):
        b1 = self.db.add_user_bot(1, "token1", "oldname")
        b2 = self.db.add_user_bot(1, "token1", "newname")
        self.assertEqual(b1, b2)
        bot = self.db.get_user_bot(b1)
        self.assertEqual(bot["bot_username"], "newname")
        self.assertEqual(bot["is_active"], 1)

    def test_getters(self):
        b1 = self.db.add_user_bot(1, "t1", "botone")
        self.db.add_user_bot(2, "t2", "bottwo")
        self.assertEqual(len(self.db.get_user_bots_by_owner(1)), 1)
        self.assertEqual(len(self.db.get_all_user_bots()), 2)
        self.assertEqual(self.db.get_bot_by_username("botone")["bot_id"], b1)
        self.assertIsNone(self.db.get_user_bot("nope"))

    def test_set_active(self):
        b1 = self.db.add_user_bot(1, "t1", "botone")
        self.db.set_user_bot_active(b1, False)
        self.assertEqual(self.db.get_user_bot(b1)["is_active"], 0)
        self.db.set_user_bot_active(b1, True)
        self.assertEqual(self.db.get_user_bot(b1)["is_active"], 1)

    def test_persist_telegram_bot_id_for_session_routing(self):
        b1 = self.db.add_user_bot(1, "t1", "botone")
        self.db.set_user_bot_telegram_id(b1, 12345)
        self.assertEqual(self.db.get_user_bot(b1)["tg_bot_id"], 12345)

    def test_remove_user_bot_cascades(self):
        b1 = self.db.add_user_bot(1, "t1", "botone")
        self.db.add_subscription_for_bot(b1, "Basic", 30)
        self.db.add_channel(b1, -100, "ch", "Chan")
        mid = self.db.add_message(b1, -100, "hello", None, None)
        self.db.save_user_emoji_map(b1, mid, {"🔥": "1"})
        self.db.add_join_request(b1, 77, -100, "pending")
        self.db.mark_reachable(b1, 77)

        self.db.remove_user_bot(b1)

        self.assertIsNone(self.db.get_user_bot(b1))
        self.assertIsNone(self.db.get_subscription_for_bot(b1))
        self.assertEqual(self.db.get_bot_channels(b1), [])
        self.assertEqual(self.db.get_messages(-100, b1), [])
        self.assertEqual(self.db.get_pending_requests(b1), [])
        self.assertEqual(self.db.get_requesters_for_bot(b1), [])
        self.assertEqual(self.db.get_user_emoji_map(b1, mid), {})


class TestSubscriptions(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.b1 = self.db.add_user_bot(1, "t1", "botone")

    def test_add_subscription_basic(self):
        self.db.add_subscription_for_bot(self.b1, "Basic", 30)
        s = self.db.get_subscription_for_bot(self.b1)
        self.assertEqual(s["subscription_type"], "Basic")
        self.assertEqual(s["max_channels"], 1)
        self.assertGreater(s["expiry_date"], now_aware())

    def test_add_subscription_pro_max_channels(self):
        self.db.add_subscription_for_bot(self.b1, "Pro", 30)
        s = self.db.get_subscription_for_bot(self.b1)
        self.assertEqual(s["max_channels"], 5)

    def test_latest_subscription_wins(self):
        self.db.add_subscription_for_bot(self.b1, "Basic", 1)
        self.db.add_subscription_for_bot(self.b1, "Pro", 30)
        s = self.db.get_subscription_for_bot(self.b1)
        self.assertEqual(s["subscription_type"], "Pro")

    def test_update_subscription_expiry(self):
        self.db.add_subscription_for_bot(self.b1, "Basic", 30)
        s1 = self.db.get_subscription_for_bot(self.b1)
        new_exp = now_aware() + timedelta(days=60)
        self.db.update_subscription_expiry(self.b1, new_exp)
        s2 = self.db.get_subscription_for_bot(self.b1)
        self.assertEqual(s2["id"], s1["id"])
        self.assertAlmostEqual(s2["expiry_date"], new_exp, delta=timedelta(seconds=2))

    def test_expiring_subscriptions(self):
        self.db.add_subscription_for_bot(self.b1, "Basic", 2)  # 2 days -> in 3d window
        rows = self.db.get_expiring_subscriptions(3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bot_id"], self.b1)
        # 1d window me nahi hona chahiye (2 days left)
        self.assertEqual(self.db.get_expiring_subscriptions(1), [])

    def test_reminder_flags(self):
        self.db.add_subscription_for_bot(self.b1, "Basic", 2)
        self.db.mark_reminder_sent(self.b1, 3)
        self.assertEqual(self.db.get_expiring_subscriptions(3), [])
        self.assertEqual(self.db.get_expiring_subscriptions(1), [])

    def test_expired_subscriptions(self):
        b2 = self.db.add_user_bot(2, "t2", "bottwo")
        self.db.add_subscription_for_bot(self.b1, "Basic", -1)   # expired
        self.db.add_subscription_for_bot(b2, "Basic", 10)        # live
        expired = self.db.get_expired_subscriptions()
        self.assertIn(self.b1, expired)
        self.assertNotIn(b2, expired)

    def test_expired_requires_at_least_one_sub(self):
        b2 = self.db.add_user_bot(2, "t2", "bottwo")  # no subs at all
        self.db.add_subscription_for_bot(self.b1, "Basic", -1)
        expired = self.db.get_expired_subscriptions()
        self.assertEqual(expired, [self.b1])

    def test_get_all_subscriptions_join(self):
        self.db.add_user(1, "alice", "Alice", None)
        self.db.add_subscription_for_bot(self.b1, "Pro", 30)
        rows = self.db.get_all_subscriptions()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["bot_username"], "botone")
        self.assertEqual(r["user_id"], 1)
        self.assertEqual(r["owner_username"], "alice")
        self.assertIn("bot_active", r)


class TestChannelsAndMessages(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.b1 = self.db.add_user_bot(1, "t1", "botone")

    def test_add_and_get_channels(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        self.db.add_channel(self.b1, -1002, None, "Private")
        chans = self.db.get_bot_channels(self.b1)
        self.assertEqual([c["channel_id"] for c in chans], [-1002, -1001])
        self.assertEqual(chans[1]["channel_title"], "Public")

    def test_channel_upsert_updates_title(self):
        self.db.add_channel(self.b1, -1001, "pub", "Old")
        self.db.add_channel(self.b1, -1001, "pub", "New")
        chans = self.db.get_bot_channels(self.b1)
        self.assertEqual(len(chans), 1)
        self.assertEqual(chans[0]["channel_title"], "New")

    def test_auto_approve(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        self.db.set_auto_approve(self.b1, -1001, True)
        d = self.db.get_channel_owner_data(-1001, self.b1)
        self.assertEqual(d["auto_approve"], 1)

    def test_get_channel_owner_data_without_bot_id(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        d = self.db.get_channel_owner_data(-1001)
        self.assertEqual(d["bot_id"], self.b1)

    def test_add_message_and_welcome_refresh(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        mid = self.db.add_message(self.b1, -1001, "welcome text", "media1", "photo")
        self.assertIsInstance(mid, int)
        d = self.db.get_channel_owner_data(-1001, self.b1)
        self.assertEqual(d["welcome_message"], "welcome text")
        self.assertEqual(d["welcome_media_id"], "media1")
        self.assertEqual(d["welcome_media_type"], "photo")

    def test_message_order_by_id(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        self.db.add_message(self.b1, -1001, "first", None, None)
        self.db.add_message(self.b1, -1001, "second", None, None)
        texts = [m["content_text"] for m in self.db.get_messages(-1001, self.b1)]
        self.assertEqual(texts, ["first", "second"])

    def test_update_message_text_and_media(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        mid = self.db.add_message(self.b1, -1001, "old", "m1", "photo")
        self.db.update_message_text(mid, "new text")
        m = self.db.get_message_by_id(mid)
        self.assertEqual(m["content_text"], "new text")
        self.db.update_message_media(mid, "m2", "video", "caption")
        m = self.db.get_message_by_id(mid)
        self.assertEqual(m["media_id"], "m2")
        self.assertEqual(m["media_type"], "video")
        self.assertEqual(m["content_text"], "caption")

    def test_delete_message_clears_welcome(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        mid = self.db.add_message(self.b1, -1001, "only", None, None)
        self.assertTrue(self.db.delete_message(mid))
        self.assertIsNone(self.db.get_message_by_id(mid))
        d = self.db.get_channel_owner_data(-1001, self.b1)
        self.assertIsNone(d["welcome_message"])
        self.assertFalse(self.db.delete_message(mid))

    def test_delete_by_telegram_id(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        self.db.add_message(self.b1, -1001, "x", None, None, telegram_message_id=555)
        self.assertTrue(self.db.delete_message_by_telegram_id(self.b1, 555))
        self.assertEqual(self.db.get_message_count(-1001, self.b1), 0)

    def test_media_group_delete(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        self.db.add_message(self.b1, -1001, "a", "m1", "photo", media_group_id="g1")
        self.db.add_message(self.b1, -1001, "b", "m2", "photo", media_group_id="g1")
        self.db.add_message(self.b1, -1001, "c", None, None)
        self.db.delete_media_group_messages(self.b1, "g1")
        msgs = self.db.get_messages(-1001, self.b1)
        self.assertEqual([m["content_text"] for m in msgs], ["c"])

    def test_buttons_update_and_append(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        mid = self.db.add_message(self.b1, -1001, "x", None, None)
        self.db.update_message_buttons(mid, '[[{"text": "A", "url": "https://a"}]]')
        self.db.append_message_buttons(mid, '[[{"text": "B", "url": "https://b"}]]')
        import json
        data = json.loads(self.db.get_message_by_id(mid)["buttons_json"])
        self.assertEqual(len(data), 2)

    def test_clear_messages(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        self.db.add_message(self.b1, -1001, "a", "m1", "photo")
        self.db.clear_messages(self.b1, -1001)
        self.assertEqual(self.db.get_messages(-1001, self.b1), [])
        d = self.db.get_channel_owner_data(-1001, self.b1)
        self.assertIsNone(d["welcome_media_id"])

    def test_remove_channel(self):
        self.db.add_channel(self.b1, -1001, "pub", "Public")
        self.db.add_message(self.b1, -1001, "a", None, None)
        self.db.add_join_request(self.b1, 5, -1001, "pending")
        self.db.remove_channel(self.b1, -1001)
        self.assertEqual(self.db.get_bot_channels(self.b1), [])
        self.assertEqual(self.db.get_pending_requests(self.b1), [])


class TestJoinRequests(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.b1 = self.db.add_user_bot(1, "t1", "botone")

    def test_add_and_pending(self):
        self.db.add_join_request(self.b1, 10, -1001, "pending")
        self.assertEqual(self.db.get_pending_count(self.b1), 1)
        rows = self.db.get_pending_requests(self.b1)
        self.assertEqual(rows[0]["requester_id"], 10)

    def test_approved_never_downgrades_to_pending(self):
        self.db.add_join_request(self.b1, 10, -1001, "approved")
        self.db.add_join_request(self.b1, 10, -1001, "pending")
        self.assertEqual(self.db.get_pending_count(self.b1), 0)

    def test_pending_can_upgrade_to_approved(self):
        self.db.add_join_request(self.b1, 10, -1001, "pending")
        self.db.add_join_request(self.b1, 10, -1001, "approved")
        self.assertEqual(self.db.get_pending_count(self.b1), 0)
        self.assertEqual(self.db.get_total_requesters_count(self.b1), 1)

    def test_mark_request_status(self):
        self.db.add_join_request(self.b1, 10, -1001, "pending")
        req = self.db.get_pending_requests(self.b1)[0]
        self.db.mark_request_status(req["id"], "approved")
        self.assertEqual(self.db.get_pending_count(self.b1), 0)

    def test_unique_requester_channel(self):
        self.db.add_join_request(self.b1, 10, -1001, "pending")
        self.db.add_join_request(self.b1, 10, -1001, "pending")
        self.assertEqual(self.db.get_total_requesters_count(self.b1), 1)


class TestReachability(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.b1 = self.db.add_user_bot(1, "t1", "botone")

    def test_mark_reachable_and_counts(self):
        self.db.add_join_request(self.b1, 10, -1001, "approved")
        self.db.mark_reachable(self.b1, 10)
        self.assertEqual(self.db.get_reachable_requesters_count(self.b1), 1)
        self.assertEqual(self.db.get_requesters_for_bot(self.b1), [10])
        self.db.mark_unreachable(self.b1, 10)
        self.assertEqual(self.db.get_reachable_requesters_count(self.b1), 0)

    def test_requesters_fallback_to_join_requests(self):
        self.db.add_join_request(self.b1, 10, -1001, "approved")
        self.db.add_join_request(self.b1, 11, -1001, "approved")
        self.db.add_join_request(self.b1, 12, -1001, "pending")
        reqs = self.db.get_requesters_for_bot(self.b1)
        self.assertEqual(sorted(reqs), [10, 11])

    def test_userbot_user_counts(self):
        self.db.add_join_request(self.b1, 10, -1001, "approved")
        self.db.add_join_request(self.b1, 11, -1001, "approved")
        self.db.add_subscription_for_bot(self.b1, "Pro", 30)
        rows = self.db.get_userbot_user_counts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["users"], 2)
        self.assertEqual(rows[0]["plan"], "Pro")
        self.assertFalse(rows[0]["expired"])


class TestSettingsAndEmoji(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_settings_roundtrip(self):
        self.assertIsNone(self.db.get_setting("x"))
        self.assertEqual(self.db.get_setting("x", {"d": 1}), {"d": 1})
        self.db.set_setting("x", {"a": 5})
        self.assertEqual(self.db.get_setting("x"), {"a": 5})
        self.db.set_setting("x", {"a": 6})
        self.assertEqual(self.db.get_setting("x"), {"a": 6})

    def test_leave_recovery_config_defaults(self):
        cfg = self.db.get_leave_recovery_config()
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["messages"], [])
        self.assertEqual(cfg["channel_configs"], {})

    def test_leave_recovery_config_roundtrip(self):
        cfg = {"enabled": True, "target_channel_id": -1009,
               "target_channel_link": "https://t.me/+x",
               "messages": [{"text": "hi", "buttons_json": ""}],
               "channel_configs": {"-1001": False}}
        self.db.set_leave_recovery_config(cfg)
        out = self.db.get_leave_recovery_config()
        self.assertTrue(out["enabled"])
        self.assertEqual(out["target_channel_id"], -1009)
        self.assertEqual(len(out["messages"]), 1)

    def test_default_first_message(self):
        self.assertIn("{first_name}", self.db.get_default_first_message())
        self.db.set_default_first_message("custom {first_name}")
        self.assertEqual(self.db.get_default_first_message(), "custom {first_name}")

    def test_emoji_map_roundtrip(self):
        self.db.save_user_emoji_map("b1", 1, {"🔥": "123"})
        self.assertEqual(self.db.get_user_emoji_map("b1", 1), {"🔥": "123"})
        self.db.save_user_emoji_map("b1", 1, {"⭐": "456"})
        self.assertEqual(self.db.get_user_emoji_map("b1", 1), {"⭐": "456"})
        self.db.delete_user_emoji_map("b1", 1)
        self.assertEqual(self.db.get_user_emoji_map("b1", 1), {})


class TestLeaveRecoveryMessages(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.b1 = self.db.add_user_bot(1, "t1", "botone")

    def test_lifecycle(self):
        self.db.add_leave_recovery_message(self.b1, 5, -1001, -1009, 777)
        rows = self.db.get_pending_leave_recovery_messages(self.b1, 5, -1009)
        self.assertEqual(len(rows), 1)
        row_id, message_id = rows[0]
        self.assertEqual(message_id, 777)
        self.db.mark_leave_recovery_deleted(row_id)
        self.assertEqual(self.db.get_pending_leave_recovery_messages(self.b1, 5, -1009), [])

    def test_clear_pending(self):
        self.db.add_leave_recovery_message(self.b1, 5, -1001, -1009, 1)
        self.db.add_leave_recovery_message(self.b1, 6, -1001, -1009, 2)
        self.db.clear_pending_leave_recovery()
        self.assertEqual(self.db.get_pending_leave_recovery_messages(self.b1, 5, -1009), [])
        self.assertEqual(self.db.get_pending_leave_recovery_messages(self.b1, 6, -1009), [])


if __name__ == "__main__":
    unittest.main()
