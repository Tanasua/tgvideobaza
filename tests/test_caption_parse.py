# Синтетичні приклади, не справжні підписи каналу.
import unittest

from caption_parse import parse_caption


class ParseCaptionTest(unittest.TestCase):
    def test_speaker_colon_quote(self):
        p = parse_caption("Иван Иванов: «Мы всё решим»")
        self.assertEqual((p.speaker, p.quote, p.nonstandard), ("Иван Иванов", "Мы всё решим", False))

    def test_speaker_dash_quote_multiline(self):
        p = parse_caption("Министр Петров — «Первая фраза.\nВторая фраза»")
        self.assertEqual(p.speaker, "Министр Петров")
        self.assertEqual(p.quote, "Первая фраза.\nВторая фраза")

    def test_nested_quotes(self):
        p = parse_caption("Сидоров: «Компания «Ромашка» закрылась»")
        self.assertEqual(p.quote, "Компания «Ромашка» закрылась")
        self.assertFalse(p.nonstandard)

    def test_speaker_on_previous_line(self):
        p = parse_caption("Депутат Кузнецов\n«Текст цитаты»")
        self.assertEqual(p.speaker, "Депутат Кузнецов")
        self.assertIn("рядком вище", p.note)

    def test_speaker_after_quote(self):
        p = parse_caption("«Текст цитаты» — Кузнецов")
        self.assertEqual(p.speaker, "Кузнецов")
        self.assertFalse(p.nonstandard)

    def test_promo_tail_removed(self):
        p = parse_caption("Иванов: «Цитата»\n\n👉 @smotri_media")
        self.assertEqual(p.speaker, "Иванов")
        self.assertFalse(p.nonstandard)

    def test_text_line_with_link_kept(self):
        p = parse_caption("Подробнее тут https://example.com")
        self.assertTrue(p.nonstandard)
        self.assertEqual(p.note, "немає цитати в лапках")

    def test_empty(self):
        p = parse_caption("")
        self.assertTrue(p.nonstandard)
        self.assertEqual(p.note, "порожній підпис")

    def test_quote_without_speaker(self):
        p = parse_caption("«Одна лишь цитата»")
        self.assertTrue(p.nonstandard)
        self.assertIsNone(p.speaker)

    def test_multiple_quotes(self):
        p = parse_caption("Иванов: «Раз»\nПетров: «Два»")
        self.assertEqual(p.quote, "Раз")
        self.assertTrue(p.nonstandard)
        self.assertIn("кілька цитат", p.note)

    def test_straight_quotes_fallback(self):
        p = parse_caption('Иванов: "Цитата"')
        self.assertEqual((p.speaker, p.quote), ("Иванов", "Цитата"))


if __name__ == "__main__":
    unittest.main()
