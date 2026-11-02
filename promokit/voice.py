"""Seslendirme: senaryodaki zamanlı satırlar → ses dosyaları (önbellekli) → render'da karıştırma.

Ses kaynağı (`[voice] engine`):
- `google`: Google Cloud Text-to-Speech — doğal Türkçe (Chirp 3 HD / WaveNet), aylık ücretsiz kota
  bizim kullanımın çok üstünde (kullanıcı kararı, 2026-10-20). Anahtar `GOOGLE_TTS_API_KEY` ya da
  `~/.config/promokit/google-tts.key`; istekte BAŞLIKTA gider (URL'de değil → günlüklere düşmez).
- `elevenlabs`: reklam tonunda doğal Türkçe (ücretli plan; ücretsiz planı ticari kullanıma izin vermiyor).
  Anahtar `ELEVENLABS_API_KEY` ya da `~/.config/promokit/elevenlabs.key`.
  İki bulut motorunda da anahtar repoya GİRMEZ ve metinler servise gönderilir.
- `chatterbox`: YEREL açık kaynak model (MIT, Türkçe destekli) — hesap/kart yok; ayrı Python ortamında
  (`work/tts-venv`) `tts_chatterbox.py` ile TOPLU üretilir (model bir kez yüklenir). Kullanıcı kararı,
  2026-10-20: Google ön ödeme istedi. `prompt` = 5-10 sn referans kaydıyla ses klonlama (ör. kendi sesi).
- `say`: macOS'un yerel Türkçe sesi (Yelda) — ücretsiz ama robotik; yalnız zamanlama taslağı.

Okunuş sözlüğü (`spoken`): ekrandaki metin değişmez; motora giden metinde İngilizce kelimeler Türkçe
okunuşla yazılır ("Mac" → "Mek"; kullanıcı, 2026-10-20: "Mac'i dümdüz mac diye okuyor"). Önbellek
anahtarı OKUNAN metinden → sözlük değişince satır yeniden üretilir.
- Satırda `src`: kayıtlı ses dosyası (gerçek seslendirmen) — üretim yok, yalnız biçim çevrilir.

Önbellek `work/voice/`: dosya adı metnin + motorun + sesin + modelin + ayarların özetidir → aynı satır
ikinci kez ÜCRETLENDİRİLMEZ; metin ya da tek bir ayar değişince yeniden üretilir.

Karıştırma (`audio_graph`): satırlar senaryo zamanına `adelay` ile yerleşir; müzik varsa konuşma
sürerken `sidechaincompress` ile kısılır (ducking); sonuç sosyal medya düzeyine eşitlenir
(`loudnorm` I=-16 LUFS, tepe -1,5 dBTP).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .util import PromoError, ffmpeg_bin, ffprobe_bin, log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "voice")
KEY_ENV = "ELEVENLABS_API_KEY"
KEY_FILE = os.path.expanduser("~/.config/promokit/elevenlabs.key")
API = "https://api.elevenlabs.io/v1"
GOOGLE_KEY_ENV = "GOOGLE_TTS_API_KEY"
GOOGLE_KEY_FILE = os.path.expanduser("~/.config/promokit/google-tts.key")
GOOGLE_API = "https://texttospeech.googleapis.com/v1"
CHATTERBOX_PY = os.path.join(ROOT, "work", "tts-venv", "bin", "python")
CHATTERBOX_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_chatterbox.py")
# Chatterbox çıktısı yazıya dökülüp metinle KARŞILAŞTIRILIR (tts_chatterbox.py başlığı: cümle sonrası
# uydurma konuşma / tekrar ölçüldü); tutmazsa sonraki tohum. Sözcük zamanlarından kırpılır: baştaki
# sessizlik ve son sözcükten sonraki kuyruk (hırıltı, uydurma) gider — `TRIM_PAD` son sözcüğün sönümü.
VERIFY_TRIES = 4
VERIFY_MODELS = {"en": "base.en"}          # diğer diller çok dilli modelle
VERIFY_DEFAULT = "small"
TRIM_PAD = (0.04, 0.22)
ENGINES = ("chatterbox", "elevenlabs", "google", "say")
LANGS = ("tr", "en", "zh")              # seslendirme dili (kullanıcı, 2026-10-20: Türkçe TTS yetersiz → İngilizce)
# İngilizce kelimelerin Türkçe yazımla okunuşu — YALNIZ Türkçe seslendirmede (`lang = "tr"`);
# senaryonun `[voice] lexicon`u ezer/ekler (o, her dilde uygulanır)
DEFAULT_LEXICON = {
    "Mission Control": "Mişın Kontrol",
    "MacBook": "Mekbuk",
    "Wi-Fi": "vayfay",
    "Mac": "Mek",
    "ADB": "ey di bi",
}
SAY_DEFAULT = "Yelda"           # bu Mac'teki tek Türkçe sistem sesi
SAY_WPM = 175                   # `say`ın varsayılan hızı (kelime/dk) — `speed` bununla çarpılır
SR = 48000
GAP = 0.12                      # iki satır arası en kısa sessizlik (sn)
# Seviye: her satır VO_LUFS'a, müzik onun BED_DB altına oturtulur (ölçülen yüksekliğe göre sabit kazanç) —
# motorlar farklı seviyede üretiyor (Chatterbox satırları −19…−22 LUFS ölçüldü) ve indirilen parçalar
# genelde −10…−14 LUFS'ta mastering'li: elle `music_volume` aramadan ses hep önde kalsın.
# `music_volume`/`[voice] volume` bu düzeyin ÇARPANI (1.0 = varsayılan denge).
VO_LUFS = -20.0
BED_DB = 10.0
STING_DB = 3.0                  # kısa efekt sesleri (`sfx`) konuşmanın bu kadar altında
DRIFT_WARN = 0.6                # yazılan zamandan bu kadar geç başlayan satır uyarılır


@dataclass
class VoiceCfg:
    engine: str = "elevenlabs"
    lang: str = "tr"                     # seslendirme dili; Türkçe okunuş sözlüğü yalnız "tr"de
    id: str = ""                         # ElevenLabs ses kimliği · Google ses adı (tr-TR-…) · `say` ses adı
    model: str = "eleven_multilingual_v2"
    stability: float = 0.45              # düşük = daha canlı (reklam tonu), yüksek = daha düz
    similarity: float = 0.8
    style: float = 0.3
    speed: float = 1.0                   # ElevenLabs 0,7–1,2 · Chatterbox'ta perdeyi bozmadan tempo (atempo)
    volume: float = 1.0                  # karışımda konuşmanın kazancı
    lexicon: dict | None = None          # okunuş sözlüğü eklemeleri/ezmeleri (DEFAULT_LEXICON üstüne)
    exaggeration: float = 0.7            # Chatterbox: duygu yoğunluğu (0,5 nötr; yüksek = daha canlı)
    cfg_weight: float = 0.4              # Chatterbox: düşük = daha sakin/ölçülü tempo
    temperature: float = 0.8             # Chatterbox: örnekleme sıcaklığı
    seed: int = 1                        # Chatterbox: aynı satır = aynı ses (önbelleğe girer)
    prompt: str = ""                     # Chatterbox: ses klonlama referans kaydı (5-10 sn)


@dataclass
class Line:
    t: float                             # senaryo (mutlak) zamanı, sn
    text: str
    src: str | None                      # kayıtlı ses (varsa üretim yok)
    scene: int
    path: str | None = None              # hazırlanmış wav
    dur: float = 0.0
    want: float | None = None            # yazılan zaman (`schedule` kaydırırsa `t`den farklı)
    subs: dict | None = None             # altyazı çevirileri: {"tr": "…"} (promokit/subs.py)
    say: str | None = None               # motora giden okunuş (vurgu/duraklama ayarı); altyazı `text`ten
    lufs: float | None = None            # ölçülen yükseklik (`prepare`), karışımda VO_LUFS'a eşitlenir
    speed: float | None = None           # YALNIZ bu satırın hızı (atempo, genel `speed`in üstüne) — `_line_tempo`


@dataclass
class Graph:
    inputs: list                         # ffmpeg giriş argümanları, her biri bir liste ([..., "-i", yol])
    filter: str                          # -filter_complex
    out: str                             # çıkış etiketi


# ── üretim ────────────────────────────────────────────────────────────────────
def _read_key(env: str, path: str, name: str) -> str:
    k = os.environ.get(env, "").strip()
    if not k and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            k = f.read().strip()
    if not k:
        raise PromoError(f"{name} API anahtarı yok — {env} ortam değişkenine ya da {path} dosyasına koyun "
                         "(dosya: tek satır, yalnız anahtar; `chmod 600`). Taslak için: --engine say")
    return k


def api_key() -> str:
    return _read_key(KEY_ENV, KEY_FILE, "ElevenLabs")


def google_key() -> str:
    return _read_key(GOOGLE_KEY_ENV, GOOGLE_KEY_FILE, "Google Text-to-Speech")


def spoken(text: str, lexicon: dict | None = None, lang: str = "tr") -> str:
    """Okunuş sözlüğü: TEK geçiş, uzun eşleşme önce ("MacBook" "Mac"ten önce), kelime sınırında
    ("Macera"ya dokunmaz), Türkçe ekleri korur ("Mac'inize" → "Mek'inize"). Büyük/küçük harf duyarlı.
    Varsayılan sözlük yalnız Türkçede (İngilizce metinde "Mac" zaten doğru okunur)."""
    lex = {**(DEFAULT_LEXICON if lang == "tr" else {}), **(lexicon or {})}
    if not lex:
        return text
    pat = re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in sorted(lex, key=len, reverse=True)) + r")(?!\w)")
    return pat.sub(lambda m: lex[m.group(1)], text)


def _digest(text: str, cfg: VoiceCfg, engine: str) -> str:
    d = {"engine": engine, "text": text, "lang": cfg.lang}
    if engine == "elevenlabs":
        d.update(id=cfg.id, model=cfg.model, stability=cfg.stability, similarity=cfg.similarity,
                 style=cfg.style, speed=cfg.speed)
    elif engine == "google":
        d.update(id=cfg.id, speed=cfg.speed)
    elif engine == "chatterbox":
        prompt = ""
        if cfg.prompt:
            st = os.stat(cfg.prompt)
            prompt = f"{os.path.abspath(cfg.prompt)}|{st.st_mtime_ns}|{st.st_size}"
        d.update(exaggeration=cfg.exaggeration, cfg_weight=cfg.cfg_weight, temperature=cfg.temperature,
                 seed=cfg.seed, prompt=prompt, speed=cfg.speed, verify=_verify(cfg.lang), pad=list(TRIM_PAD))
    else:
        d.update(id=cfg.id or SAY_DEFAULT, speed=cfg.speed)
    return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _verify(lang: str) -> dict:
    from . import asrcheck
    return {"model": VERIFY_MODELS.get(lang, VERIFY_DEFAULT), "tries": VERIFY_TRIES,
            "rule": [asrcheck.RULE, asrcheck.RATIO_OK, asrcheck.LEN_SLACK]}


def _to_wav(src: str, out: str, tempo: float = 1.0, trim: tuple | None = None):
    """→ 48 kHz mono wav. `tempo` ≠ 1: perdeyi bozmadan hızlandır/yavaşlat (atempo, 0,5–2).
    `trim` = (baş, son) sn: yalnız bu aralık (uçlarda kısa yumuşatma — kesik tık olmasın)."""
    tmp = out + ".part"
    f = []
    if trim is not None:
        a, b = trim
        f += [f"atrim=start={a:.3f}:end={b:.3f}", "asetpts=PTS-STARTPTS", "afade=t=in:d=0.01",
              f"afade=t=out:st={max(0.0, b - a - 0.04):.3f}:d=0.04"]
    if abs(tempo - 1.0) > 1e-6:
        f.append(f"atempo={tempo:.4f}")
    af = ["-af", ",".join(f)] if f else []
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", src, *af, "-ac", "1", "-ar", str(SR),
                        "-c:a", "pcm_s16le", "-f", "wav", tmp], capture_output=True, text=True)
    if r.returncode != 0:
        raise PromoError(f"ses çevrilemedi ({os.path.basename(src)}): {r.stderr.strip()[-300:]}")
    os.replace(tmp, out)


def _say(text: str, cfg: VoiceCfg, tmp: str) -> str:
    raw = os.path.join(tmp, "say.aiff")
    cmd = ["say", "-v", cfg.id or SAY_DEFAULT, "-o", raw]
    if abs(cfg.speed - 1.0) > 1e-6:
        cmd += ["-r", str(int(round(SAY_WPM * cfg.speed)))]
    r = subprocess.run(cmd + ["--", text], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(raw):
        raise PromoError(f"`say` başarısız ({cfg.id or SAY_DEFAULT}): {r.stderr.strip()}")
    return raw


def _eleven(text: str, cfg: VoiceCfg, tmp: str) -> str:
    if not cfg.id:
        raise PromoError("[voice] id: ElevenLabs ses kimliği gerekli — listesi için `promo.py voice --list-voices`")
    body = {"text": text, "model_id": cfg.model,
            "voice_settings": {"stability": cfg.stability, "similarity_boost": cfg.similarity, "style": cfg.style,
                               "use_speaker_boost": True, "speed": cfg.speed}}
    req = Request(f"{API}/text-to-speech/{cfg.id}?output_format=mp3_44100_128",
                  data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                  headers={"xi-api-key": api_key(), "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        data = urlopen(req, timeout=120).read()
    except HTTPError as e:
        raise PromoError(f"ElevenLabs {e.code}: {_detail(e)}")
    except URLError as e:
        raise PromoError(f"ElevenLabs'e ulaşılamadı: {e.reason}")
    raw = os.path.join(tmp, "eleven.mp3")
    with open(raw, "wb") as f:
        f.write(data)
    return raw


def _google(text: str, cfg: VoiceCfg, tmp: str) -> str:
    if not cfg.id:
        raise PromoError("[voice] id: Google ses adı gerekli (ör. tr-TR-Chirp3-HD-…) — listesi için "
                         "`promo.py voice --engine google --list-voices`")
    import base64
    lang = "-".join(cfg.id.split("-")[:2])           # "tr-TR-Chirp3-HD-Charon" → "tr-TR"

    def call(rate):
        ac = {"audioEncoding": "LINEAR16", "sampleRateHertz": SR}
        if rate is not None:
            ac["speakingRate"] = rate
        body = {"input": {"text": text}, "voice": {"languageCode": lang, "name": cfg.id}, "audioConfig": ac}
        req = Request(f"{GOOGLE_API}/text:synthesize", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      method="POST", headers={"X-Goog-Api-Key": google_key(), "Content-Type": "application/json"})
        return json.loads(urlopen(req, timeout=120).read().decode("utf-8"))

    rate = cfg.speed if abs(cfg.speed - 1.0) > 1e-6 else None
    try:
        try:
            j = call(rate)
        except HTTPError as e:
            if e.code != 400 or rate is None:
                raise
            # bazı ses aileleri hız ayarını kabul etmeyebilir → bir kez hızsız (zamanlama `schedule`la uyar)
            log(f"⚠️ Google: {cfg.id} hız ayarını kabul etmedi ({_gdetail(e)}) — hızsız üretiliyor")
            j = call(None)
    except HTTPError as e:
        raise PromoError(f"Google TTS {e.code}: {_gdetail(e)}")
    except URLError as e:
        raise PromoError(f"Google TTS'e ulaşılamadı: {e.reason}")
    raw = os.path.join(tmp, "google.wav")
    with open(raw, "wb") as f:
        f.write(base64.b64decode(j.get("audioContent", "")))
    return raw


def _gdetail(e: HTTPError) -> str:
    try:
        j = json.loads(e.read().decode("utf-8", "replace"))
        return j.get("error", {}).get("message", "") or json.dumps(j, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — gövde JSON değilse kodu söylemek yeter
        return e.reason or "bilinmeyen hata"


def _detail(e: HTTPError) -> str:
    try:
        j = json.loads(e.read().decode("utf-8", "replace"))
        d = j.get("detail", j)
        return d.get("message", json.dumps(d, ensure_ascii=False)) if isinstance(d, dict) else str(d)
    except Exception:  # noqa: BLE001 — hata gövdesi JSON değilse kodu söylemek yeter
        return e.reason or "bilinmeyen hata"


def _run_chatterbox(cmd, **kw):        # testte sahtelenir (model gerektirmeden)
    return subprocess.run(cmd, **kw)


def _chatterbox_batch(items: list, cfg: VoiceCfg):
    """[(okunan metin, önbellek yolu)] → TEK süreçte üret (model bir kez yüklenir), 48 kHz'e çevir."""
    if not os.path.exists(CHATTERBOX_PY):
        raise PromoError("Chatterbox ortamı yok — kurulum: python3 -m venv work/tts-venv && "
                         "work/tts-venv/bin/pip install chatterbox-tts 'setuptools<81' faster-whisper")
    tmp = tempfile.mkdtemp(prefix="promo-cbx-")
    try:
        jobs = [{"text": t, "out": os.path.join(tmp, f"{i:03d}.wav"), "lang": cfg.lang, "exaggeration": cfg.exaggeration,
                 "cfg_weight": cfg.cfg_weight, "temperature": cfg.temperature, "seed": cfg.seed,
                 "prompt": cfg.prompt or None, "verify": _verify(cfg.lang)} for i, (t, _) in enumerate(items)]
        jp = os.path.join(tmp, "jobs.json")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False)
        r = _run_chatterbox([CHATTERBOX_PY, CHATTERBOX_HELPER, jp], capture_output=True, text=True)
        if r.returncode != 0:
            raise PromoError(f"Chatterbox başarısız:\n{(r.stderr or '').strip()[-600:]}")
        for j, (_, out) in zip(jobs, items):
            rep = {}
            if os.path.exists(j["out"] + ".json"):
                with open(j["out"] + ".json", encoding="utf-8") as f:
                    rep = json.load(f)
            trim = None
            if rep.get("verified") and rep.get("end", 0) > rep.get("start", 0):
                end = rep["end"] + TRIM_PAD[1]
                if rep.get("next") is not None:                 # kuyruk varsa pay onun başını geçmez
                    end = min(end, max(rep["end"], rep["next"] - 0.02))
                trim = (max(0.0, rep["start"] - TRIM_PAD[0]), min(duration(j["out"]), end))
            _to_wav(j["out"], out, tempo=cfg.speed, trim=trim)
            with open(out + ".json", "w", encoding="utf-8") as f:     # önbelleğin yanında: `check` her koşuda uyarır
                json.dump(rep, f, ensure_ascii=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _line_tempo(path: str, speed: float) -> str:
    """Satır başına hız (kullanıcı, 2026-10-30: "o ilk cümleyi çok hızlı söylüyor"): önbellekteki sesin yanında
    `<ad>-x0.850.wav` — yalnız atempo (perde değişmez). Ham üretim aynı kalır → hızı değiştirmek modeli yeniden
    KOŞTURMAZ. Doğrulama raporu da kopyalanır: `check`in "metinle tutmadı" uyarısı hız ayarıyla kaybolmasın."""
    out = f"{path[:-4]}-x{speed:.3f}.wav"
    if not os.path.exists(out):
        _to_wav(path, out, tempo=speed)
    if os.path.exists(path + ".json") and not os.path.exists(out + ".json"):
        shutil.copyfile(path + ".json", out + ".json")
    return out


def _cache_path(said: str, cfg: VoiceCfg, engine: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, f"{engine}-{_digest(said, cfg, engine)}.wav")


def synth(text: str, cfg: VoiceCfg, engine: str | None = None) -> str:
    """Metin → önbellekteki wav (48 kHz mono). Önbellekte varsa hiçbir istek atılmaz.
    Motora OKUNAN metin gider (`spoken`: "Mac" → "Mek")."""
    engine = engine or cfg.engine
    if engine not in ENGINES:
        raise PromoError(f"ses motoru: {' | '.join(ENGINES)} (gelen: {engine!r})")
    text = spoken(text, cfg.lexicon, cfg.lang)
    out = _cache_path(text, cfg, engine)
    if os.path.exists(out):
        return out
    if engine == "chatterbox":
        _chatterbox_batch([(text, out)], cfg)
        return out
    tmp = tempfile.mkdtemp(prefix="promo-voice-")
    try:
        raw = {"say": _say, "google": _google, "elevenlabs": _eleven}[engine](text, cfg, tmp)
        _to_wav(raw, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def from_file(src: str) -> str:
    """Kayıtlı seslendirme dosyası → önbellekte wav (yol + değişim zamanı özeti)."""
    os.makedirs(CACHE, exist_ok=True)
    st = os.stat(src)
    h = hashlib.sha1(f"{os.path.abspath(src)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]
    out = os.path.join(CACHE, f"src-{h}.wav")
    if not os.path.exists(out):
        _to_wav(src, out)
    return out


def duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        return float(r.stdout.strip() or 0)


def prepare(lines: list, cfg: VoiceCfg, engine: str | None = None) -> list:
    """Satırları hazırla (üret/çevir, süreyi ölç). Bulutta önbellek dışındaki her satır bir istek;
    Chatterbox'ta eksik satırların HEPSİ tek süreçte üretilir."""
    engine = engine or cfg.engine
    if engine == "chatterbox":
        todo = []
        for ln in lines:
            if ln.src:
                ln.path = from_file(ln.src)
                continue
            said = spoken(ln.say or ln.text, cfg.lexicon, cfg.lang)
            ln.path = _cache_path(said, cfg, engine)
            if not os.path.exists(ln.path) and (said, ln.path) not in todo:
                todo.append((said, ln.path))
        if todo:
            _chatterbox_batch(todo, cfg)
    else:
        for ln in lines:
            ln.path = from_file(ln.src) if ln.src else synth(ln.say or ln.text, cfg, engine)
    for ln in lines:
        if ln.speed is not None and abs(ln.speed - 1.0) > 1e-6:
            ln.path = _line_tempo(ln.path, ln.speed)
        ln.dur = duration(ln.path)
        ln.lufs = loudness(ln.path)
    return schedule(lines)


_LOUD = {}


def loudness(path: str) -> float | None:
    """Tümleşik yükseklik (LUFS, EBU R128). Sessiz/çok kısa (kapılı) dosyada None."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if key not in _LOUD:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.findall(r"^\s*I:\s*(-?[\d.]+) LUFS", r.stderr, re.M)
        v = float(m[-1]) if m else None
        _LOUD[key] = v if v is not None and v > -69.0 else None
    return _LOUD[key]


def _gain(target: float, measured: float | None) -> float:
    return 10 ** ((target - measured) / 20) if measured is not None else 1.0


def schedule(lines: list) -> list:
    """Konuşma ÜST ÜSTE BİNMEZ: bir satır, öncekinin bitişinden (+`GAP`) önce başlamaz — gerekirse kayar.
    Yazılan zaman `want`ta saklanır. Ses değişince (taslak `say` ↔ ElevenLabs, `speed`) süreler değişir;
    sabit zamanlar o zaman çakışır, bu kural sesten bağımsız sağlam kalır (kayma `check`te uyarılır)."""
    end = float("-inf")
    for ln in sorted(lines, key=lambda x: x.t if x.want is None else x.want):
        if ln.want is None:
            ln.want = ln.t
        ln.t = max(ln.want, end + GAP)
        end = ln.t + ln.dur
    return lines


def check(lines: list, total: float) -> list:
    """Zamanlama uyarıları: satır yazıldığından belirgin geç başlıyor ya da video bitiminden sonra bitiyor."""
    out = []
    for a in sorted(lines, key=lambda x: x.t):
        late = a.t - (a.want if a.want is not None else a.t)
        if late > DRIFT_WARN:
            out.append(f"«{a.text or a.src}» {late:.2f} sn geç başlıyor (öncekiler uzun) — metinleri kısaltın "
                       "ya da `speed`i artırın")
        if a.t + a.dur > total + 1e-6:
            out.append(f"«{a.text or a.src}» video bitiminden sonra bitiyor ({a.t + a.dur:.2f} > {total:.2f} sn)")
        rep = _report(a.path)
        if rep.get("verified") and not rep.get("ok"):
            out.append(f"«{a.text}» metinle tutmadı ({rep.get('tries', '?')} denemenin en iyisi, duyulan: "
                       f"«{rep.get('heard', '')}») — dinleyip kontrol edin, gerekirse metni değiştirin")
    return out


def _report(path: str | None) -> dict:
    """Chatterbox doğrulama raporu (önbellekteki wav'ın yanında); yoksa boş."""
    if not path or not os.path.exists(path + ".json"):
        return {}
    try:
        with open(path + ".json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def list_voices(engine: str = "elevenlabs", lang: str = "tr-TR") -> list:
    """Kullanılabilir sesler: [(kimlik, ad, etiketler)]. Google'da yalnız `lang` dilindekiler."""
    if engine == "google":
        req = Request(f"{GOOGLE_API}/voices?languageCode={lang}", headers={"X-Goog-Api-Key": google_key()})
        try:
            j = json.loads(urlopen(req, timeout=60).read().decode("utf-8"))
        except HTTPError as e:
            raise PromoError(f"Google TTS {e.code}: {_gdetail(e)}")
        except URLError as e:
            raise PromoError(f"Google TTS'e ulaşılamadı: {e.reason}")
        return [(v.get("name", ""), v.get("name", ""), {"cinsiyet": v.get("ssmlGender", "")}) for v in j.get("voices", [])]
    req = Request(f"{API}/voices", headers={"xi-api-key": api_key(), "Accept": "application/json"})
    try:
        j = json.loads(urlopen(req, timeout=60).read().decode("utf-8"))
    except HTTPError as e:
        raise PromoError(f"ElevenLabs {e.code}: {_detail(e)}")
    except URLError as e:
        raise PromoError(f"ElevenLabs'e ulaşılamadı: {e.reason}")
    return [(v.get("voice_id", ""), v.get("name", ""), v.get("labels") or {}) for v in j.get("voices", [])]


# ── karıştırma ────────────────────────────────────────────────────────────────
def audio_graph(lines: list, t_from: float, dur: float, music: str | None, music_volume: float,
                vo_volume: float, first_input: int, music_lufs: float | None = None, sfx=(),
                music_final: float | None = None) -> Graph:
    """Konuşma (+ efekt + müzik) → tek ses akışı. `first_input`: ilk ek girişin ffmpeg sırası (0 = video).
    Her satır VO_LUFS'a (`ln.lufs`), müzik VO_LUFS − BED_DB'ye (`music_lufs`) eşitlenir; ölçüm yoksa ham.
    `sfx` = [(senaryo anı, wav, süre, ölçülen LUFS)] (marka girişinin sesi): VO_LUFS − STING_DB'ye eşitlenir ve
    konuşmayla AYNI veriyoluna girer → müzik altında kısılır, altyazıya girmez (satır değil).
    `music_final` (senaryo anı): fon kendi finalini çözüyorsa (musicbed — kartsız Short'ta kapanış satırı) sönme o
    finalden ÖNCE başlamaz: 1,5 sn'lik sönme 9,1'de başlayınca 9,6'daki final vuruşu kısılmış sese düşüp 1 sn'de
    sıfıra iniyordu ("sonlarda bir anda kesiliyor ses", kullanıcı 2026-10-25)."""
    inputs, chains, labels = [], [], []
    idx = first_input
    mus = None
    if music:
        inputs.append(["-ss", f"{t_from:.3f}", "-i", music])
        mus = idx
        idx += 1
    clips = [(ln.t, ln.path, ln.dur, vo_volume * _gain(VO_LUFS, ln.lufs)) for ln in sorted(lines, key=lambda x: x.t)]
    clips += [(t, p, d, vo_volume * _gain(VO_LUFS - STING_DB, lufs)) for t, p, d, lufs in sfx]
    for t, path, d, gain in clips:
        off = t - t_from
        if off + d <= 0 or off >= dur:
            continue                                   # kısmi render aralığının dışında
        inputs.append(["-i", path])
        trim = f"atrim=start={-off:.3f},asetpts=PTS-STARTPTS," if off < 0 else ""
        ms = int(round(max(0.0, off) * 1000))
        lab = f"v{len(labels)}"
        chains.append(f"[{idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,{trim}adelay={ms}|{ms},"
                      f"volume={gain:.3f}[{lab}]")
        labels.append(lab)
        idx += 1
    if labels:
        chains.append("".join(f"[{x}]" for x in labels) +
                      (f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[vo]" if len(labels) > 1 else "anull[vo]"))
    if mus is not None:
        if music_final is None:
            fade = f"afade=t=out:st={max(0.0, dur - 1.5):.2f}:d=1.5"
        else:
            fd = min(1.5, max(0.25, (t_from + dur) - music_final - 0.5))
            fade = f"afade=t=out:st={max(0.0, dur - fd):.2f}:d={fd:.2f}"
        chains.append(f"[{mus}:a]aformat=sample_rates={SR}:channel_layouts=stereo,afade=t=in:d=0.6,"
                      f"{fade},volume={music_volume * _gain(VO_LUFS - BED_DB, music_lufs):.3f},"
                      "apad[mus]")
        if labels:
            # konuşma sürerken müzik kısılır (~8 dB: sesin altında ~18 dB) — konuşma yan-zincir anahtarı
            # anahtar sessizlikle UZAR: sidechaincompress girişlerinden biri bitince çıktısını da kesiyordu → son konuşma
            # satırından sonra müzik TAMAMEN susuyordu (Mac mini kısası: son 3,4 sn sessiz — "sonlarda bir anda kesiliyor
            # ses", kullanıcı 2026-10-25); sondaki atrim videoyla birlikte keser
            chains.append("[vo]apad,asplit=2[vo1][vok]")
            chains.append("[mus][vok]sidechaincompress=threshold=0.03:ratio=4:attack=15:release=350[duck]")
            chains.append("[duck][vo1]amix=inputs=2:normalize=0:dropout_transition=0[mix]")
        else:
            chains.append("[mus]anull[mix]")
    elif labels:
        chains.append("[vo]anull[mix]")
    else:
        return Graph([], "", "")
    chains.append(f"[mix]apad,atrim=0:{dur:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    return Graph(inputs, ";".join(chains), "[aout]")
