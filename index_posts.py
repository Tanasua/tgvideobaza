# index_posts.py
# Індекс відеопостів каналу за підписами: SQLite + CSV у ./data/. Файли відео НЕ завантажує.
#
# pip install -r requirements.txt
# Запуск:          TG_API_ID=... TG_API_HASH=... python index_posts.py
# Інший період:    INDEX_DAYS=365 TG_API_ID=... TG_API_HASH=... python index_posts.py
# Перерахувати розбір підписів і CSV без Telegram:  python index_posts.py --reparse
#
# Повторний запуск продовжує роботу: спершу докачує нові пости (від max post_id,
# у хронологічному порядку), потім дозбирає старіші (від min post_id назад) до межі періоду.
# Перерваний скан (FloodWait, Ctrl+C) не лишає дірок — просто запустіть ще раз.
#
# Сесія — та сама, що в period_scan.py (~/.tg-smotri/scan_session), поза репозиторієм.
# Якщо сесія не авторизована, скрипт зупиняється: перший вхід робіть вручну (див. period_scan.py).
# Скрипт тільки читає публічний канал: не підписується і нічого не пише.

import asyncio
import csv
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from caption_parse import parse_caption

CHANNEL = "smotri_media"
DAYS = int(os.environ.get("INDEX_DAYS", "730"))
SESSION = os.path.expanduser(os.environ.get("TG_SESSION", "~/.tg-smotri/scan_session"))
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "smotri.db")
CSV_PATH = os.path.join(DATA_DIR, "posts.csv")
COMMIT_EVERY = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id            INTEGER PRIMARY KEY,
    date               TEXT NOT NULL,     -- дата посту, UTC ISO (не дата зйомки!)
    edit_date          TEXT,
    grouped_id         INTEGER,           -- альбом: підпис буває лише на одному елементі
    caption            TEXT NOT NULL,     -- сирий підпис як є
    caption_from_album INTEGER NOT NULL DEFAULT 0,
    speaker            TEXT,              -- з підпису, не факт
    quote              TEXT,              -- переказ каналу, не стенограма
    nonstandard        INTEGER NOT NULL DEFAULT 0,
    parse_note         TEXT,
    duration           INTEGER,           -- секунди
    size               INTEGER,           -- байти
    width              INTEGER,
    height             INTEGER,
    views              INTEGER,
    fwd_from           TEXT,              -- технічні дані пересилання, якщо є
    post_url           TEXT NOT NULL,
    original_source    TEXT,              -- заповнюється вручну, скрипт не перезаписує
    fetched_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_date ON posts(date);
CREATE INDEX IF NOT EXISTS posts_speaker ON posts(speaker);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Поля, які оновлюються при повторному проході; original_source сюди не входить
FETCHED_FIELDS = [
    "post_id", "date", "edit_date", "grouped_id", "caption", "duration", "size",
    "width", "height", "views", "fwd_from", "post_url", "fetched_at",
]

CSV_FIELDS = [
    "post_id", "date", "caption", "speaker", "quote", "duration", "size", "post_url",
    "nonstandard", "parse_note", "caption_from_album", "grouped_id", "fwd_from",
    "original_source", "views", "edit_date",
]


def open_db(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def get_meta(db, key):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(db, key, value):
    db.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def _fwd_repr(fwd):
    if not fwd:
        return None
    parts = []
    if getattr(fwd, "from_name", None):
        parts.append(fwd.from_name)
    peer = getattr(fwd, "from_id", None)
    if peer is not None:
        pid = getattr(peer, "channel_id", None) or getattr(peer, "user_id", None) or getattr(peer, "chat_id", None)
        parts.append(f"peer:{pid}")
    if getattr(fwd, "channel_post", None):
        parts.append(f"post:{fwd.channel_post}")
    return " ".join(parts) or "forwarded"


def message_row(msg, fetched_at):
    f = msg.file
    return {
        "post_id": msg.id,
        "date": msg.date.astimezone(timezone.utc).isoformat(),
        "edit_date": msg.edit_date.astimezone(timezone.utc).isoformat() if msg.edit_date else None,
        "grouped_id": msg.grouped_id,
        "caption": msg.message or "",
        "duration": int(f.duration) if f and f.duration else None,
        "size": f.size if f else None,
        "width": f.width if f else None,
        "height": f.height if f else None,
        "views": msg.views,
        "fwd_from": _fwd_repr(msg.fwd_from),
        "post_url": f"https://t.me/{CHANNEL}/{msg.id}",
        "fetched_at": fetched_at,
    }


def upsert(db, row):
    cols = ", ".join(FETCHED_FIELDS)
    marks = ", ".join("?" for _ in FETCHED_FIELDS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in FETCHED_FIELDS if c != "post_id")
    db.execute(f"INSERT INTO posts({cols}) VALUES({marks}) "
               f"ON CONFLICT(post_id) DO UPDATE SET {updates}",
               [row[c] for c in FETCHED_FIELDS])


def album_captions(db):
    """grouped_id -> підпис альбому (з першого за post_id елемента, що має підпис)."""
    return dict(db.execute(
        "SELECT grouped_id, caption FROM posts WHERE grouped_id IS NOT NULL AND caption != '' "
        "ORDER BY post_id DESC"))  # dict залишає останнє значення, тобто найменший post_id


def reparse(db):
    """Підтягує підпис альбому на елементи без підпису і заново розбирає всі підписи."""
    albums = album_captions(db)
    updates = []
    for post_id, caption, gid in db.execute("SELECT post_id, caption, grouped_id FROM posts").fetchall():
        from_album = not caption and gid in albums
        p = parse_caption(albums[gid] if from_album else caption)
        updates.append((int(from_album), p.speaker, p.quote, int(p.nonstandard), p.note, post_id))
    db.executemany("UPDATE posts SET caption_from_album = ?, speaker = ?, quote = ?, nonstandard = ?, "
                   "parse_note = ? WHERE post_id = ?", updates)
    db.commit()


def export_csv(db, path=CSV_PATH):
    albums = album_captions(db)
    cur = db.execute(f"SELECT {', '.join(CSV_FIELDS)} FROM posts ORDER BY post_id DESC")
    cap_i, alb_i, gid_i = (CSV_FIELDS.index(c) for c in ("caption", "caption_from_album", "grouped_id"))
    tmp = path + ".tmp"
    n = 0
    # utf-8-sig — щоб Excel відкривав кирилицю без танців
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_FIELDS)
        for row in cur:
            row = list(row)
            if row[alb_i]:
                row[cap_i] = albums.get(row[gid_i], "")
            w.writerow(row)
            n += 1
    os.replace(tmp, path)
    return n


