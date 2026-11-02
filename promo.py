#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
promokit — senaryodan (TOML) ürün tanıtım videosu üreten araç

Akış:  YAKALA  →  SENARYO  →  RENDER
  1) capture  : Mac ekranını (ScreenCaptureKit) / Android ekranını (adb) kaydet;
                isteğe bağlı olarak Android'deki GERÇEK dokunuşları (getevent) eşzamanlı yakala
  2) senaryo  : TOML dosyası — sahneler, cihaz yerleşimi, jestler, başlıklar, kamera
  3) render   : cihaz mockup'ı + ekranda kayıt/HTML sayfa + jestleri gösteren daireler → MP4

Komutlar:
  render  STORY.toml [-o out.mp4] [--aspect 9:16] [--size 1080] [--fps 60] [-j 6] [--preview]
  still   STORY.toml -t 3.2 [-o kare.png]        tek kare (hızlı deneme)
  stills  STORY.toml [--format jpg --max-kb 1900] `still = <sn>` işaretli her sahneden bir kare
  sheet   STORY.toml [--every 1] [-o sheet.png]  kontak sayfası (zamanlama kontrolü)
  info    STORY.toml                             zaman çizelgesi
  voice   STORY.toml                             seslendirmeyi üret (önbellekli) + zamanlamayı denetle
  subs    STORY.toml [--lang tr]                 seslendirmeden altyazı (SRT)
  burn    VIDEO.mp4 [--lang tr] [-o out.mp4]     altyazıyı görüntüye yak
  music   [--dur 60] [--mood bright]             telifsiz yer tutucu fon müziği üret
  grid    MEDYA [-t 2.0] [-o grid.png]           koordinat ızgarası (jest konumu seçmek için)
  capture list | mac | tablet | shot ...         kayıt (ayrıntı: capture -h)
  touches ÇEKİM [--plot out.png] [--offset S]    kaydedilmiş dokunuşları incele / senkronla

Gereksinim: Python 3.11+ (tomllib), Pillow, ffmpeg. Kayıt için: macOS 13+ + Xcode CLT (swiftc), adb.
Ayrıntılı kılavuz: README.md
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from promokit.util import PromoError, log  # noqa: E402


def _opts(a) -> dict:
    return {"aspect": getattr(a, "aspect", None), "size": getattr(a, "size", None), "fps": getattr(a, "fps", None),
            "codec": getattr(a, "codec", None), "crf": getattr(a, "crf", None),
            "preview": getattr(a, "preview", False), "jobs": getattr(a, "jobs", None),
            "from": getattr(a, "t_from", None), "to": getattr(a, "t_to", None),
            "voice_engine": getattr(a, "voice_engine", None), "music": getattr(a, "music", None),
            "music_volume": getattr(a, "music_volume", None)}


OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "out")


def _default_out(story: str, aspect: str | None, ext: str, suffix: str = "") -> str:
    """Çıktılar work/out/[<dil>/] altına (gitignore'lu)."""
    from promokit.render import lang_dir
    from promokit.story import load
    base = os.path.splitext(os.path.basename(story))[0]
    tag = f"-{aspect.replace(':', 'x')}" if aspect else ""
    return os.path.join(lang_dir(OUT_DIR, load(story).video.lang), f"{base}{tag}{suffix}{ext}")


