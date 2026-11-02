"""Seslendirme (voice): senaryo alanı, ses üretimi (ElevenLabs / macOS `say`), önbellek, zamanlama
denetimi ve render'da karıştırma (müzik konuşurken kısılır, ses yüksekliği eşitlenir).

Ağ çağrısı YOK: ElevenLabs isteği sahte `urlopen` ile yakalanıp denetlenir (gerçek hesap/ücret yok).
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from promokit import story, voice  # noqa: E402
from promokit.util import PromoError, ffmpeg_bin  # noqa: E402


def _tone(path: str, freq: float, dur: float, fmt_args=()):
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
                    *fmt_args, path], check=True)
    return path


def _mk(body: str) -> str:
    d = tempfile.mkdtemp(prefix="promo-voice-")
    Image.new("RGB", (300, 200), (40, 90, 160)).save(os.path.join(d, "a.png"))
    p = os.path.join(d, "s.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body))
    return p


STORY = """
[video]
size = 360
fps = 10
transition = "cut"

[voice]
engine = "elevenlabs"
id = "testvoice"

[[scene]]
duration = 2.0
tablet = "a.png"
voice = [ { t = 0.2, text = "Mac'te dokunmatik ekran yok." }, { t = 1.2, text = "Tabletiniz o ekran olsun." } ]

[[scene]]
duration = 1.5
tablet = "a.png"
voice = [ { t = 0.1, text = "Acmeify Studio." } ]
"""


class TestStoryVoice(unittest.TestCase):
    def test_lines_in_story_time(self):
        st = story.load(_mk(STORY))
        self.assertEqual(st.voice.engine, "elevenlabs")
        self.assertEqual(st.voice.id, "testvoice")
        lines = st.voice_lines()
        self.assertEqual([round(x.t, 3) for x in lines], [0.2, 1.2, 2.1])     # 2. sahne 2.0'da başlar (cut)
        self.assertEqual(lines[2].text, "Acmeify Studio.")
        self.assertEqual(lines[2].scene, 1)

    def test_line_speed_reaches_the_line(self):
        """Satır başına `speed` (kullanıcı, 2026-10-30: "o ilk cümleyi çok hızlı söylüyor") — yalnız o satırın Line'ına."""
        body = STORY.replace('voice = [ { t = 0.1, text = "Acmeify Studio." } ]',
                             'voice = [ { t = 0.1, text = "Acmeify Studio.", speed = 0.85 } ]')
        lines = story.load(_mk(body)).voice_lines()
        self.assertEqual([x.speed for x in lines], [None, None, 0.85])

    def test_validation(self):
        bad = [
            ('voice = [ { t = 0.2, txt = "x" } ]', "txt"),
            ('voice = [ { t = 0.2 } ]', "text"),
            ('voice = [ { t = -1.0, text = "x" } ]', "t"),
            ('voice = [ { t = 0.2, src = "yok.wav" } ]', "yok.wav"),
            ('voice = "metin"', "listesi"),
            ('voice = [ { t = 0.2, text = "x", say = "" } ]', "say"),
            ('voice = [ { t = 0.2, text = "x", say = 3 } ]', "say"),
            ('voice = [ { t = 0.2, text = "x", speed = 3.0 } ]', "speed"),      # atempo aralığı 0,5–2
            ('voice = [ { t = 0.2, text = "x", speed = "yavaş" } ]', "speed"),
        ]
        for line, needle in bad:
            with self.subTest(line):
                body = STORY.replace('voice = [ { t = 0.1, text = "Acmeify Studio." } ]', line)
                with self.assertRaises(PromoError) as cm:
                    story.load(_mk(body))
                self.assertIn(needle, str(cm.exception))
        for cfg, needle in (('engine = "polly"', "engine"), ("stability = 1.5", "stability"), ("speed = 3.0", "speed"),
                            ('idd = "x"', "idd")):
            with self.subTest(cfg):
                base = STORY.replace('engine = "elevenlabs"\n', "") if cfg.startswith("engine") else STORY
                body = base.replace('id = "testvoice"', f'id = "testvoice"\n{cfg}')
                with self.assertRaises(PromoError) as cm:
                    story.load(_mk(body))
                self.assertIn(needle, str(cm.exception))


class TestTimingCheck(unittest.TestCase):
    def test_lines_never_overlap(self):
        """Konuşma hiçbir sesle üst üste binmez: satır öncekinin bitişinden (+ara) önce başlamaz."""
        L = voice.Line
        lines = [L(0.2, "bir", None, 0, dur=1.5), L(1.2, "iki", None, 0, dur=0.5), L(3.2, "üç", None, 1, dur=0.3)]
        voice.schedule(lines)
        self.assertAlmostEqual(lines[1].t, 0.2 + 1.5 + voice.GAP)     # kaydı
        self.assertAlmostEqual(lines[1].want, 1.2)                     # yazılan zaman saklı
        self.assertAlmostEqual(lines[2].t, 3.2)                        # çakışmayan yerinde kalır
        for a, b in zip(lines, lines[1:]):
            self.assertGreaterEqual(b.t, a.t + a.dur + voice.GAP - 1e-9)

    def test_drift_and_overrun_are_reported(self):
        L = voice.Line
        lines = [L(0.2, "bir", None, 0, dur=1.5), L(1.2, "iki", None, 0, dur=0.5), L(3.2, "üç", None, 1, dur=0.9)]
        voice.schedule(lines)
        w = voice.check(lines, total=3.5)
        self.assertTrue(any("iki" in x and "geç" in x for x in w), w)      # 0,62 sn kaydı (> 0,6)
        self.assertTrue(any("üç" in x and "bitim" in x for x in w), w)    # 3,2+0,9 > 3,5
        ok = [L(0.2, "bir", None, 0, dur=0.8), L(1.2, "iki", None, 0, dur=0.5)]
        voice.schedule(ok)
        self.assertEqual(voice.check(ok, total=3.5), [])


