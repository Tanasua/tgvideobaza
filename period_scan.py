# period_scan.py
# Сканує метадані відео каналу за останні N днів без завантаження файлів.
#
# pip install -r requirements.txt
# Запуск:        TG_API_ID=... TG_API_HASH=... python period_scan.py
# Інший період:  SCAN_DAYS=180 TG_API_ID=... TG_API_HASH=... python period_scan.py
#
# api_id / api_hash беруться на my.telegram.org (з окремого акаунта).
# Файл сесії — це повний доступ до акаунта. Він лежить ПОЗА репозиторієм
# (за замовчуванням ~/.tg-smotri/scan_session.session), щоб його не бачили git і Claude Code.
# Перший запуск (вхід з кодом з Telegram) робіть вручну у звичайному терміналі.
# Скрипт тільки читає публічний канал: не підписується, нічого не пише і не завантажує.

import asyncio
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import InputMessagesFilterVideo

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
CHANNEL = "smotri_media"
DAYS = int(os.environ.get("SCAN_DAYS", "365"))
SESSION = os.path.expanduser(os.environ.get("TG_SESSION", "~/.tg-smotri/scan_session"))

# Оцінка вартості за припущеннями з нашого розрахунку.
# Ціни станом на 29.09.2026 — перевіряйте перед реальним запуском обробки.
TRANSCRIBE_USD_PER_MIN = 0.003  # gpt-4o-mini-transcribe; локальний faster-whisper = $0
VISION_USD_PER_CLIP = {
    # 4 кадри 768x432 + ~800 токенів тексту + ~300 токенів відповіді, Batch API
    "Haiku 4.5 (batch)": 0.00205,
    "Sonnet 5.5 (batch)": 0.00478,
}


async def main():
    os.makedirs(os.path.dirname(SESSION), mode=0o700, exist_ok=True)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS)

    per_month = Counter()
    total_sec = 0
    total_bytes = 0
    no_duration = 0
    count = 0
    first_date = None
    last_date = None
    interrupted = False

    # flood_sleep_threshold: короткі FloodWait (до 120 с) Telethon перечекає сам
    async with TelegramClient(SESSION, API_ID, API_HASH, flood_sleep_threshold=120) as client:
        try:
            # Порядок — від нових до старих; wait_time — пауза 1 с між запитами по 100 повідомлень
            async for msg in client.iter_messages(CHANNEL, filter=InputMessagesFilterVideo, wait_time=1):
                if msg.date < cutoff:
                    break

                count += 1
                per_month[msg.date.strftime("%Y-%m")] += 1
                if last_date is None or msg.date > last_date:
                    last_date = msg.date
                if first_date is None or msg.date < first_date:
                    first_date = msg.date

                if msg.file:
                    if msg.file.duration:
                        total_sec += msg.file.duration
                    else:
                        no_duration += 1
                    total_bytes += msg.file.size or 0

                if count % 1000 == 0:
                    print(f"...{count} відео (дійшли до {msg.date:%Y-%m-%d})")
        except FloodWaitError as e:
            interrupted = True
            print(f"\nTelegram вимагає паузу {e.seconds} с — скан зупинено, результат нижче НЕПОВНИЙ.")

    print(f"\nВікно скану: {cutoff:%Y-%m-%d} — {now:%Y-%m-%d} (UTC, {DAYS} днів)")
    print(f"Відео: {count}" + (" (неповний підрахунок)" if interrupted else ""))
    if not count:
        return

    minutes = total_sec / 60
    print(f"Фактичний період відео: {first_date:%Y-%m-%d} — {last_date:%Y-%m-%d}")
    print(f"Сумарна тривалість: {total_sec / 3600:.1f} год (середня {total_sec / count:.0f} с)")
    if no_duration:
        print(f"Без тривалості в метаданих: {no_duration} відео (не враховані в сумі)")
    print(f"Сумарний розмір: {total_bytes / 1024**3:.1f} ГБ")

    print("\nОрієнтовна вартість обробки:")
    print(f"  Транскрипція gpt-4o-mini-transcribe: ~${minutes * TRANSCRIBE_USD_PER_MIN:.0f} (локально faster-whisper: $0)")
    for name, per_clip in VISION_USD_PER_CLIP.items():
        print(f"  Опис кадрів, {name}: ~${count * per_clip:.0f}")

    print("\nВідео по місяцях:")
    for month in sorted(per_month):
        print(f"  {month}: {per_month[month]}")


asyncio.run(main())
