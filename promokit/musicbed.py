"""Telifsiz YER TUTUCU fon müziği — saf Python sentezi (stdlib; numpy YOK, util.py kuralı).

Kullanıcı (2026-10-20): "Müzik de olsun". Gerçek parça (YouTube Ses Kitaplığı "atıf gerekmez" ya da
Pixabay Music) seçilene kadar videolar bununla dinlenir; lisans sorusu doğurmaz (her örnek burada üretilir).
Belirlenimci: aynı ayar → bayt bayt aynı dosya.

    python3 promo.py render <senaryo> --music bed   # o videoya ÖZEL (ya da [video] music = "bed")
    python3 promo.py music                           # genel 60 sn'lik dosya (work/music/)

Düzen (kullanıcı, 2026-10-20: "müziğin başlangıç ve bitişini değiştirecektin"): yumuşak GİRİŞ (davul/bas yok;
kısada 1, uzunda 2 ölçü) → akış → kapanış kartının başında TONİKTE çözülen FİNAL (IV → I, tek vuruş, doğal
sönüm). Ölçü ızgarası karta hizalanır; uzun videoda 4 ölçülük A (I–V–vi–IV) ↔ B (vi–IV–I–V) bölümleri.

Kurgu: F majör I–V–vi–IV (Fmaj7 · C(add9) · Dm7 · B♭maj7), bar başına bir akor. Katmanlar: sıcak ped (iki
hafif ayrık akortlu ses, yumuşak atak, alçak geçiren) · 16'lık arp (pluck, ping-pong yankı) · bas (akor
kökü, 8'likler) · yumuşak davul (1-3 kick, 2-4 clap, 8'lik hi-hat). Kick'te ped/arp/bas "nefes alır"
(sidechain) — reklam fonlarının tanıdık nabzı. Seslendirmenin altında kalacak kadar sade: melodi YOK.
"""
import itertools
import math
import os
import random
import re
import struct
import wave

SR = 24000                      # fon için yeterli (en üst katman hi-hat ~10 kHz); karışım 48 kHz'e çevrilir
TAB = 4096
_MASK = TAB - 1

CHORDS = [                      # (bas kökü, ped sesleri, arp sesleri) — MIDI
    (41, (53, 57, 60, 64), (65, 69, 72, 76)),    # Fmaj7
    (36, (52, 55, 60, 62), (64, 67, 72, 74)),    # C(add9)
    (38, (53, 57, 60, 62), (65, 69, 72, 74)),    # Dm7
    (34, (53, 57, 58, 62), (65, 69, 70, 74)),    # B♭maj7
]
ARP = (0, 1, 2, 3, 2, 1, 2, 3, 0, 1, 2, 3, 3, 2, 1, 2)     # bar başına 16 adım (akor sesi sırası)

# ── Ruh hâlleri (kullanıcı, 2026-10-25: "hep aynı müziği kullanıyoruz" → "içeriğe göre majörler minörler 7'liler vs
# akorlar çeşitlensin"). Senaryo içeriğine göre `[video] music_mood` seçer: ton + yürüyüş (akor SEMBOLLERİ, sondaki
# akor toniğe bağlanır: IV→I · V7→I · ♭VII→i) + tempo + davul + arpej + ped rengi. "bright" = eski fon, BAYT BAYT
# (CHORDS/ARP doğrudan; yayındaki/zamanlanmış videolar yeniden üretilirse değişmesin — golden test).
QUALITY = {                     # akor niteliği → kökten yarım ses aralıkları
    "": (0, 4, 7), "m": (0, 3, 7), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "7": (0, 4, 7, 10),
    "add9": (0, 4, 7, 14), "madd9": (0, 3, 7, 14), "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14),
    "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "7sus4": (0, 5, 7, 10),
}
NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
        "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
