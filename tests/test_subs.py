"""YouTube altyazısı (SRT): seslendirme satırlarının GERÇEK (seslendirilmiş + çakışmasız) zamanlarıyla.
Kullanıcı (2026-10-20): videolar İngilizce seslendirilir, Türkçe YouTube'a kendi altyazısıyla eklenir."""
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from promokit import story, subs, voice  # noqa: E402
from promokit.util import PromoError  # noqa: E402


def _mk(body: str) -> str:
    d = tempfile.mkdtemp(prefix="promo-subs-")
    Image.new("RGB", (300, 200), (40, 90, 160)).save(os.path.join(d, "a.png"))
    p = os.path.join(d, "s.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body))
    return p


class TestSrt(unittest.TestCase):
    def test_format_and_timing(self):
        L = voice.Line
        lines = [L(0.2, "Got an old tablet in a drawer?", None, 0, dur=1.8, subs={"tr": "Çekmecede eski bir tablet mi var?"}),
                 L(2.1, "With Acmeify Studio, it becomes your Mac's second display.", None, 0, dur=3.0,
                   subs={"tr": "Acmeify Studio ile o tablet, Mac'inizin ikinci ekranı olur."})]
        out = subs.srt(lines, "tr")
        self.assertEqual(out.split("\n\n")[0], "1\n00:00:00,200 --> 00:00:02,000\nÇekmecede eski bir tablet mi var?")
        second = out.strip().split("\n\n")[1].split("\n")
        self.assertEqual(second[:2], ["2", "00:00:02,100 --> 00:00:05,100"])
        self.assertTrue(all(len(x) <= subs.MAX_CHARS for x in second[2:]))    # uzun satır kırılır
        self.assertLessEqual(len(second[2:]), 2)
        en = subs.srt(lines, "en", default_lang="en")                          # seslendirme dili = satır metni
        self.assertIn("Got an old tablet in a drawer?", en)

    def test_missing_translation_is_an_error(self):
        lines = [voice.Line(0.2, "Hello.", None, 0, dur=1.0)]
        with self.assertRaises(PromoError) as cm:
            subs.srt(lines, "tr")
        self.assertIn("Hello.", str(cm.exception))

    def test_story_carries_translations(self):
        p = _mk("""
            [video]
            size = 360
            fps = 10
            [voice]
            engine = "say"
            lang = "en"
            [[scene]]
            duration = 2.0
            tablet = "a.png"
            voice = [ { t = 0.2, text = "Hello.", tr = "Merhaba." } ]
        """)
        st = story.load(p)
        self.assertEqual(st.voice_lines()[0].subs, {"tr": "Merhaba."})
        bad = p.replace("s.toml", "b.toml")
        with open(p, encoding="utf-8") as f, open(bad, "w", encoding="utf-8") as g:
            g.write(f.read().replace('tr = "Merhaba."', 'xx = "Merhaba."'))
        with self.assertRaises(PromoError):
            story.load(bad)


if __name__ == "__main__":
    unittest.main()