class TestPronunciation(unittest.TestCase):
    """İngilizce kelimeler İngilizce okunur (kullanıcı, 2026-10-20: "Mac'i dümdüz mac diye okuyor").
    Ekrandaki metin değişmez; ses motoruna giden metne okunuş sözlüğü uygulanır."""

    def test_default_lexicon(self):
        sp = voice.spoken
        self.assertEqual(sp("Mac'te dokunmatik ekran yok."), "Mek'te dokunmatik ekran yok.")
        self.assertEqual(sp("İkisi de Mac'inize ayrı bir ekran."), "İkisi de Mek'inize ayrı bir ekran.")
        self.assertEqual(sp("MacBook'un kapağını kapatın"), "Mekbuk'un kapağını kapatın")
        self.assertEqual(sp("Aynı Wi-Fi'da tablet"), "Aynı vayfay'da tablet")
        self.assertEqual(sp("Üç parmakla Mission Control."), "Üç parmakla Mişın Kontrol.")
        self.assertEqual(sp("Geliştirici modu yok, ADB yok."), "Geliştirici modu yok, ey di bi yok.")
        self.assertEqual(sp("Macera ve MACRO"), "Macera ve MACRO")          # kelime içi / başka kelime: dokunma

    def test_turkish_lexicon_only_for_turkish(self):
        """İngilizce seslendirmede "Mac" İngilizce kalır (sözlük Türkçe okunuş içindir)."""
        self.assertEqual(voice.spoken("Your Mac's second display.", lang="en"), "Your Mac's second display.")
        self.assertEqual(voice.spoken("Mac'te", lang="tr"), "Mek'te")
        st = story.load(_mk(STORY.replace('id = "testvoice"', 'id = "testvoice"\nlang = "en"')))
        self.assertEqual(st.voice.lang, "en")
        with self.assertRaises(PromoError):
            story.load(_mk(STORY.replace('id = "testvoice"', 'id = "testvoice"\nlang = "xx"')))

    def test_story_lexicon_overrides_and_extends(self):
        body = STORY.replace('id = "testvoice"', 'id = "testvoice"\nlexicon = { "Acmeify Studio" = "Akmifay Stüdyo", "HDR" = "eyç di ar" }')
        st = story.load(_mk(body))
        self.assertEqual(voice.spoken("Acmeify Studio ve HDR, Mac'te.", st.voice.lexicon),
                         "Akmifay Stüdyo ve eyç di ar, Mek'te.")
        bad = STORY.replace('id = "testvoice"', 'id = "testvoice"\nlexicon = { "Mac" = 3 }')
        with self.assertRaises(PromoError):
            story.load(_mk(bad))

    def test_engine_receives_spoken_text(self):
        """Önbellek anahtarı OKUNAN metinden → sözlük değişince yeniden üretilir."""
        tmp = tempfile.mkdtemp(prefix="promo-voice-lex-")
        mp3 = _tone(os.path.join(tmp, "t.mp3"), 440, 0.3)
        sent = []

        def fake(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8"))["text"])
            with open(mp3, "rb") as f:
                return io.BytesIO(f.read())
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), \
                mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "k"}), mock.patch.object(voice, "urlopen", fake):
            voice.synth("Mac'te.", voice.VoiceCfg(engine="elevenlabs", id="x"))
            voice.synth("Mac'te.", voice.VoiceCfg(engine="elevenlabs", id="x", lexicon={"Mac": "Mak"}))
        self.assertEqual(sent, ["Mek'te.", "Mak'te."])


def _words(s: str, step: float = 0.2):
    return [(w, i * step, i * step + 0.18) for i, w in enumerate(s.split())]


class TestTranscriptJudge(unittest.TestCase):
    """Chatterbox'ın ÖLÇÜLEN arızaları (2026-10-20, en-B) — tanıyıcının gerçek çıktılarıyla. Kulakla dinlenmeden
    fark edilmiyordu: 2 sn'lik cümle 8,5 sn'ye uzamıştı ve sonu anlamsız konuşmaydı."""

    def test_made_up_tail_is_rejected_and_cut_at_the_sentence(self):
        from promokit.asrcheck import judge
        r = judge("Macs don't have a touchscreen.", _words("Max don't have a touch screen. What is a presenting in all of Dicis"))
        self.assertFalse(r["ok"])
        self.assertAlmostEqual(r["end"], 5 * 0.2 + 0.18)              # "screen." biter → kuyruk kırpılır

    def test_repetition_is_rejected(self):
        from promokit.asrcheck import judge
        self.assertFalse(judge("Acmeify Studio. Now in open beta.",
                               _words("acme if I studio, now in open beta, now in open beta."))["ok"])

    def test_brand_split_by_the_recognizer_still_passes(self):
        from promokit.asrcheck import judge
        r = judge("Acmeify Studio. Now in open beta.", _words("acme if I studio, now in open beta."))
        self.assertTrue(r["ok"], r)

    def test_missing_words_and_silence_fail(self):
        from promokit.asrcheck import judge
        self.assertFalse(judge("Three fingers for Mission Control.", _words("Three fingers for"))["ok"])
        self.assertFalse(judge("Let your tablet be one.", [])["ok"])

    def test_any_word_after_the_sentence_rejects(self):
        """"…its own speaker. Bad day!": benzerlik 1,0 ve uzunluk payı içinde — eski ölçüt "tamam" diyordu."""
        from promokit.asrcheck import judge
        r = judge("Switch the output to your tablet. Each tablet is its own speaker.",
                  _words("Switch the output to your tablet. Each tablet is its own speaker. Bad day!"))
        self.assertFalse(r["ok"])
        self.assertAlmostEqual(r["next"], 12 * 0.2)
        self.assertTrue(judge("Hello there.", _words("Hello there. -"))["ok"])      # noktalama "sözcüğü" sayılmaz

    def test_exact_reading_passes_with_word_bounds(self):
        from promokit.asrcheck import judge
        r = judge("Double-tap to open. Pinch to zoom in.", [("Double", 0.05, 0.2), ("tap", 0.2, 0.36), ("to", 0.36, 0.56),
                                                         ("open,", 0.56, 0.76), ("pinch", 1.1, 1.28), ("to", 1.28, 1.48),
                                                         ("zoom", 1.48, 1.6), ("in.", 1.6, 1.84)])
        self.assertTrue(r["ok"])
        self.assertEqual((r["start"], r["end"]), (0.05, 1.84))