async def sync(client, db, cutoff, log=print):
    """Докачує нові пости і дозбирає старі до cutoff. Повертає кількість оброблених повідомлень."""
    from telethon.tl.types import InputMessagesFilterVideo

    fetched_at = datetime.now(timezone.utc).isoformat()
    max_id, min_id = db.execute("SELECT MAX(post_id), MIN(post_id) FROM posts").fetchone()
    seen = 0

    def step(msg):
        nonlocal seen
        upsert(db, message_row(msg, fetched_at))
        seen += 1
        if seen % COMMIT_EVERY == 0:
            db.commit()
        if seen % 1000 == 0:
            log(f"...{seen} відео (зараз {msg.date:%Y-%m-%d})")

    # 1. Нові пости: від старих до нових, щоб обрив не лишав дірки
    if max_id:
        log(f"Докачую нові пости після #{max_id}")
        async for msg in client.iter_messages(CHANNEL, filter=InputMessagesFilterVideo,
                                              min_id=max_id, reverse=True, wait_time=1):
            step(msg)
        db.commit()

    # 2. Старі пости: від min_id назад до межі періоду
    reached = get_meta(db, "backfill_reached")
    if reached and reached <= cutoff.isoformat():
        log(f"Старіші пости вже зібрані до {reached[:10]}")
    else:
        log("Дозбираю старіші пости" + (f" до #{min_id}" if min_id else "") + f", межа {cutoff:%Y-%m-%d}")
        async for msg in client.iter_messages(CHANNEL, filter=InputMessagesFilterVideo,
                                              offset_id=min_id or 0, wait_time=1):
            if msg.date < cutoff:
                break
            step(msg)
        # Дійшли до межі або до початку каналу
        set_meta(db, "backfill_reached", cutoff.isoformat())
        db.commit()
    return seen


async def run_sync():
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError

    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    os.makedirs(os.path.dirname(SESSION), mode=0o700, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    db = open_db()
    code = 0

    # flood_sleep_threshold: короткі FloodWait (до 120 с) Telethon перечекає сам, як у period_scan.py
    client = TelegramClient(SESSION, api_id, api_hash, flood_sleep_threshold=120)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("Сесія Telegram не авторизована. Увійдіть вручну у своєму терміналі "
                  "(python period_scan.py), потім запустіть цей скрипт ще раз.")
            return 3
        try:
            n = await sync(client, db, cutoff)
            print(f"Оброблено відеопостів за цей запуск: {n}")
        except FloodWaitError as e:
            db.commit()
            print(f"\nTelegram вимагає паузу {e.seconds} с — скан зупинено. "
                  f"Зібране збережено; запустіть пізніше, щоб продовжити.")
            code = 2
    finally:
        await client.disconnect()
        db.commit()

    finish(db)
    db.close()
    return code


def finish(db):
    reparse(db)
    n = export_csv(db)
    total, nonstd, first, last = db.execute(
        "SELECT COUNT(*), SUM(nonstandard), MIN(date), MAX(date) FROM posts").fetchone()
    print(f"\nБаза: {DB_PATH}")
    print(f"CSV:  {CSV_PATH} ({n} рядків)")
    if total:
        print(f"Постів: {total}, період {first[:10]} — {last[:10]}")
        print(f"Нестандартний підпис: {nonstd} ({nonstd / total:.0%})")


def main():
    if "--reparse" in sys.argv[1:]:
        if not os.path.exists(DB_PATH):
            print(f"Бази ще немає: {DB_PATH}")
            return 1
        db = open_db()
        finish(db)
        db.close()
        return 0
    return asyncio.run(run_sync())


if __name__ == "__main__":
    sys.exit(main())