MOODS = {
    # bright: genel/neşeli (eski fon) · night: minör, serin teknoloji (Mac mini, gece masası) · drive: minör pop, hızlı
    # (klavye, verim) · warm: majör 6'lı/7'li, ev (ses, kanepe) · dreamy: majör 7/9, yumuşak (kalem, fotoğraf) ·
    # uplift: majör, kendinden emin (nasıl-yapılır, kurulum)
    "bright": {"chords": ("Fmaj7", "Cadd9", "Dm7", "Bbmaj7"), "bpm": 100, "drums": "pop", "arp": "classic", "lp": 1600.0,
               "bass": "8ths"},
    "night": {"chords": ("Dm9", "Bbmaj7", "Fmaj7", "Csus2"), "bpm": 92, "drums": "half", "arp": "sparse", "lp": 1250.0,
              "bass": "quarters"},
    "drive": {"chords": ("Am7", "Fmaj7", "C", "G6"), "bpm": 112, "drums": "four", "arp": "up", "lp": 2000.0,
              "bass": "8ths"},
    "warm": {"chords": ("Cmaj7", "Am7", "Dm7", "G7"), "bpm": 94, "drums": "pop", "arp": "classic", "lp": 1500.0,
             "bass": "8ths"},
    "dreamy": {"chords": ("Ebmaj7", "Cm7", "Abmaj9", "Bb7sus4"), "bpm": 88, "drums": "half", "arp": "updown",
               "lp": 1400.0, "bass": "quarters"},
    "uplift": {"chords": ("Gadd9", "Em7", "Cmaj7", "D7sus4"), "bpm": 104, "drums": "pop", "arp": "classic", "lp": 1800.0,
               "bass": "8ths"},
}
ARPS = {                        # 16 adım; sayı = akor sesi sırası, None = sus
    "classic": ARP,
    "up": (0, 1, 2, 3) * 4,
    "updown": (0, 1, 2, 3, 2, 1, 0, 1, 0, 1, 2, 3, 2, 1, 0, 1),
    "sparse": (0, None, 2, None, 1, None, 3, None, 0, None, 2, None, 3, None, 1, None),
}


def parse_chord(sym: str) -> tuple:
    """"Dm9" → (2, (0, 3, 7, 10, 14)) — kök perde sınıfı + aralıklar. Bilinmeyen kök/nitelik ValueError."""
    m = re.match(r"^([A-G][#b]?)(.*)$", sym)
    if not m or m.group(1) not in NOTE or m.group(2) not in QUALITY:
        raise ValueError(f"akor okunamadı: {sym!r} (nitelikler: {', '.join(repr(q) for q in QUALITY)})")
    return NOTE[m.group(1)], QUALITY[m.group(2)]


def voice_chords(symbols) -> list:
    """Akor sembolleri → CHORDS biçimi [(bas kökü, ped 4 ses, arp 4 ses)]. Ped dört ses: beş seslide beşli düşer
    (üçlü + 7'li + 9'lu niteliği taşır), üç seslide kök oktav üstte yinelenir. Yan yana yarım
    ses YOK (ped çamurlaşmasın), tını ≤ 15 yarım ses; bir önceki akora EN AZ hareketle (ses yürütme) + orta kayıta hafif
    çekim (kaymasın). Pencere C3–F♯5 — daha dar pencerede E♭maj7'nin D'si ile E♭'ü hep yan yana düşüyordu. Arp pedin bir oktav üstü, bas kökte (B♭1–A2)."""
    out, prev = [], (53, 57, 60, 64)
    for sym in symbols:
        root, iv = parse_chord(sym)
        if len(iv) == 5:
            iv = tuple(i for i in iv if i != 7)
        pcs = [(root + i) % 12 for i in iv]
        if len(pcs) == 3:
            pcs.append(pcs[0])
        best = None
        for combo in itertools.product(*[[n for n in range(48, 79) if n % 12 == pc] for pc in pcs]):
            v = tuple(sorted(combo))
            if len(set(v)) < 4 or v[-1] - v[0] > 15 or any(b - a < 2 for a, b in zip(v, v[1:])):
                continue
            cost = sum(abs(a - b) for a, b in zip(v, prev)) + 0.15 * abs(sum(v) / 4 - 59)
            if best is None or cost < best[0]:
                best = (cost, v)
        if best is None:
            raise ValueError(f"{sym}: dar ve sürtüşmesiz bir yerleşim bulunamadı")
        pad = best[1]
        out.append((34 + (root - 34) % 12, pad, tuple(n + 12 for n in pad)))
        prev = pad
    return out