class TestChatterbox(unittest.TestCase):
    """Yerel Chatterbox: satırlar TEK süreçte toplu üretilir (model bir kez yüklenir), önbellek tohumu da kapsar."""

    def test_batch_jobs_and_cache(self):
        tmp = tempfile.mkdtemp(prefix="promo-voice-cbx-")
        wav = _tone(os.path.join(tmp, "t.wav"), 440, 0.5)
        runs = []

        def fake_run(cmd, **kw):
            with open(cmd[-1], encoding="utf-8") as f:
                jobs = json.load(f)
            runs.append(jobs)
            for j in jobs:
                import shutil
                shutil.copy(wav, j["out"])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        cfg = voice.VoiceCfg(engine="chatterbox", exaggeration=0.7, cfg_weight=0.4, seed=3, speed=1.0)
        lines = [voice.Line(0.2, "Mac'te dokunmatik ekran yok.", None, 0), voice.Line(2.0, "Tabletiniz o ekran olsun.", None, 0)]
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), mock.patch.object(voice, "_run_chatterbox", fake_run), \
                mock.patch.object(voice, "CHATTERBOX_PY", sys.executable):
            voice.prepare(lines, cfg)
            again = [voice.Line(0.2, "Mac'te dokunmatik ekran yok.", None, 0)]
            voice.prepare(again, cfg)                                # önbellekte → süreç YOK
        self.assertEqual(len(runs), 1)
        self.assertEqual([j["text"] for j in runs[0]], ["Mek'te dokunmatik ekran yok.", "Tabletiniz o ekran olsun."])
        self.assertEqual(runs[0][0]["lang"], "tr")
        self.assertEqual(runs[0][0]["exaggeration"], 0.7)
        self.assertEqual(runs[0][0]["seed"], 3)
        self.assertTrue(all(os.path.exists(x.path) for x in lines))
        self.assertAlmostEqual(lines[0].dur, 0.5, delta=0.05)

    def test_language_reaches_the_model(self):
        tmp = tempfile.mkdtemp(prefix="promo-voice-cbx-en-")
        wav = _tone(os.path.join(tmp, "t.wav"), 440, 0.4)
        runs = []

        def fake_run(cmd, **kw):
            with open(cmd[-1], encoding="utf-8") as f:
                jobs = json.load(f)
            runs.append(jobs)
            for j in jobs:
                import shutil
                shutil.copy(wav, j["out"])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), mock.patch.object(voice, "_run_chatterbox", fake_run), \
                mock.patch.object(voice, "CHATTERBOX_PY", sys.executable):
            voice.prepare([voice.Line(0.0, "Your Mac's second display.", None, 0)], voice.VoiceCfg(engine="chatterbox", lang="en"))
        self.assertEqual(runs[0][0]["lang"], "en")
        self.assertEqual(runs[0][0]["text"], "Your Mac's second display.")

    def test_speed_is_tempo_not_pitch(self):
        """Chatterbox'ta hız ayarı yok → `speed` dönüşümde `atempo` olur (perde değişmez, süre kısalır)."""
        tmp = tempfile.mkdtemp(prefix="promo-voice-tempo-")
        wav = _tone(os.path.join(tmp, "t.wav"), 440, 1.0)
        out = os.path.join(tmp, "o.wav")
        voice._to_wav(wav, out, tempo=1.25)
        self.assertAlmostEqual(voice.duration(out), 0.8, delta=0.03)

    def test_line_speed_slows_only_that_line_and_keeps_report(self):
        """Satır `speed`i YALNIZ o satırı atempo ile yavaşlatır (perde değişmez); komşu satır aynı kalır, ham üretim
        önbellekte bir kez — hızı değiştirmek modeli yeniden KOŞTURMAZ. Doğrulama raporu yeni dosyada da okunur:
        tutmayan satırın `check` uyarısı hız ayarıyla kaybolmamalı."""
        tmp = tempfile.mkdtemp(prefix="promo-voice-line-speed-")
        wav = _tone(os.path.join(tmp, "t.wav"), 440, 1.0)
        runs = []

        def fake_run(cmd, **kw):
            with open(cmd[-1], encoding="utf-8") as f:
                jobs = json.load(f)
            runs.append(jobs)
            for j in jobs:
                import shutil
                shutil.copy(wav, j["out"])
                with open(j["out"] + ".json", "w", encoding="utf-8") as f:
                    json.dump({"verified": True, "ok": False, "ratio": 0.6, "heard": "x", "start": 0.0, "end": 0.0,
                               "tries": 4}, f)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        cfg = voice.VoiceCfg(engine="chatterbox", lang="en")
        slow = voice.Line(0.0, "Final Cut Pro on a tablet?", None, 0, speed=0.8)
        plain = voice.Line(3.0, "Hold a clip.", None, 0)
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), mock.patch.object(voice, "_run_chatterbox", fake_run), \
                mock.patch.object(voice, "CHATTERBOX_PY", sys.executable):
            voice.prepare([slow, plain], cfg)
            again = voice.Line(0.0, "Final Cut Pro on a tablet?", None, 0, speed=0.9)
            voice.prepare([again], cfg)
        self.assertAlmostEqual(slow.dur, 1.0 / 0.8, delta=0.04)
        self.assertAlmostEqual(plain.dur, 1.0, delta=0.03)
        self.assertAlmostEqual(again.dur, 1.0 / 0.9, delta=0.04)
        self.assertEqual(len(runs), 1)                                   # ham ses önbellekten: model bir kez
        self.assertTrue(any("tutmadı" in w for w in voice.check([slow], total=10.0)))

    def test_verified_line_is_trimmed_and_failure_is_reported(self):
        """Rapor (tts_chatterbox.py) sözcük zamanlarını verir → önbelleğe giden ses [baş−pay, son+pay] aralığıdır
        (kuyruktaki hırıltı/uydurma GİTMEZSE süre şişer ve sonraki satırlar kayar). Tutmayan satır `check`te
        her koşuda uyarılır — önbellekten gelse bile."""
        tmp = tempfile.mkdtemp(prefix="promo-voice-cbx-trim-")
        wav = _tone(os.path.join(tmp, "t.wav"), 440, 3.0)

        def fake_run(cmd, **kw):
            with open(cmd[-1], encoding="utf-8") as f:
                jobs = json.load(f)
            for j in jobs:
                import shutil
                shutil.copy(wav, j["out"])
                ok = "touchscreen" in j["text"]
                with open(j["out"] + ".json", "w", encoding="utf-8") as f:
                    json.dump({"verified": True, "ok": ok, "ratio": 0.93 if ok else 0.6, "heard": "x y z",
                               "start": 0.5, "end": 1.5, "tries": 4}, f)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        cfg = voice.VoiceCfg(engine="chatterbox", lang="en")
        lines = [voice.Line(0.0, "Macs don't have a touchscreen.", None, 0), voice.Line(3.0, "Three fingers.", None, 0)]
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), mock.patch.object(voice, "_run_chatterbox", fake_run), \
                mock.patch.object(voice, "CHATTERBOX_PY", sys.executable):
            voice.prepare(lines, cfg)
        lo, hi = voice.TRIM_PAD
        self.assertAlmostEqual(lines[0].dur, 1.0 + lo + hi, delta=0.03)
        warns = voice.check(lines, 30.0)
        self.assertEqual(len(warns), 1)
        self.assertIn("Three fingers.", warns[0])
        self.assertIn("tutmadı", warns[0])

    def test_verification_is_part_of_the_cache_key(self):
        """Doğrulamasız eski önbellek (uydurma kuyruklu ses) yeniden kullanılmasın: ayar anahtara girer."""
        cfg = voice.VoiceCfg(engine="chatterbox", lang="en")
        a = voice._digest("Hello.", cfg, "chatterbox")
        with mock.patch.object(voice, "VERIFY_TRIES", 1):
            self.assertNotEqual(a, voice._digest("Hello.", cfg, "chatterbox"))
        with mock.patch.object(voice, "TRIM_PAD", (0.0, 0.0)):
            self.assertNotEqual(a, voice._digest("Hello.", cfg, "chatterbox"))
        from promokit import asrcheck
        with mock.patch.object(asrcheck, "RULE", 99):                    # ölçüt değişti → eski kararlar geçersiz
            self.assertNotEqual(a, voice._digest("Hello.", cfg, "chatterbox"))

    def test_say_goes_to_the_engine_subtitles_keep_text(self):
        """`say`: motora giden okunuş (Chatterbox "Mac on the desk, you on the couch." satırında 4 denemede de sonu
        TEKRARLADI; "Mac on the desk; you, on the couch." ilk denemede temiz — ölçüldü). Altyazı `text`le kalır."""
        tmp = tempfile.mkdtemp(prefix="promo-voice-say-")
        wav = _tone(os.path.join(tmp, "t.wav"), 440, 0.4)
        runs = []

        def fake_run(cmd, **kw):
            with open(cmd[-1], encoding="utf-8") as f:
                jobs = json.load(f)
            runs.append(jobs)
            for j in jobs:
                import shutil
                shutil.copy(wav, j["out"])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        ln = voice.Line(0.0, "Mac on the desk, you on the couch.", None, 0, subs={"tr": "Mac masada, siz kanepede."},
                        say="Mac on the desk; you, on the couch.")
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), mock.patch.object(voice, "_run_chatterbox", fake_run), \
                mock.patch.object(voice, "CHATTERBOX_PY", sys.executable):
            voice.prepare([ln], voice.VoiceCfg(engine="chatterbox", lang="en", lexicon={"ADB": "A-D-B"}))
        self.assertEqual(runs[0][0]["text"], "Mac on the desk; you, on the couch.")
        from promokit.subs import srt
        self.assertIn("Mac on the desk, you on the couch.", srt([ln], "en", "en"))
        self.assertEqual(voice.spoken("No ADB.", {"ADB": "A-D-B"}, "en"), "No A-D-B.")    # İngilizcede de sözlük

    def test_tail_never_leaks_into_the_trim_pad(self):
        """Kuyruk ("…speaker. Bad day!") son sözcükten hemen sonra başlıyorsa kırpma payı onun başında durur."""
        tmp = tempfile.mkdtemp(prefix="promo-voice-cbx-next-")
        wav = _tone(os.path.join(tmp, "t.wav"), 440, 3.0)

        def fake_run(cmd, **kw):
            with open(cmd[-1], encoding="utf-8") as f:
                jobs = json.load(f)
            for j in jobs:
                import shutil
                shutil.copy(wav, j["out"])
                with open(j["out"] + ".json", "w", encoding="utf-8") as f:
                    json.dump({"verified": True, "ok": False, "ratio": 1.0, "heard": "x", "start": 0.0, "end": 1.0,
                               "next": 1.08, "tries": 4}, f)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        ln = voice.Line(0.0, "Each tablet is its own speaker.", None, 0)
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), mock.patch.object(voice, "_run_chatterbox", fake_run), \
                mock.patch.object(voice, "CHATTERBOX_PY", sys.executable):
            voice.prepare([ln], voice.VoiceCfg(engine="chatterbox", lang="en"))
        self.assertAlmostEqual(ln.dur, 1.06, delta=0.02)

    def test_missing_environment_says_how_to_install(self):
        """Ortam (work/tts-venv, gitignore'lu) temiz kopyada YOK → model süreci denenmeden, kurulum komutuyla dur."""
        tmp = tempfile.mkdtemp(prefix="promo-voice-cbx-none-")
        called = []
        with mock.patch.object(voice, "CACHE", os.path.join(tmp, "c")), \
                mock.patch.object(voice, "CHATTERBOX_PY", os.path.join(tmp, "yok", "python")), \
                mock.patch.object(voice, "_run_chatterbox", lambda *a, **k: called.append(a)):
            with self.assertRaises(PromoError) as cm:
                voice.prepare([voice.Line(0.0, "Hello.", None, 0)], voice.VoiceCfg(engine="chatterbox", lang="en"))
        self.assertIn("pip install chatterbox-tts", str(cm.exception))
        self.assertEqual(called, [])


