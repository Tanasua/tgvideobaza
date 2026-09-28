# Логіка продовження скану з фейковим клієнтом — без мережі і без Telegram.
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import index_posts

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def make_msg(i, days_ago, caption="", grouped_id=None):
    return SimpleNamespace(
        id=i, date=NOW - timedelta(days=days_ago), edit_date=None, grouped_id=grouped_id,
        message=caption, views=10, fwd_from=None,
        file=SimpleNamespace(duration=30, size=1000, width=1280, height=720),
    )


class FloodWait(Exception):
    pass


class FakeClient:
    """Імітує iter_messages: newest-first, offset_id/min_id виключні, reverse — oldest-first."""

    def __init__(self, messages, fail_after=None):
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)
        self.fail_after = fail_after
        self.served = 0
        self.calls = []

    async def iter_messages(self, channel, filter=None, offset_id=0, min_id=0, reverse=False, wait_time=None):
        self.calls.append(dict(offset_id=offset_id, min_id=min_id, reverse=reverse, wait_time=wait_time))
        assert wait_time is not None and wait_time >= 1
        msgs = [m for m in self.messages if m.id > min_id and (not offset_id or m.id < offset_id)]
        if reverse:
            msgs.reverse()
        for m in msgs:
            if self.fail_after is not None and self.served >= self.fail_after:
                raise FloodWait()
            self.served += 1
            yield m


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = index_posts.open_db(os.path.join(self.tmp.name, "t.db"))
        self.cutoff = NOW - timedelta(days=730)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def ids(self):
        return [r[0] for r in self.db.execute("SELECT post_id FROM posts ORDER BY post_id")]

    def run_sync(self, client):
        return asyncio.run(index_posts.sync(client, self.db, self.cutoff, log=lambda *_: None))

    def test_stops_at_cutoff(self):
        msgs = [make_msg(i, days_ago=(10 - i) * 100) for i in range(1, 11)]  # 900..0 днів тому
        self.run_sync(FakeClient(msgs))
        self.assertEqual(self.ids(), [3, 4, 5, 6, 7, 8, 9, 10])  # 700 днів і новіші
        self.assertEqual(index_posts.get_meta(self.db, "backfill_reached"), self.cutoff.isoformat())

    def test_resume_after_interruption_has_no_gaps(self):
        msgs = [make_msg(i, days_ago=100 - i) for i in range(1, 51)]
        with self.assertRaises(FloodWait):
            self.run_sync(FakeClient(msgs, fail_after=20))
        self.db.commit()
        self.assertEqual(self.ids(), list(range(31, 51)))
        self.assertIsNone(index_posts.get_meta(self.db, "backfill_reached"))

        # Між запусками з'явилися нові пости; другий запуск теж обривається посеред докачки
        msgs += [make_msg(i, days_ago=0) for i in range(51, 61)]
        with self.assertRaises(FloodWait):
            self.run_sync(FakeClient(msgs, fail_after=5))
        self.db.commit()
        self.assertEqual(self.ids(), list(range(31, 56)))  # без дірки: 51..55 підряд

        client = FakeClient(msgs)
        self.run_sync(client)
        self.assertEqual(self.ids(), list(range(1, 61)))
        self.assertEqual(client.calls[0], dict(offset_id=0, min_id=55, reverse=True, wait_time=1))
        self.assertEqual(client.calls[1], dict(offset_id=31, min_id=0, reverse=False, wait_time=1))

        # Усе зібрано: наступний запуск лише перевіряє нові
        client = FakeClient(msgs)
        self.run_sync(client)
        self.assertEqual(len(client.calls), 1)

    def test_original_source_survives_refetch(self):
        msgs = [make_msg(1, 1, "Иванов: «Цитата»")]
        self.run_sync(FakeClient(msgs))
        self.db.execute("UPDATE posts SET original_source = 'Первый канал' WHERE post_id = 1")
        index_posts.upsert(self.db, index_posts.message_row(msgs[0], NOW.isoformat()))
        self.assertEqual(self.db.execute("SELECT original_source FROM posts").fetchone()[0], "Первый канал")

    def test_album_caption_propagates(self):
        msgs = [make_msg(1, 1, "Иванов: «Цитата»", grouped_id=7), make_msg(2, 1, "", grouped_id=7),
                make_msg(3, 1, "", grouped_id=8)]
        self.run_sync(FakeClient(msgs))
        index_posts.reparse(self.db)
        rows = dict((r[0], r[1:]) for r in self.db.execute(
            "SELECT post_id, caption_from_album, speaker, nonstandard FROM posts"))
        self.assertEqual(rows[1], (0, "Иванов", 0))
        self.assertEqual(rows[2], (1, "Иванов", 0))
        self.assertEqual(rows[3], (0, None, 1))

        path = os.path.join(self.tmp.name, "posts.csv")
        self.assertEqual(index_posts.export_csv(self.db, path), 3)
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
        self.assertEqual(text.count("Иванов: «Цитата»"), 2)


if __name__ == "__main__":
    unittest.main()