def _voice_cmd(a):
    from promokit import voice as vo
    from promokit.story import load
    engine = a.engine or (load(a.story).voice.engine if a.story else "google")
    if a.list_voices:
        for vid, name, labels in vo.list_voices(engine):
            tags = ", ".join(f"{k}: {v}" for k, v in labels.items())
            print(f"{vid}  {name}" + (f"  ({tags})" if tags else ""))
        return
    if a.try_ids:
        cfg = load(a.story).voice if a.story else vo.VoiceCfg()
        for vid in [x.strip() for x in a.try_ids.split(",") if x.strip()]:
            cfg.id = vid
            print(f"{vid}  {vo.synth(a.text, cfg, engine)}")
        return
    if not a.story:
        raise PromoError("senaryo gerekli: promo.py voice <senaryo.toml>  (ya da --list-voices / --try)")
    st = load(a.story)
    lines = st.voice_lines()
    if not lines:
        raise PromoError("senaryoda seslendirme satırı yok (sahnede voice = [ { t = …, text = \"…\" } ])")
    vo.prepare(lines, st.voice, a.engine)
    for ln in lines:
        print(f"{ln.t:6.2f}–{ln.t + ln.dur:6.2f} sn  #{ln.scene + 1}  {ln.text or os.path.basename(ln.src)}")
    warns = vo.check(lines, st.duration)
    for w in warns:
        log(f"⚠️ {w}")
    log(f"✓ {len(lines)} satır hazır (önbellek: {vo.CACHE})" + (f" · {len(warns)} uyarı" if warns else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="promo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def story_args(p, size_default=None):
        p.add_argument("story", help="senaryo (.toml)")
        p.add_argument("--aspect", help="16:9 | 9:16 | 4:5 | 1:1 (senaryodakini ezer)")
        p.add_argument("--size", type=int, default=size_default, help="kısa kenar px (1080, 1440, 2160)")
        p.add_argument("--fps", type=float)

    p = sub.add_parser("render", help="videoyu üret")
    story_args(p)
    p.add_argument("-o", "--out")
    p.add_argument("-j", "--jobs", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 2)))
    p.add_argument("--codec", choices=["h264", "hevc", "prores"])
    p.add_argument("--crf", type=int)
    p.add_argument("--preview", action="store_true", help="hızlı taslak: 540p, 30 fps, ultrafast")
    p.add_argument("--from", dest="t_from", type=float)
    p.add_argument("--to", dest="t_to", type=float)
    p.add_argument("--voice-engine", choices=["chatterbox", "google", "elevenlabs", "say"], help="seslendirme motorunu ez (say = yerel taslak)")
    p.add_argument("--music", help="fon müziği (senaryodakini ezer; seslendirmeli videoda sesin ~10 dB altına oturur)")
    p.add_argument("--music-volume", type=float, help="müzik çarpanı (1.0 = varsayılan denge)")

    p = sub.add_parser("still", help="tek kare PNG")
    story_args(p)
    p.add_argument("-t", "--time", type=float, required=True)
    p.add_argument("-o", "--out")

    p = sub.add_parser("stills", help="`still = <sn>` işaretli her sahneden bir kare (PNG/JPG)")
    story_args(p)
    p.add_argument("-o", "--out", help="klasör (vars. work/out/[<dil>/]<senaryo>-stills/)")
    p.add_argument("--format", choices=["png", "jpg"], default="png", help="png | jpg")
    p.add_argument("--max-kb", type=float, help="JPG boyut tavanı, KB (ör. 1900)")

    p = sub.add_parser("sheet", help="kontak sayfası")
    story_args(p)
    p.add_argument("--every", type=float, default=1.0)
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("-o", "--out")

    p = sub.add_parser("info", help="zaman çizelgesini yazdır")
    story_args(p)

    p = sub.add_parser("voice", help="seslendirmeyi üret (önbellekli) + zamanlamayı denetle · sesleri listele/dene")
    p.add_argument("story", nargs="?", help="senaryo (.toml)")
    p.add_argument("--engine", choices=["chatterbox", "google", "elevenlabs", "say"], help="senaryodaki motoru ez (say = yerel taslak)")
    p.add_argument("--list-voices", action="store_true", help="motorun Türkçe sesleri (google) / hesaptaki sesler (elevenlabs)")
    p.add_argument("--try", dest="try_ids", help="virgüllü ses kimlikleri: aynı cümleyi her biriyle oku (seçim için)")
    p.add_argument("--text", default="Merhaba, bu bir deneme cümlesidir.")

    p = sub.add_parser("subs", help="seslendirmeden altyazı (SRT)")
    p.add_argument("story", help="senaryo (.toml)")
    p.add_argument("--lang", default="tr", help="tr | en | zh (vars. tr)")
    p.add_argument("--engine", choices=["chatterbox", "google", "elevenlabs", "say"], help="senaryodaki motoru ez")
    p.add_argument("-o", "--out")

    p = sub.add_parser("burn", help="altyazıyı görüntüye yak (Instagram Reels altyazı dosyası almıyor) — video.<dil>.srt → video.<dil>-sub.mp4")
    p.add_argument("video", help="render çıktısı (.mp4)")
    p.add_argument("--lang", default="tr", help="altyazı dili: video.<dil>.srt (vars. tr)")
    p.add_argument("--srt", help="altyazı dosyası (verilmezse video.<dil>.srt)")
    p.add_argument("-o", "--out", help="çıktı (vars. video.<dil>-sub.mp4)")

    p = sub.add_parser("music", help="telifsiz yer tutucu fon müziği üret (gerçek parça seçilene kadar)")
    p.add_argument("--dur", type=float, default=60.0, help="süre, sn (vars. 60)")
    p.add_argument("--bpm", type=float, help="vars. ruh hâlinin temposu")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--mood", default="bright", help="ruh hâli: bright (vars.) · night · drive · warm · dreamy · uplift "
                   "(senaryoda `[video] music_mood`)")
    p.add_argument("-o", "--out")

    p = sub.add_parser("grid", help="medya üstüne koordinat ızgarası")
    p.add_argument("media")
    p.add_argument("-t", "--time", type=float, default=0.0)
    p.add_argument("-o", "--out")

    from promokit import capture as cap_mod
    cap_mod.add_parser(sub)
    from promokit import sync as sync_mod
    sync_mod.add_parser(sub)

    a = ap.parse_args(argv)
    try:
        if a.cmd == "render":
            from promokit.render import render, resolve_output
            o = _opts(a)
            if a.preview:
                o["size"] = 540 if o["size"] is None else o["size"]
                o["fps"] = 30 if o["fps"] is None else o["fps"]
            # uzantı ETKİN kodlayıcıdan (senaryodaki `codec` dahil); `[video] out` varsayılan yol
            out = resolve_output(a.story, a.out, a.aspect, a.codec, a.preview, OUT_DIR)
            render(a.story, out, o)
        elif a.cmd == "still":
            from promokit.render import still
            out = a.out or _default_out(a.story, a.aspect, ".png", f"-t{a.time:g}")
            print(still(a.story, a.time, out, _opts(a)))
        elif a.cmd == "stills":
            from promokit.render import stills
            out = a.out or _default_out(a.story, a.aspect, "", "-stills")
            for path in stills(a.story, out, _opts(a), a.format, a.max_kb):
                print(path)
        elif a.cmd == "sheet":
            from promokit.render import sheet
            out = a.out or _default_out(a.story, a.aspect, ".png", "-sheet")
            print(sheet(a.story, out, _opts(a), a.every, a.cols))
        elif a.cmd == "info":
            from promokit.render import timeline_text
            print(timeline_text(a.story, _opts(a)))
        elif a.cmd == "voice":
            _voice_cmd(a)
        elif a.cmd == "subs":
            from promokit import subs as subs_mod, voice as vo
            from promokit.story import load
            st = load(a.story)
            lines = st.voice_lines()
            if not lines:
                raise PromoError("senaryoda seslendirme satırı yok")
            vo.prepare(lines, st.voice, a.engine)
            out = a.out or _default_out(a.story, None, f".{a.lang}.srt")
            with open(out, "w", encoding="utf-8") as f:
                f.write(subs_mod.srt(lines, a.lang, st.voice.lang))
            print(out)
        elif a.cmd == "burn":
            from promokit import burn as burn_mod
            print(burn_mod.burn(a.video, a.srt or burn_mod.default_srt(a.video, a.lang),
                                a.out or burn_mod.default_out(a.video, a.lang)))
        elif a.cmd == "music":
            from promokit.musicbed import MOODS, write_bed
            if a.mood not in MOODS:
                raise PromoError(f"--mood: {a.mood!r} yok — {', '.join(MOODS)}")
            bpm = a.bpm or float(MOODS[a.mood]["bpm"])
            tag = "" if a.mood == "bright" else f"{a.mood}-"
            out = a.out or os.path.join(os.path.dirname(OUT_DIR), "music", f"bed-{tag}{bpm:g}bpm-s{a.seed}.wav")
            print(write_bed(out, a.dur, bpm, a.seed, mood=a.mood))
        elif a.cmd == "grid":
            from promokit.grid import grid
            out = a.out or os.path.splitext(a.media)[0] + "-grid.png"
            print(grid(a.media, a.time, out))
        elif a.cmd == "capture":
            cap_mod.run(a)
        elif a.cmd == "touches":
            sync_mod.run(a)
    except PromoError as e:
        log(f"✗ {e}")
        return 1
    except KeyboardInterrupt:
        log("\n✗ iptal")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