def _table(harmonics) -> list:
    t = [sum(a * math.sin(n * 2 * math.pi * i / TAB) for n, a in harmonics) for i in range(TAB)]
    m = max(abs(x) for x in t)
    return [x / m for x in t]


WARM = _table([(n, 1 / n ** 1.6) for n in range(1, 8)])
BRIGHT = _table([(1, 1.0), (2, 0.45), (3, 0.25), (4, 0.12)])
BASS = _table([(1, 1.0), (2, 0.3), (3, 0.08)])


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _osc(tab, f: float, n: int, phase: float = 0.0) -> list:
    inc = f * TAB / SR
    return [tab[int(phase + i * inc) & _MASK] for i in range(n)]


def _add(dst: list, src: list, at: int, gain: float = 1.0):
    if at < 0:                       # kısmi ilk ölçü (ızgara karta hizalı): baş kırpılır, kalanı çalar
        src, at = src[-at:], 0
    n = min(len(src), len(dst) - at)
    for i in range(max(0, n)):
        dst[at + i] += src[i] * gain


def _lowpass(x: list, fc: float) -> list:
    a = math.exp(-2 * math.pi * fc / SR)
    y, s = [0.0] * len(x), 0.0
    for i, v in enumerate(x):
        s = v + a * (s - v)
        y[i] = s
    return y


def _kick() -> list:
    n = int(0.3 * SR)
    out, ph = [0.0] * n, 0.0
    for i in range(n):
        t = i / SR
        ph += (50 + 80 * math.exp(-t / 0.03)) / SR
        out[i] = math.sin(2 * math.pi * ph) * math.exp(-t / 0.09)
    return out


def _noise_hit(rng, dur: float, tau: float, bursts=(0.0,)) -> list:
    n = int(dur * SR)
    raw = [rng.uniform(-1, 1) for _ in range(n + 1)]
    hp = [raw[i + 1] - raw[i] for i in range(n)]          # birinci fark = parlak (yüksek geçiren) gürültü
    env = [0.0] * n
    for b in bursts:
        s = int(b * SR)
        for i in range(s, n):
            env[i] = max(env[i], math.exp(-(i - s) / (tau * SR)))
    return [h * e * 0.5 for h, e in zip(hp, env)]


BED_VERSION = 3                 # sentez/düzen değişince önbellek adı değişsin (bayat müzik kalmasın) — 3: kısa kuyrukta final sönümü
LONG = 24.0                     # bu süreden uzun videoda B bölümü + 2 ölçülük giriş
B_SECTION = (2, 3, 0, 1)        # vi–IV–I–V (A'nın I'sına V'den döner)
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work", "music", "auto")