class TestElevenLabs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="promo-voice-cache-")
        self.mp3 = _tone(os.path.join(self.tmp, "t.mp3"), 440, 0.4)
        self.patches = [mock.patch.object(voice, "CACHE", os.path.join(self.tmp, "cache")),
                        mock.patch.object(voice, "KEY_FILE", os.path.join(self.tmp, "nokey")),
                        mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "sk-test"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_request_and_cache(self):
        sent = []
        data = open(self.mp3, "rb").read()

        def fake(req, timeout=None):
            sent.append(req)
            return io.BytesIO(data)
        cfg = voice.VoiceCfg(engine="elevenlabs", id="abc123", stability=0.4, similarity=0.8, style=0.3, speed=1.05)
        with mock.patch.object(voice, "urlopen", fake):
            p1 = voice.synth("Tabletiniz o ekran olsun.", cfg)
            p2 = voice.synth("Tabletiniz o ekran olsun.", cfg)       # önbellek: ikinci istek YOK
        self.assertEqual(p1, p2)
        self.assertEqual(len(sent), 1)
        req = sent[0]
        self.assertTrue(req.full_url.startswith("https://api.elevenlabs.io/v1/text-to-speech/abc123"))
        self.assertEqual(req.get_header("Xi-api-key"), "sk-test")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["text"], "Tabletiniz o ekran olsun.")
        self.assertEqual(body["model_id"], "eleven_multilingual_v2")
        self.assertEqual(body["voice_settings"]["stability"], 0.4)
        self.assertEqual(body["voice_settings"]["similarity_boost"], 0.8)
        self.assertEqual(body["voice_settings"]["speed"], 1.05)
        self.assertAlmostEqual(voice.duration(p1), 0.4, delta=0.08)
        # metin ya da ayar değişince YENİ üretim (önbellek anahtarı ikisini de kapsar)
        with mock.patch.object(voice, "urlopen", fake):
            voice.synth("Tabletiniz o ekran olsun!", cfg)
            voice.synth("Tabletiniz o ekran olsun.", voice.VoiceCfg(engine="elevenlabs", id="abc123", speed=1.0))
        self.assertEqual(len(sent), 3)

    def test_key_from_file(self):
        """Ortam değişkeni yoksa anahtar dosyası (repo DIŞINDA) okunur."""
        with open(voice.KEY_FILE, "w", encoding="utf-8") as f:
            f.write("sk-dosyadan\n")
        sent = []
        with open(self.mp3, "rb") as f:
            data = f.read()

        def fake(req, timeout=None):
            sent.append(req)
            return io.BytesIO(data)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(voice, "urlopen", fake):
            try:
                voice.synth("Dosyadan anahtar.", voice.VoiceCfg(engine="elevenlabs", id="abc123"))
            except PromoError as e:              # okunmadıysa İDDİA olarak düşsün (mutasyon ölçümü çökme saymaz)
                self.fail(f"anahtar dosyadan okunmadı: {e}")
        self.assertEqual(sent[0].get_header("Xi-api-key"), "sk-dosyadan")

    def test_missing_key_says_where_to_put_it(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PromoError) as cm:
                voice.synth("Merhaba", voice.VoiceCfg(engine="elevenlabs", id="abc123"))
        self.assertIn("ELEVENLABS_API_KEY", str(cm.exception))
        self.assertIn(voice.KEY_FILE, str(cm.exception))

    def test_missing_voice_id(self):
        with self.assertRaises(PromoError) as cm:
            voice.synth("Merhaba", voice.VoiceCfg(engine="elevenlabs", id=""))
        self.assertIn("id", str(cm.exception))


