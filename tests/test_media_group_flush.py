"""Regression tests for Telegram album ingress and delayed flush jobs."""

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock

import advanced


class FakeJob:
    def __init__(self):
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when, **kwargs):
        job = FakeJob()
        self.jobs.append({"callback": callback, "when": when, "kwargs": kwargs, "job": job})
        return job


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=900)


class AlbumMessage:
    def __init__(self, message_id, file_id, media_group_id="album-1"):
        self.message_id = message_id
        self.chat_id = 55
        self.media_group_id = media_group_id
        self.text = None
        self.caption = f"Photo {message_id}"
        self.entities = []
        self.caption_entities = []
        self.photo = [SimpleNamespace(file_id=file_id)]
        self.video = None
        self.document = None
        self.animation = None
        self.audio = None
        self.voice = None
        self.video_note = None
        self.sticker = None
        self.contact = None
        self.location = None
        self.venue = None
        self.poll = None
        self.dice = None
        self.reply_to_message = None
        self.replies = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=901)


class TestMediaGroupFlush(unittest.TestCase):
    def setUp(self):
        self.old_db = advanced.db
        advanced.init_database(mongomock.MongoClient())
        self.actor_uid = 55
        self.bot_id = advanced.db.add_user_bot(self.actor_uid, "token", "album_bot")
        advanced.db.add_channel(self.bot_id, -10055, "albums", "Albums")
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()
        self.user_data = {f"{self.actor_uid}_{self.bot_id}": {"setting_message": True}}
        self.context = SimpleNamespace(
            bot=self.bot,
            job_queue=self.job_queue,
            user_data=self.user_data,
        )
        self.user = SimpleNamespace(id=self.actor_uid, first_name="Admin", username="admin")

    def tearDown(self):
        advanced.db = self.old_db

    def test_album_flush_job_is_bound_to_actor_user_data_and_saves_all_items(self):
        first = AlbumMessage(100, "photo-file-1")
        second = AlbumMessage(101, "photo-file-2")

        asyncio.run(advanced.handle_user_bot_message(
            SimpleNamespace(effective_user=self.user, message=first),
            self.context,
            self.bot_id,
            self.actor_uid,
        ))
        first_job = self.job_queue.jobs[-1]

        asyncio.run(advanced.handle_user_bot_message(
            SimpleNamespace(effective_user=self.user, message=second),
            self.context,
            self.bot_id,
            self.actor_uid,
        ))
        flush_job = self.job_queue.jobs[-1]

        # Each rescheduled job must retain the admin's PTB user_data mapping.
        self.assertEqual(first_job["kwargs"].get("user_id"), self.actor_uid)
        self.assertTrue(first_job["job"].removed)
        self.assertEqual(flush_job["kwargs"].get("user_id"), self.actor_uid)
        self.assertEqual(flush_job["kwargs"]["data"]["actor_uid"], self.actor_uid)

        # A PTB Job callback context resolves user_data from its scheduled user_id.
        job_context = SimpleNamespace(
            job=SimpleNamespace(data=flush_job["kwargs"]["data"]),
            user_data=self.user_data,
            bot=self.bot,
        )
        asyncio.run(flush_job["callback"](job_context))

        saved = advanced.db.get_messages(-10055, self.bot_id)
        self.assertEqual(len(saved), 2)
        self.assertEqual([row["media_id"] for row in saved], ["photo-file-1", "photo-file-2"])
        self.assertEqual({row["media_group_id"] for row in saved}, {"album-1"})
        self.assertIn("pending_buttons_group", self.user_data[f"{self.actor_uid}_{self.bot_id}"])
        self.assertEqual(len(self.bot.calls), 1)
        self.assertIn("Media group saved", self.bot.calls[0][1])


if __name__ == "__main__":
    unittest.main()