def arrangement(dur: float, bpm: float = 100.0, ending: float | None = None) -> list:
    """Ölçü planı [(t0, akor sırası, "intro" | "groove" | "final")]. Izgara `ending`e (kapanış kartının başı;
    yoksa son ölçü) HİZALANIR: o an TONİK final, önceki ölçü IV → müzik kartta çözülür. İlk ölçü kısmi olabilir
    (t0 < 0). Geriye doğru 4 ölçülük bölümler: son bölüm A (I–V–vi–IV); uzun videoda önceki B (vi–IV–I–V)."""
    bar = 240.0 / bpm
    end = ending if ending is not None else max(bar, dur - bar)
    long_ = dur >= LONG
    intro = 2 if long_ else 1
    k_max = max(1, math.ceil(end / bar - 1e-9))
    plan = []
    for k in range(k_max, 0, -1):                  # k = karta kalan ölçü sayısı
        pos = (-k) % 4
        chord = B_SECTION[pos] if long_ and ((k - 1) // 4) % 2 == 1 else pos
        plan.append((end - k * bar, chord, "intro" if k_max - k < intro else "groove"))
    plan.append((end, 0, "final"))
    return plan


def render_bed(dur: float = 60.0, bpm: float = 100.0, seed: int = 1, ending: float | None = None,
               mood: str = "bright") -> tuple:
    """→ (sol, sağ) örnek listeleri, tepe −1 dBFS. Düzen `arrangement`tan; akor/davul/arpej/ped rengi `MOODS[mood]`dan
    ("bright" = eski fonun CHORDS/ARP'si doğrudan — bayt bayt aynı)."""
    M = MOODS[mood]
    chords = CHORDS if mood == "bright" else voice_chords(M["chords"])
    arp_pat, style, lp, quarters = ARPS[M["arp"]], M["drums"], M["lp"], M["bass"] == "quarters"
    rng = random.Random(seed)
    n = int(dur * SR)
    beat = 60.0 / bpm
    bar = 4 * beat
    step = bar / 16
    padA, padB, arpL, arpR, bass, drums = ([0.0] * n for _ in range(6))
    kick, clap = _kick(), _noise_hit(rng, 0.25, 0.07, bursts=(0.0, 0.011, 0.023))
    hats = [_noise_hit(rng, 0.08, 0.018) for _ in range(4)]
    crash = _lowpass(_noise_hit(rng, 1.8, 0.55), 5200.0)
    kicks = []
    plan = arrangement(dur, bpm, ending)
    # giriş YÜKSELİR: ped + arp %25'ten tam seviyeye (girişte kick yok → sidechain de yok; yükselme olmadan
    # "yumuşak" giriş akıştan YÜKSEK çalıyordu — test ölçtü)
    intro_end = next((t for t, _, p in plan if p != "intro"), 0.0)
    for t0, ci, part in plan:
        root, pad, arp = chords[ci]
        i0 = int(round(t0 * SR))
        if part == "final":
            # tonik: ped uzun sönümle sona dek, arp tek yükselen süsleme, bas tek uzun nota, tek vuruş + yumuşak zil.
            # KISA kuyruk (< 2 sn — kartsız Short'un ~1 sn kapanış satırı): her şey o süre İÇİNDE doğal söner; 1,6 sn'lik
            # sönüm videonun sonunda kesiliyordu ("sonlarda bir anda kesiliyor ses", kullanıcı 2026-10-25). Uzun kuyruk
            # (kart) eski ifadelerle — bayt bayt aynı (golden test).
            tail = dur - t0
            short = tail < 2.0
            ln = max(1, n - i0)
            env = [min(1.0, i / (0.12 * SR)) * math.exp(-i / ((max(0.3, tail * 0.35) if short else 1.6) * SR))
                   for i in range(ln)]
            for k, m in enumerate(pad):
                a = _osc(WARM, _hz(m) * 2 ** (6 / 1200), ln, phase=k * 731.0)
                c = _osc(WARM, _hz(m) * 2 ** (-6 / 1200), ln, phase=k * 1259.0)
                _add(padA, [x * e for x, e in zip(a, env)], i0, 0.075)
                _add(padB, [x * e for x, e in zip(c, env)], i0, 0.075)
            pl = int(1.2 * SR)
            penv = [min(1.0, i / (0.003 * SR)) * math.exp(-i / ((min(0.4, tail * 0.3) if short else 0.4) * SR))
                    for i in range(pl)]
            for s_, m in enumerate(arp):
                hit = [x * e for x, e in zip(_osc(BRIGHT, _hz(m), pl), penv)]
                if short:                # süsleme kuyruğa sığsın: notalar sıklaşır
                    sp = min(beat / 2, tail / 6)
                    _add(arpL, hit, i0 + int(s_ * sp * SR), 0.07)
                    _add(arpR, hit, i0 + int((s_ * sp + sp) * SR), 0.04)
                else:
                    _add(arpL, hit, i0 + int(s_ * beat / 2 * SR), 0.07)
                    _add(arpR, hit, i0 + int((s_ * beat / 2 + 3 * step) * SR), 0.04)
            bl = int(min(2.4, dur - t0) * SR)
            benv = [min(1.0, i / (0.005 * SR)) * math.exp(-i / ((max(0.2, tail * 0.3) if short else 0.8) * SR))
                    for i in range(bl)]
            _add(bass, [x * e for x, e in zip(_osc(BASS, _hz(root + 12), bl), benv)], i0, 0.16)
            _add(drums, kick, i0, 0.3)
            if short:
                ct = max(0.15, tail * 0.3)
                _add(drums, [v * math.exp(-i / (ct * SR)) for i, v in enumerate(crash)], i0, 0.09)
            else:
                _add(drums, crash, i0, 0.09)
            kicks.append(t0)
            continue
        # ped: atak 0,35 sn, sonraki akora 0,25 sn bindirme (girişte de çalar)
        ln = int((bar + 0.25) * SR)
        env = [min(1.0, i / (0.35 * SR)) * (min(1.0, (ln - i) / (0.25 * SR))) for i in range(ln)]
        for k, m in enumerate(pad):
            a = _osc(WARM, _hz(m) * 2 ** (6 / 1200), ln, phase=k * 731.0)
            c = _osc(WARM, _hz(m) * 2 ** (-6 / 1200), ln, phase=k * 1259.0)
            _add(padA, [x * e for x, e in zip(a, env)], i0, 0.075)
            _add(padB, [x * e for x, e in zip(c, env)], i0, 0.075)
        # arp: 16'lık pluck (girişte yarı güçte)
        soft = 0.45 if part == "intro" else 1.0
        pl = int(0.5 * SR)
        penv = [min(1.0, i / (0.003 * SR)) * math.exp(-i / (0.16 * SR)) for i in range(pl)]
        for s_, idx in enumerate(arp_pat):
            if idx is None:              # seyrek desen: sus
                continue
            o = _osc(BRIGHT, _hz(arp[idx]), pl)
            acc = (1.0 if s_ % 4 == 0 else 0.72) * soft
            hit = [x * e for x, e in zip(o, penv)]
            at = int(round((t0 + s_ * step) * SR))
            _add(arpL, hit, at, 0.085 * acc)
            _add(arpR, hit, at + int(3 * step * SR), 0.05 * acc)     # ping-pong yankı (3/16)
            _add(arpL, hit, at + int(6 * step * SR), 0.025 * acc)
        if part == "intro":              # giriş: davul ve bas YOK
            continue
        # bas: 8'likler, vuruşta daha güçlü
        bl = int(beat / 2 * 0.92 * SR)
        benv = [min(1.0, i / (0.005 * SR)) * math.exp(-i / (0.25 * SR)) * min(1.0, (bl - i) / (0.01 * SR))
                for i in range(bl)]
        o = _osc(BASS, _hz(root + 12), bl)
        bhit = [x * e for x, e in zip(o, benv)]
        for e8 in range(8):
            if quarters and e8 % 2:      # sakin ruh hâli: dörtlükler
                continue
            _add(bass, bhit, int(round((t0 + e8 * beat / 2) * SR)), 0.16 if e8 % 2 == 0 else 0.1)
        # davul — pop: 1-3 kick, 2-4 clap, 8'lik hi-hat (eski fon) · half: yarım tempo hissi (1 + 2'nin "ve"si kick,
        # 3 clap, yumuşak hi-hat) · four: her vuruşta kick, 2-4 clap, ara 8'liklerde hi-hat
        if style == "pop":
            for q in range(4):
                tq = t0 + q * beat
                if q in (0, 2):
                    _add(drums, kick, int(round(tq * SR)), 0.3)
                    if tq >= 0:
                        kicks.append(tq)
                else:
                    _add(drums, clap, int(round(tq * SR)), 0.12)
            for e8 in range(8):
                _add(drums, hats[e8 % 4], int(round((t0 + e8 * beat / 2) * SR)), 0.07 if e8 % 2 else 0.045)
        else:
            kick_at = (0, 1.5) if style == "half" else (0, 1, 2, 3)
            for q in kick_at:
                tq = t0 + q * beat
                _add(drums, kick, int(round(tq * SR)), 0.3 if q in (0, 2) else 0.22)
                if tq >= 0:
                    kicks.append(tq)
            for q in ((2,) if style == "half" else (1, 3)):
                _add(drums, clap, int(round((t0 + q * beat) * SR)), 0.12)
            for e8 in range(8):
                if style == "four" and e8 % 2 == 0:
                    continue
                _add(drums, hats[e8 % 4], int(round((t0 + e8 * beat / 2) * SR)),
                     (0.08 if style == "four" else 0.05) if e8 % 2 else 0.032)
    padA, padB = _lowpass(padA, lp), _lowpass(padB, lp)     # sesin anlaşılırlık bandına (1-4 kHz) yer
    # sidechain: kick'te ped/arp/bas kısılır, ~0,15 sn'de geri gelir
    duck, ki = [1.0] * n, 0
    for i in range(n):
        t = i / SR
        while ki + 1 < len(kicks) and kicks[ki + 1] <= t:
            ki += 1
        if kicks and kicks[ki] <= t:
            duck[i] = 1.0 - 0.45 * math.exp(-(t - kicks[ki]) / 0.15)
    L, R = [0.0] * n, [0.0] * n
    for i in range(n):
        t = i / SR
        if t < intro_end:
            u = t / intro_end
            duck[i] *= 0.25 + 0.75 * u * u * (3 - 2 * u)
        g = duck[i]
        mel = g * (padA[i] * 0.7 + padB[i] * 0.3 + arpL[i] + bass[i])
        mer = g * (padA[i] * 0.3 + padB[i] * 0.7 + arpR[i] + bass[i])
        L[i] = math.tanh(1.2 * (mel + drums[i]))
        R[i] = math.tanh(1.2 * (mer + drums[i]))
    peak = max(max(abs(x) for x in L), max(abs(x) for x in R)) or 1.0
    k = 10 ** (-1 / 20) / peak
    return [x * k for x in L], [x * k for x in R]


def story_params(story) -> tuple:
    """(süre, final anı) — final = SON kapanış kartının başı. Kartsız videoda (Short: sonda kart/logo YOK, "free" ~1 sn
    tek satır — kullanıcı, 2026-10-25) son sahnenin videonun SONUNA kadar süren son başlığının başı: müzik kapanış
    satırıyla birlikte çözülür (son ölçüye düşünce final vuruşu yazı yazılırken geliyordu — "ses görüntü tutarsızlığı").
    İkisi de yoksa None → son ölçü."""
    dur = round(story.duration, 3)
    outro = next((sc for sc in reversed(story.scenes) if sc.type == "outro"), None)
    if outro:
        return dur, round(outro.start, 3)
    last = story.scenes[-1] if story.scenes else None
    if last is not None and last.captions:
        c = max(last.captions, key=lambda c: float(c.get("t", 0.0)))
        t0 = float(c.get("t", 0.0))
        if t0 > 0 and t0 + float(c.get("d", last.duration - t0)) >= last.duration - 0.05:
            return dur, round(last.start + t0, 3)
    return dur, None


def story_bed(story, cache: str | None = None, bpm: float = 100.0, seed: int = 1) -> str:
    """O videoya ÖZEL müzik (önbellekli: süre + final anı + ruh hâli + sürüm aynıysa aynı dosya). Ruh hâli senaryonun
    `[video] music_mood`u (vars. "bright" — eski dosya adı korunur); tempo ruh hâlinin."""
    dur, ending = story_params(story)
    mood = getattr(story.video, "music_mood", "bright") or "bright"
    tag = "x" if ending is None else f"{ending:g}"
    if mood != "bright":
        bpm = float(MOODS[mood]["bpm"])
    name = f"bed-v{BED_VERSION}-{'' if mood == 'bright' else mood + '-'}{bpm:g}bpm-s{seed}-{dur:g}s-e{tag}.wav"
    out = os.path.join(cache or CACHE, name)
    return out if os.path.exists(out) else write_bed(out, dur, bpm, seed, ending, mood)


def write_bed(out: str, dur: float = 60.0, bpm: float = 100.0, seed: int = 1, ending: float | None = None,
              mood: str = "bright") -> str:
    L, R = render_bed(dur, bpm, seed, ending, mood)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    tmp = out + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<hh", int(a * 32767), int(b * 32767)) for a, b in zip(L, R)))
    os.replace(tmp, out)
    return out