class TestGoogle(unittest.TestCase):
    """Google Cloud Text-to-Speech (kullanıcı kararı, 2026-10-20: ücretsiz kota + doğal Türkçe)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="promo-voice-g-")
        self.wav = _tone(os.path.join(self.tmp, "t.wav"), 440, 0.5)
        self.patches = [mock.patch.object(voice, "CACHE", os.path.join(self.tmp, "cache")),
                        mock.patch.object(voice, "GOOGLE_KEY_FILE", os.path.join(self.tmp, "nokey")),
                        mock.patch.dict(os.environ, {"GOOGLE_TTS_API_KEY": "g-test"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _reply(self):
        import base64
        return io.BytesIO(json.dumps({"audioContent": base64.b64encode(open(self.wav, "rb").read()).decode()}).encode())

    def test_request_and_cache(self):
        sent = []

        def fake(req, timeout=None):
            sent.append(req)
            return self._reply()
        cfg = voice.VoiceCfg(engine="google", id="tr-TR-Chirp3-HD-Charon", speed=1.1)
        with mock.patch.object(voice, "urlopen", fake):
            p1 = voice.synth("Tabletiniz o ekran olsun.", cfg)
            p2 = voice.synth("Tabletiniz o ekran olsun.", cfg)
        self.assertEqual(p1, p2)
        self.assertEqual(len(sent), 1)                                   # önbellek: yeniden istek yok
        req = sent[0]
        self.assertEqual(req.full_url, "https://texttospeech.googleapis.com/v1/text:synthesize")   # anahtar URL'de DEĞİL
        self.assertEqual(req.get_header("X-goog-api-key"), "g-test")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["input"]["text"], "Tabletiniz o ekran olsun.")
        self.assertEqual(body["voice"], {"languageCode": "tr-TR", "name": "tr-TR-Chirp3-HD-Charon"})
        self.assertEqual(body["audioConfig"]["speakingRate"], 1.1)
        self.assertAlmostEqual(voice.duration(p1), 0.5, delta=0.05)

    def test_rate_rejected_falls_back_once(self):
        """Bazı sesler hız ayarını kabul etmeyebilir → bir kez hızsız dener (uyarıyla), ikinci 400'de hata."""
        from urllib.error import HTTPError
        calls = []

        def fake(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            calls.append(body)
            if "speakingRate" in body["audioConfig"]:
                raise HTTPError(req.full_url, 400, "Bad Request", {},
                                io.BytesIO(b'{"error": {"message": "This voice does not support speaking rate"}}'))
            return self._reply()
        with mock.patch.object(voice, "urlopen", fake):
            p = voice.synth("Hız denemesi.", voice.VoiceCfg(engine="google", id="tr-TR-Chirp3-HD-Charon", speed=1.1))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("speakingRate", calls[1]["audioConfig"])
        self.assertTrue(os.path.exists(p))

    def test_missing_key_says_where_to_put_it(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PromoError) as cm:
                voice.synth("Merhaba", voice.VoiceCfg(engine="google", id="tr-TR-Chirp3-HD-Charon"))
        self.assertIn("GOOGLE_TTS_API_KEY", str(cm.exception))
        self.assertIn(voice.GOOGLE_KEY_FILE, str(cm.exception))

    def test_list_turkish_voices(self):
        sent = []

        def fake(req, timeout=None):
            sent.append(req)
            return io.BytesIO(json.dumps({"voices": [
                {"name": "tr-TR-Chirp3-HD-Charon", "ssmlGender": "MALE", "languageCodes": ["tr-TR"]},
                {"name": "tr-TR-Wavenet-A", "ssmlGender": "FEMALE", "languageCodes": ["tr-TR"]}]}).encode())
        with mock.patch.object(voice, "urlopen", fake):
            vs = voice.list_voices("google")
        self.assertIn("languageCode=tr-TR", sent[0].full_url)
        self.assertEqual([v[0] for v in vs], ["tr-TR-Chirp3-HD-Charon", "tr-TR-Wavenet-A"])
        self.assertEqual(vs[0][2].get("cinsiyet"), "MALE")


@unittest.skipUnless(sys.platform == "darwin" and os.path.exists("/usr/bin/say"), "macOS `say` yok")
class TestSay(unittest.TestCase):
    def test_local_draft_voice(self):
        tmp = tempfile.mkdtemp(prefix="promo-voice-say-")
        with mock.patch.object(voice, "CACHE", tmp):
            p = voice.synth("Merhaba, bu bir deneme.", voice.VoiceCfg(engine="say", id="Yelda"))
        self.assertGreater(voice.duration(p), 0.6)


class TestMix(unittest.TestCase):
    def test_graph(self):
        L = voice.Line
        lines = [L(1.0, "a", None, 0, path="/x/a.wav", dur=0.5), L(3.5, "b", None, 0, path="/x/b.wav", dur=0.5)]
        g = voice.audio_graph(lines, t_from=0.0, dur=6.0, music="/x/m.mp3", music_volume=0.8, vo_volume=1.0, first_input=1)
        self.assertEqual(g.inputs[0][-1], "/x/m.mp3")
        self.assertIn("adelay=1000|1000", g.filter)
        self.assertIn("adelay=3500|3500", g.filter)
        self.assertIn("sidechaincompress", g.filter)     # müzik konuşurken kısılır
        self.assertIn("loudnorm", g.filter)
        g2 = voice.audio_graph(lines, t_from=2.0, dur=4.0, music=None, music_volume=0.8, vo_volume=1.0, first_input=1)
        self.assertNotIn("sidechaincompress", g2.filter)
        self.assertIn("adelay=1500|1500", g2.filter)     # kısmi render: başlangıca göre
        self.assertEqual(len(g2.inputs), 1)               # 1.0+0.5 < 2.0 → ilk satır aralık dışında

    def test_music_fade_waits_for_the_final(self):
        """Fon kendi finalini çözüyorsa (`music_final`) karıştırıcının sönmesi finalden ÖNCE başlamaz. Kartsız Short'ta
        1,5 sn'lik sönme 9,1'de başlıyordu: 9,6'daki final vuruşu kısılmış sese düşüyor ve 1 sn'de sıfıra iniyordu ("ses
        hâlâ uyumsuz, sonlarda bir anda kesiliyor", kullanıcı 2026-10-25). Kuyruk uzunsa (kart) ve final bilinmiyorsa eski."""
        g = voice.audio_graph([], 0.0, 10.6, "/x/m.wav", 0.8, 1.0, 1, music_final=9.6)
        self.assertIn("afade=t=out:st=10.10:d=0.50", g.filter)
        g = voice.audio_graph([], 0.0, 10.6, "/x/m.wav", 0.8, 1.0, 1, music_final=7.0)
        self.assertIn("afade=t=out:st=9.10:d=1.50", g.filter)
        g = voice.audio_graph([], 0.0, 10.6, "/x/m.wav", 0.8, 1.0, 1)
        self.assertIn("afade=t=out:st=9.10:d=1.5", g.filter)

    def test_render_ducks_music_under_voice(self):
        """Uçtan uca: 300 Hz "müzik" + 1 kHz "konuşma" (1,0-2,0 sn). Konuşurken müzik ≥ 6 dB kısılır."""
        from promokit.render import render
        d = tempfile.mkdtemp(prefix="promo-voice-mix-")
        _tone(os.path.join(d, "m.wav"), 300, 4.0)
        _tone(os.path.join(d, "v.wav"), 1000, 1.0)
        Image.new("RGB", (300, 200), (40, 90, 160)).save(os.path.join(d, "a.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""
                [video]
                size = 360
                fps = 10
                music = "m.wav"
                [[scene]]
                duration = 3.0
                tablet = "a.png"
                voice = [ { t = 1.0, src = "v.wav" } ]
            """))
        out = os.path.join(d, "o.mp4")
        render(p, out, {"jobs": 1})

        def rms(band, t0, t1):
            # astats özetini INFO düzeyinde basar → `-v error` onu yutar
            r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-ss", str(t0), "-t", str(t1 - t0), "-i", out,
                                "-af", f"bandpass=f={band}:w=60,astats", "-f", "null", "-"],
                               capture_output=True, text=True)
            vals = [float(x.split(":")[1]) for x in r.stderr.splitlines() if "RMS level dB" in x and "inf" not in x]
            return vals[-1] if vals else -120.0
        before, during = rms(300, 0.3, 0.9), rms(300, 1.2, 1.8)
        self.assertGreater(rms(1000, 1.2, 1.8), -40)           # konuşma duyulur
        self.assertLess(during, before - 6, (before, during))  # müzik konuşurken kısılır

    def test_music_continues_after_last_voice_line(self):
        """Son konuşma satırı videodan ÖNCE bitince müzik sürer. `sidechaincompress` girişlerinden biri (konuşma anahtarı)
        bitince çıktısını da kesiyordu → Mac mini kısasında 7,2 sn'den sonu (3,4 sn) TAMAMEN sessizdi ("sonlarda bir anda
        kesiliyor ses", kullanıcı 2026-10-25). Önceki test yalnız konuşma ÖNCESİ ve SIRASINI ölçüyordu."""
        from promokit.render import render
        d = tempfile.mkdtemp(prefix="promo-voice-tail-")
        _tone(os.path.join(d, "m.wav"), 300, 6.0)
        _tone(os.path.join(d, "v.wav"), 1000, 1.0)
        Image.new("RGB", (300, 200), (40, 90, 160)).save(os.path.join(d, "a.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""
                [video]
                size = 360
                fps = 10
                music = "m.wav"
                [[scene]]
                duration = 5.0
                tablet = "a.png"
                voice = [ { t = 1.0, src = "v.wav" } ]
            """))
        out = os.path.join(d, "o.mp4")
        render(p, out, {"jobs": 1})

        def rms(t0, t1):
            r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-ss", str(t0), "-t", str(t1 - t0), "-i", out,
                                "-af", "bandpass=f=300:w=60,astats", "-f", "null", "-"], capture_output=True, text=True)
            vals = [float(x.split(":")[1]) for x in r.stderr.splitlines() if "RMS level dB" in x and "inf" not in x]
            return vals[-1] if vals else -120.0
        before, after = rms(0.3, 0.9), rms(2.6, 3.2)                 # sönme 3,5'te başlar
        self.assertGreater(after, before - 4, (before, after))

    def test_levels_follow_measured_loudness(self):
        """Satırlar VO_LUFS'a, müzik VO_LUFS − BED_DB'ye: ölçülen yüksekliğe göre sabit kazanç (Chatterbox satırları
        −19…−22 LUFS ölçüldü; indirilen parçalar −10…−14 LUFS mastering'li)."""
        a = voice.Line(0.0, "a", None, 0, dur=1.0, path="/x/a.wav", lufs=voice.VO_LUFS + 4)
        b = voice.Line(2.0, "b", None, 0, dur=1.0, path="/x/b.wav", lufs=voice.VO_LUFS - 4)
        loud = voice.audio_graph([a, b], 0.0, 4.0, "/x/m.wav", 1.0, 1.0, 1, music_lufs=voice.VO_LUFS - voice.BED_DB + 6)
        self.assertIn(f"volume={10 ** (-4 / 20):.3f}[v0]", loud.filter)
        self.assertIn(f"volume={10 ** (4 / 20):.3f}[v1]", loud.filter)
        self.assertIn(f"volume={10 ** (-6 / 20):.3f},apad[mus]", loud.filter)
        raw = voice.audio_graph([a], 0.0, 4.0, "/x/m.wav", 0.5, 1.0, 1)             # ölçüm yok → ham çarpan
        self.assertIn("volume=0.500,apad[mus]", raw.filter)

    def test_loud_and_quiet_tracks_sit_at_the_same_level(self):
        """Uçtan uca: 12 dB farklı iki parça → konuşmadan ÖNCEKİ müzik seviyesi aynı (±1,5 dB). `--music` ile
        senaryoya dokunmadan verilir."""
        from promokit.render import render
        d = tempfile.mkdtemp(prefix="promo-voice-level-")
        loud = _tone(os.path.join(d, "loud.wav"), 300, 4.0, ("-af", "volume=0.9"))
        quiet = _tone(os.path.join(d, "quiet.wav"), 300, 4.0, ("-af", "volume=0.225"))
        _tone(os.path.join(d, "v.wav"), 1000, 1.0)
        Image.new("RGB", (300, 200), (40, 90, 160)).save(os.path.join(d, "a.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""
                [video]
                size = 360
                fps = 10
                [[scene]]
                duration = 3.0
                tablet = "a.png"
                voice = [ { t = 1.6, src = "v.wav" } ]
            """))

        def level(music):
            out = os.path.join(d, os.path.basename(music) + ".mp4")
            render(p, out, {"jobs": 1, "music": music})
            r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-ss", "0.7", "-t", "0.6", "-i", out,
                                "-af", "bandpass=f=300:w=60,astats", "-f", "null", "-"], capture_output=True, text=True)
            vals = [float(x.split(":")[1]) for x in r.stderr.splitlines() if "RMS level dB" in x and "inf" not in x]
            return vals[-1]
        self.assertAlmostEqual(level(loud), level(quiet), delta=1.5)
        with self.assertRaises(PromoError):
            render(p, os.path.join(d, "x.mp4"), {"jobs": 1, "music": os.path.join(d, "yok.wav")})


class TestMusicBed(unittest.TestCase):
    """Yer tutucu fon müziği (telifsiz, burada üretilir): belirlenimci, istenen süre, kırpılmasız, sessiz değil."""

    def test_bed(self):
        from promokit import musicbed
        d = tempfile.mkdtemp(prefix="promo-bed-")
        a = musicbed.write_bed(os.path.join(d, "a.wav"), 4.0, 100.0, 1)
        b = musicbed.write_bed(os.path.join(d, "b.wav"), 4.0, 100.0, 1)
        c = musicbed.write_bed(os.path.join(d, "c.wav"), 4.0, 100.0, 2)
        with open(a, "rb") as f1, open(b, "rb") as f2, open(c, "rb") as f3:
            ra, rb, rc = f1.read(), f2.read(), f3.read()
        self.assertEqual(ra, rb)
        self.assertNotEqual(ra, rc)                                   # tohum gürültü katmanlarını değiştirir
        self.assertAlmostEqual(voice.duration(a), 4.0, delta=0.01)
        import wave as wv
        with wv.open(a, "rb") as w:
            self.assertEqual(w.getnchannels(), 2)
            import array
            x = array.array("h", w.readframes(w.getnframes()))
        peak = max(abs(v) for v in x) / 32767
        self.assertLessEqual(peak, 10 ** (-0.9 / 20))
        self.assertGreater(peak, 0.5)
        lufs = voice.loudness(a)
        self.assertIsNotNone(lufs)
        self.assertGreater(lufs, -30.0)

    def test_arrangement_resolves_on_the_outro(self):
        """Kullanıcı (2026-10-20): "müziğin başlangıç ve bitişini değiştirecektin". Ölçü ızgarası kapanış kartının
        başına HİZALI: o an TONİK (Fmaj7) final, önceki ölçü IV (B♭) — müzik kartta çözülür; döngü kesilip
        karartılmaz. Giriş: kısada 1, uzunda 2 ölçü (davul/bas yok). Uzunda 4 ölçülük A ↔ B bölümleri."""
        from promokit import musicbed
        bar = 240 / 100.0
        plan = musicbed.arrangement(26.4, 100.0, ending=23.8)
        self.assertEqual(plan[-1], (23.8, 0, "final"))
        self.assertEqual(plan[-2][1:], (3, "groove"))                       # IV → I
        self.assertAlmostEqual(plan[-2][0], 23.8 - bar, places=6)
        self.assertEqual([p for _, _, p in plan[:2]], ["intro", "intro"])
        self.assertNotIn("intro", [p for _, _, p in plan[2:]])
        self.assertLessEqual(plan[0][0], 0.0)                                # ilk ölçü videonun başını kapsar
        chords = [c for _, c, p in plan if p != "final"]
        self.assertEqual(chords[-4:], [0, 1, 2, 3])                          # son bölüm A: I–V–vi–IV
        self.assertEqual(chords[-8:-4], [2, 3, 0, 1])                        # önceki bölüm B: vi–IV–I–V
        short = musicbed.arrangement(9.7, 100.0, ending=7.1)
        self.assertEqual(short[-1], (7.1, 0, "final"))
        self.assertEqual([p for _, _, p in short].count("intro"), 1)
        self.assertEqual([c for _, c, p in short], [1, 2, 3, 0])            # V–vi–IV → kartta I (kısada B yok)

    def test_bed_has_soft_intro_and_rings_out(self):
        from promokit import musicbed

        def rms(x, a, b):
            seg = x[int(a * musicbed.SR):int(b * musicbed.SR)]
            return (sum(v * v for v in seg) / max(1, len(seg))) ** 0.5
        L, _ = musicbed.render_bed(9.0, 100.0, 1, ending=6.6)
        groove = rms(L, 2.2, 6.2)
        self.assertLess(rms(L, 0.0, 1.2), 0.6 * groove)                     # yumuşak giriş (davul/bas yok)
        self.assertGreater(rms(L, 0.1, 0.6), 0.05 * groove)                  # ama HEMEN başlar (kısmi ilk ölçü çalar)
        self.assertLess(rms(L, 8.4, 9.0), 0.6 * rms(L, 6.6, 7.2))            # final akoru doğal söner
        self.assertGreater(rms(L, 8.4, 9.0), 0.0)                            # kesilmez, söner

    # varsayılan ("bright") fonun özetleri — 2026-10-25'te ruh hâlleri eklenmeden ÖNCE alındı
    GOLDEN = {(9.0, 6.6): "f9b29a60b3d4ea7acb26ba2fbd2655ea619ce2f698a4be205cddb581cea285e1",
              (26.4, 23.8): "d2013234b240e1df9cfe563f88254cace879b6f9c5f5cc31d29a1d8f7b57eb2b"}

    @staticmethod
    def _sha(L, R):
        import hashlib
        import struct
        return hashlib.sha256(b"".join(struct.pack("<hh", int(a * 32767), int(b * 32767)) for a, b in zip(L, R))).hexdigest()

    def test_default_mood_is_byte_identical(self):
        """Varsayılan ruh hâli ("bright") ESKİ fonun bayt bayt aynısı: yayındaki/zamanlanmış videolar yeniden üretilirse
        müzikleri değişmesin."""
        from promokit import musicbed
        for (dur, end), h in self.GOLDEN.items():
            self.assertEqual(self._sha(*musicbed.render_bed(dur, 100.0, 1, ending=end)), h)
            self.assertEqual(self._sha(*musicbed.render_bed(dur, 100.0, 1, ending=end, mood="bright")), h)

    def test_moods_voice_their_chords(self):
        """Kullanıcı (2026-10-25): "içeriğe göre majörler minörler 7'liler vs akorlar çeşitlensin". Ruh hâllerinin
        akorları SEMBOLDEN seslenir: pedin sesleri akorun kendi sesleri; niteliği belirleyen üçlü (minörde küçük,
        majörde büyük; sus'ta yok) ve varsa 7'li/9'lu pedde; tını dar (≤ 15 yarım ses), yan yana yarım ses sürtüşmesi
        yok; arp pedin bir oktav üstü; bas kökte. Minör VE majör tonlu ruh hâlleri birlikte var."""
        from promokit import musicbed
        # koddan BAĞIMSIZ beklenti (tablo yanlışsa aşağıdaki seslendirme kontrolleri kendi tutarlılığını ölçerdi)
        known = {"Am7": {9, 0, 4, 7}, "G7": {7, 11, 2, 5}, "Dm9": {2, 5, 9, 0, 4}, "Bbmaj7": {10, 2, 5, 9},
                 "Csus2": {0, 2, 7}, "Ebmaj7": {3, 7, 10, 2}, "G6": {7, 11, 2, 4}, "Gadd9": {7, 11, 2, 9},
                 "Cm7": {0, 3, 7, 10}, "Bb7sus4": {10, 3, 5, 8}, "Abmaj9": {8, 0, 3, 7, 10}, "C": {0, 4, 7}}
        for sym, pcs in known.items():
            root, iv = musicbed.parse_chord(sym)
            self.assertEqual({(root + i) % 12 for i in iv}, pcs, sym)
        moods = [m for m in musicbed.MOODS if m != "bright"]
        self.assertGreaterEqual(len(moods), 4)
        qualities = set()
        for m in moods:
            syms = musicbed.MOODS[m]["chords"]
            for sym, (bass, pad, arp) in zip(syms, musicbed.voice_chords(syms)):
                root, iv = musicbed.parse_chord(sym)
                pcs, padpc = {(root + i) % 12 for i in iv}, {n % 12 for n in pad}
                with self.subTest(mood=m, chord=sym):
                    self.assertTrue(padpc <= pcs, (pad, pcs))
                    for i in iv:
                        if i in (3, 4, 10, 11, 14):
                            self.assertIn((root + i) % 12, padpc, (pad, i))
                    self.assertLessEqual(pad[-1] - pad[0], 15)
                    self.assertTrue(all(b - a >= 2 for a, b in zip(pad, pad[1:])), pad)
                    self.assertEqual(tuple(n + 12 for n in pad), tuple(arp))
                    self.assertEqual(bass % 12, root)
                qualities.add(sym.lstrip("ABCDEFG").lstrip("#b"))
        self.assertTrue({"m7", "maj7", "7"} <= qualities, qualities)
        minor_tonic = {m: 3 in musicbed.parse_chord(musicbed.MOODS[m]["chords"][0])[1] for m in moods}
        self.assertTrue(any(minor_tonic.values()) and not all(minor_tonic.values()), minor_tonic)
        with self.assertRaises(ValueError):
            musicbed.parse_chord("Hm7")

    def test_mood_from_story_changes_music(self):
        """`[video] music_mood`: senaryonun içeriğine göre seçilir, bilinmeyen ad YÜKLEMEDE hata. Ruh hâli önbellek
        adına girer ve fon gerçekten değişir."""
        from promokit import musicbed, story as story_mod
        d = tempfile.mkdtemp(prefix="promo-mood-")
        Image.new("RGB", (300, 200), (40, 60, 90)).save(os.path.join(d, "a.png"))

        def load(extra):
            p = os.path.join(d, "s.toml")
            with open(p, "w") as f:
                f.write(f'[video]\nsize = 360\nfps = 30\nmusic = "bed"\n{extra}[[scene]]\nduration = 4\ntablet = "a.png"\n')
            return story_mod.load(p)
        bright, night = load(""), load('music_mood = "night"\n')
        self.assertEqual((bright.video.music_mood, night.video.music_mood), ("bright", "night"))
        a, b = musicbed.story_bed(bright, cache=d), musicbed.story_bed(night, cache=d)
        self.assertIn("night", os.path.basename(b))
        with open(a, "rb") as f1, open(b, "rb") as f2:
            self.assertNotEqual(f1.read(), f2.read())
        with self.assertRaises(PromoError):
            load('music_mood = "nigth"\n')

    def test_short_tail_final_rings_out_inside_the_video(self):
        """Kapanış satırı ~1 sn (kartsız Short): final akoru o süre İÇİNDE doğal söner. Önce 1,6 sn'lik sönüm videonun
        sonunda kesiliyordu ("sonlarda bir anda kesiliyor ses", kullanıcı 2026-10-25). Uzun kuyruk (kart) değişmez
        (golden test)."""
        from promokit import musicbed

        def rms(x, a, b):
            seg = x[int(a * musicbed.SR):int(b * musicbed.SR)]
            return (sum(v * v for v in seg) / max(1, len(seg))) ** 0.5
        L, _ = musicbed.render_bed(10.6, 92.0, 1, ending=9.6, mood="night")
        hit = rms(L, 9.6, 9.8)
        self.assertGreater(hit, 0.05)
        self.assertLess(rms(L, 10.45, 10.6), 0.15 * hit)

    def test_cardless_short_resolves_on_the_closing_line(self):
        """Kartsız Short (kullanıcı, 2026-10-25: sonda logo/kart YOK, "free" ~1 sn tek satır): müzik, son sahnenin videonun
        SONUNA kadar süren son başlığında çözülür. Önce son ölçüye düşüyordu: final vuruşu yazı yazılırken geldi ("son
        kısımda ses görüntü tutarsızlığı var"). Sona varmayan son başlık → eski davranış (son ölçü)."""
        from promokit import musicbed, story as story_mod
        d = tempfile.mkdtemp(prefix="promo-close-")
        Image.new("RGB", (300, 200), (40, 60, 90)).save(os.path.join(d, "a.png"))

        def load(last_d):
            p = os.path.join(d, "s.toml")
            with open(p, "w") as f:
                f.write('[video]\nsize = 360\nfps = 30\ntransition = "cut"\nmusic = "bed"\n'
                        '[[scene]]\nduration = 2\ntablet = "a.png"\n[[scene]]\nduration = 6\ntablet = "a.png"\n'
                        f'captions = [ {{ t = 0.0, d = 4.8, text = "a" }}, {{ t = 4.8, d = {last_d}, text = "end" }} ]\n')
            return story_mod.load(p)
        self.assertEqual(musicbed.story_params(load(1.2)), (8.0, 6.8))
        self.assertEqual(musicbed.story_params(load(0.8)), (8.0, None))

    def test_story_bed_lands_on_the_outro_card(self):
        """`--music bed` / `[video] music = "bed"`: müzik o videoya özel üretilir — süre = video, final = kapanış
        kartının başı (kartsız videoda son ölçü)."""
        from promokit import musicbed, story as story_mod
        d = tempfile.mkdtemp(prefix="promo-bedst-")
        Image.new("RGB", (300, 200), (40, 60, 90)).save(os.path.join(d, "a.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w") as f:
            f.write('[video]\nsize = 360\nfps = 30\ntransition = "cut"\nmusic = "bed"\n[[scene]]\nduration = 4\ntablet = "a.png"\n'
                    '[[scene]]\ntype = "outro"\nduration = 2.5\ntitle = "x"\n')
        st = story_mod.load(p)
        self.assertEqual(st.video.music, "bed")
        self.assertEqual(musicbed.story_params(st), (6.5, 4.0))
        path = musicbed.story_bed(st, cache=d)
        self.assertAlmostEqual(voice.duration(path), 6.5, delta=0.01)
        self.assertEqual(musicbed.story_bed(st, cache=d), path)              # önbellek: aynı dosya


if __name__ == "__main__":
    unittest.main()
