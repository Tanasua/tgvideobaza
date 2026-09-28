# caption_parse.py
# Розбір підпису посту на спікера і цитату. Без мережі, тільки текст.
#
# Реальний формат підписів СМОТРИ ще не перевірено на даних, тому правила
# евристичні: цитата — перший фрагмент у «ялинках» (або “лапках”/"лапках"),
# спікер — текст перед цитатою на тому ж рядку або рядок вище.
# Усе, що не вкладається в цю схему, позначається nonstandard=1 з поясненням
# у note. Сирий підпис завжди зберігається окремо, тож правила можна
# змінити і перерахувати базу: python index_posts.py --reparse

import re
from typing import NamedTuple, Optional

# Пари лапок у порядку пріоритету
QUOTE_PAIRS = [("«", "»"), ("“", "”"), ("„", "“"), ('"', '"')]

MAX_SPEAKER_LEN = 150
SPEAKER_STRIP = " \t:—–-,.|•"

LINK_TOKEN = re.compile(r"^(?:@\w+|https?://\S+|t\.me/\S+)$", re.I)


class Parsed(NamedTuple):
    speaker: Optional[str]
    quote: Optional[str]
    nonstandard: bool
    note: str


def _find_quote(text):
    """Повертає (start, end, quote) першої цитати з урахуванням вкладених «»."""
    for left, right in QUOTE_PAIRS:
        start = text.find(left)
        if start == -1:
            continue
        if left == right:
            end = text.find(right, start + 1)
        else:
            depth, end = 0, -1
            for i in range(start, len(text)):
                if text.startswith(left, i):
                    depth += 1
                elif text.startswith(right, i):
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
        if end == -1:
            continue
        quote = text[start + len(left):end].strip()
        if quote:
            return start, end + len(right), quote
    return None


def _is_promo_line(line):
    """Рядок-«хвіст» каналу: лише посилання/@згадки і символи без літер."""
    tokens = line.split()
    has_link = False
    for tok in tokens:
        if LINK_TOKEN.match(tok.strip("()[]<>")):
            has_link = True
        elif re.search(r"\w", tok):
            return False
    return has_link


def clean_caption(caption):
    lines = [ln.rstrip() for ln in (caption or "").strip().splitlines()]
    while lines and (not lines[-1].strip() or _is_promo_line(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


def parse_caption(caption):
    text = clean_caption(caption)
    if not text:
        return Parsed(None, None, True, "порожній підпис")

    found = _find_quote(text)
    if not found:
        return Parsed(None, None, True, "немає цитати в лапках")
    start, end, quote = found

    notes = []
    line_start = text.rfind("\n", 0, start) + 1
    speaker = text[line_start:start].strip(SPEAKER_STRIP)
    if not speaker and line_start > 0:
        prev_start = text.rfind("\n", 0, line_start - 1) + 1
        speaker = text[prev_start:line_start - 1].strip(SPEAKER_STRIP)
        if speaker:
            notes.append("спікер рядком вище")
    if not speaker:
        # Формат «цитата» — спікер
        tail = text[end:].split("\n", 1)[0].strip(SPEAKER_STRIP)
        if tail:
            speaker = tail
            notes.append("спікер після цитати")

    nonstandard = False
    if not speaker:
        nonstandard = True
        notes.append("спікера не знайдено")
        speaker = None
    elif len(speaker) > MAX_SPEAKER_LEN:
        nonstandard = True
        notes.append("задовгий спікер")
    if _find_quote(text[end:]):
        nonstandard = True
        notes.append("кілька цитат")

    return Parsed(speaker, quote, nonstandard, "; ".join(notes))
