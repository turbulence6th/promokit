"""Senaryo (storyboard) dosyası: TOML → doğrulanmış sahne listesi.

TOML stdlib'de (`tomllib`) okunur — ek paket yok. Göreli yollar senaryo dosyasının
dizinine göre çözülür. Bilinmeyen anahtar → yakın eşleşme önerisiyle HATA (yazım
hatası sessizce yok sayılırsa video "neden zoom yapmadı" diye saatler yer). Değerler de
YÜKLEMEDE denetlenir: hatalı bir yumuşatma adı ya da renk, render'ın ortasında (paralel
parçalar bittikten sonra) ham traceback olarak çıkmasın.
Örnek: examples/site.toml · alan listesi: README.md §Senaryo.
"""
from __future__ import annotations

import difflib
import os
import tomllib
from dataclasses import dataclass, field

from .util import EASINGS, PromoError, parse_color
from .voice import ENGINES as VOICE_ENGINES, LANGS as VOICE_LANGS, Line, VoiceCfg

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "4:5": (4, 5), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4)}
SIZE_ALIASES = {"720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160, "2160p": 2160}

VIDEO_KEYS = {
    "aspect", "size", "fps", "theme", "background", "font", "music", "music_volume", "music_mood",
    "transition", "transition_dur", "caption_size", "laptop_finish", "tablet_color",
    "codec", "crf", "out", "title", "lang", "laptop_model",
}
SCENE_KEYS = {
    "type", "duration", "layout", "tablet", "laptop", "caption",
    "subcaption", "captions", "camera", "transition", "transition_dur", "title",
    "subtitle", "badge", "portrait", "landscape", "note",
    "page", "params", "scroll", "scroller", "anim", "voice", "still",
}
MEDIA_KEYS = {"src", "in", "out", "speed", "loop", "fit", "color", "params", "scroll", "scroller"}
SCROLL_KEYS = {"t", "y", "to", "dur", "ease", "offset"}
SCENE_TYPES = {"device", "title", "outro", "html"}
ANIM_KEYS = {"t", "x", "y", "s", "o", "on", "lid", "rot", "ease"}
LAYOUTS = {"tablet", "laptop", "duo"}
LAYOUT_DEVICES = {"tablet": ("tablet",), "laptop": ("laptop",), "duo": ("laptop", "tablet")}
TRANSITIONS = ("auto", "morph", "fade", "cut", "slide")
THEMES = ("light", "dark")
CODECS = ("h264", "hevc", "prores")


def _check_keys(d, allowed: set, where: str):
    if not isinstance(d, dict):
        raise PromoError(f"{where}: tablo ({{ … }}) bekleniyordu, gelen: {d!r}")
    for k in d:
        if k not in allowed:
            m = difflib.get_close_matches(k, sorted(allowed), 1)
            raise PromoError(f"{where}: bilinmeyen alan '{k}'" + (f" — '{m[0]}' mi demek istediniz?" if m else "")
                             + f"\n  geçerli alanlar: {', '.join(sorted(allowed))}")


def _num(v, where: str, lo: float | None = None, hi: float | None = None, above: float | None = None) -> float:
    """Sayı denetimi: `lo` ≤ v ≤ `hi`, `above` < v. bool sayı SAYILMAZ (TOML `true` ≠ 1)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PromoError(f"{where}: sayı olmalı (gelen: {v!r})")
    v = float(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi) or (above is not None and v <= above):
        rng = (f"> {above:g}" if above is not None else f"≥ {lo:g}" if lo is not None else "")
        rng += (f" ve ≤ {hi:g}" if hi is not None else "")
        raise PromoError(f"{where}: {v:g} geçersiz — {rng.strip()} olmalı")
    return v


def _enum(v, allowed, where: str) -> str:
    if v not in allowed:
        m = difflib.get_close_matches(str(v), [str(a) for a in allowed], 1)
        raise PromoError(f"{where}: '{v}' geçersiz — {' | '.join(allowed)}" + (f" ('{m[0]}' mi?)" if m else ""))
    return v


def _bool(v, where: str) -> bool:
    if not isinstance(v, bool):
        raise PromoError(f"{where}: true | false olmalı (gelen: {v!r})")
    return v


def _color(v, where: str) -> str:
    try:
        parse_color(v)
    except (ValueError, TypeError) as e:
        raise PromoError(f"{where}: {e}")
    return str(v)


def _ease(v, where: str) -> str:
    return _enum(v, EASINGS, f"{where}.ease")


def _list_of_tables(v, where: str) -> list:
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        raise PromoError(f"{where}: [ {{ t = …, … }}, … ] biçiminde tablo listesi olmalı")
    return v


@dataclass
class MediaRef:
    path: str | None = None
    color: str | None = None
    clip_in: float = 0.0
    clip_out: float | None = None
    speed: float = 1.0
    loop: bool = False
    fit: str = "cover"
    params: dict = field(default_factory=dict)   # HTML ekrana giden sayfa parametreleri (promo.params)
    scroll: list = field(default_factory=list)   # HTML ekran: kaydırma hareketleri [{t, y|to, dur, ease, offset}]
    scroller: str | None = None                  # kaydırılan öğenin CSS seçicisi (yoksa sayfanın kendisi)


@dataclass
class VideoCfg:
    lang: str = "tr"              # ekran dili: her HTML sayfanın `params.lang`ine iner + çıktı klasörü/altyazı dili
    width: int = 1920
    height: int = 1080
    aspect: str = "16:9"
    fps: float = 60.0
    theme: str = "light"
    background: str | None = None
    font: str | None = None
    music: str | None = None
    music_volume: float = 0.8
    music_mood: str = "bright"   # music = "bed" ruh hâli (musicbed.MOODS) — içeriğe göre: night/drive/warm/dreamy/uplift
    music_final: float | None = None   # (render doldurur) music = "bed": fonun final anı → karıştırıcı sönmeyi ona göre
    transition: str = "auto"      # auto = cihaz↔cihaz morph, diğerleri fade
    transition_dur: float = 0.6
    caption_size: float = 1.0
    laptop_model: str = "macbook"     # macbook (14" MacBook Pro) | windows (genel Windows dizüstü) — devices.LAPTOP_MODELS
    laptop_finish: str = ""           # boş → modelin varsayılanı (MacBook silver · Windows graphite)
    tablet_color: str = "frame"
    codec: str = "h264"
    crf: int = 16
    out: str | None = None        # varsayılan çıktı yolu (senaryoya göreli; `-o` ezer)
    title: str = ""               # çıktının başlık metaverisi

    @property
    def portrait(self) -> bool:
        return self.height > self.width


@dataclass
class Scene:
    index: int
    type: str = "device"
    duration: float | None = None
    layout: str = "tablet"
    tablet: MediaRef | None = None
    laptop: MediaRef | None = None
    caption: str = ""
    subcaption: str = ""
    captions: list = field(default_factory=list)
    camera: list = field(default_factory=list)
    transition: str | None = None
    transition_dur: float | None = None
    title: str = ""
    subtitle: str = ""
    badge: str = ""
    page: str | None = None       # type="html": tam ekran sayfa
    scroll: list = field(default_factory=list)   # type="html": sayfanın kaydırma hareketleri
    scroller: str | None = None   # type="html": kaydırılan öğenin seçicisi (yoksa sayfanın kendisi)
    still: float | None = None    # sahnenin bu anı (sn) — `promo.py stills` o kareyi PNG/JPG olarak yazar
    params: dict = field(default_factory=dict)
    anim: dict = field(default_factory=dict)      # cihaz → anahtar kareler (x/y/s/o/on/lid/rot)
    voice: list = field(default_factory=list)   # [{t, text, src?}]: seslendirme satırları (sahne zamanı)
    # çalışma anında doldurulur
    start: float = 0.0


@dataclass
class Story:
    path: str
    base: str
    video: VideoCfg
    scenes: list[Scene]
    voice: VoiceCfg = field(default_factory=VoiceCfg)

    def voice_lines(self) -> list:
        """Seslendirme satırları senaryo (mutlak) zamanında, sıralı."""
        out = [Line(sc.start + float(e.get("t", 0.0)), str(e.get("text", "")), e.get("src"), sc.index,
                    subs={k: str(e[k]) for k in VOICE_LANGS if k in e} or None, say=e.get("say"),
                    speed=float(e["speed"]) if "speed" in e else None)
               for sc in self.scenes for e in sc.voice]
        return sorted(out, key=lambda x: x.t)

    def resolve(self, p: str | None) -> str | None:
        if not p:
            return None
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.base, p))

    @property
    def duration(self) -> float:
        last = self.scenes[-1]
        return last.start + last.duration

    def overlap(self, sc: Scene) -> float:
        """`sc`nin bir öncekiyle örtüşme süresi (geçiş)."""
        if sc.index == 0:
            return 0.0
        kind = sc.transition or self.video.transition
        if kind == "cut":
            return 0.0
        d = sc.transition_dur if sc.transition_dur is not None else self.video.transition_dur
        prev = self.scenes[sc.index - 1]
        return max(0.0, min(d, sc.duration * 0.5, prev.duration * 0.5))


def size_for(aspect: str, short: int) -> tuple[int, int]:
    """Kısa kenar `short` → (W, H). İkisi de ÇİFT (yuv420p / libx264 tek boyutu reddeder:
    `--preview --aspect 4:5` = 540×675 → 540×674)."""
    if aspect not in ASPECTS:
        raise PromoError(f"en-boy '{aspect}' desteklenmiyor: {', '.join(ASPECTS)}")
    a, b = ASPECTS[aspect]
    if a >= b:
        h = short
        w = int(round(short * a / b))
    else:
        w = short
        h = int(round(short * b / a))
    return (w // 2 * 2, h // 2 * 2)


def _size(sz) -> int:
    if isinstance(sz, str):
        alias = SIZE_ALIASES.get(sz.strip().lower())
        if alias is None:
            try:
                alias = int(sz)
            except ValueError:
                raise PromoError(f"[video] size: '{sz}' anlaşılamadı — 720 | 1080 | 1440 | 2160 | \"4k\"")
        sz = alias
    return int(_num(sz, "[video] size", lo=16, hi=8640))


def _media(v, where: str) -> MediaRef | None:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        if v.startswith("#"):
            _color(v, where)
            return MediaRef(color=v)
        return MediaRef(path=v)
    if isinstance(v, dict):
        _check_keys(v, MEDIA_KEYS, where)
        m = MediaRef(path=v.get("src"), color=v.get("color"),
                     clip_in=_num(v.get("in", 0.0), f"{where}.in", lo=0.0),
                     clip_out=_num(v["out"], f"{where}.out", lo=0.0) if "out" in v else None,
                     speed=_num(v.get("speed", 1.0), f"{where}.speed", above=0.0),
                     loop=_bool(v.get("loop", False), f"{where}.loop"),
                     fit=_enum(v.get("fit", "cover"), ("cover", "contain"), f"{where}.fit"),
                     params=dict(v.get("params", {})),
                     scroll=_scroll(v.get("scroll", []), f"{where}.scroll"),
                     scroller=str(v["scroller"]) if "scroller" in v else None)
        if m.color is not None:
            _color(m.color, f"{where}.color")
        if m.clip_out is not None and m.clip_out <= m.clip_in:
            raise PromoError(f"{where}: out ({m.clip_out:g}) in'den ({m.clip_in:g}) büyük olmalı")
        return m
    raise PromoError(f"{where}: medya bir dosya yolu, '#renk' ya da tablo olmalı")


def _anim(anim, where: str) -> dict:
    """Cihaz anahtar kareleri: x/y (ekran yüksekliği birimi) · s (ölçek ya da "fit") · o/on/lid (0..1) ·
    rot (derece, + = saat yönü) · ease."""
    if not isinstance(anim, dict):
        raise PromoError(f"{where}: {{ tablet = [ {{t=0, y=0.5, o=0}}, … ] }} biçiminde olmalı")
    for dev_name, kfs in anim.items():
        w = f"{where}.{dev_name}"
        if dev_name not in ("tablet", "laptop"):
            raise PromoError(f"{where}: cihaz 'tablet' ya da 'laptop' olmalı ('{dev_name}')")
        if not isinstance(kfs, list):
            raise PromoError(f"{w}: anahtar kare LİSTESİ olmalı: [ {{ t = 0, x = 0.1 }}, … ]")
        for kf in kfs:
            _check_keys(kf, ANIM_KEYS, w)
            _num(kf.get("t", 0.0), f"{w}.t", lo=0.0)
            for k in ("x", "y", "rot"):
                if k in kf:
                    _num(kf[k], f"{w}.{k}")
            for k in ("o", "on", "lid"):
                if k in kf:
                    _num(kf[k], f"{w}.{k}", lo=0.0, hi=1.0)
            if "s" in kf and kf["s"] != "fit":
                if isinstance(kf["s"], str):
                    raise PromoError(f"{w}.s: sayı ya da \"fit\" olmalı (gelen: {kf['s']!r})")
                _num(kf["s"], f"{w}.s", above=0.0)
            if "ease" in kf:
                _ease(kf["ease"], w)
    return anim


def _scroll(moves, where: str) -> list:
    """Sayfa kaydırma hareketleri: `{ t, y | to, dur, ease, offset }` — her hareket `t`de, o anki konumdan başlayıp
    `dur` sn'de hedefe varır. `y` = piksel (CSS px, sayfanın başından) · `to` = bir CSS seçici (öğenin üstü; `offset`
    px kaydırır). Hareketler ZAMAN SIRASINDA ve birbirini beklemeli (çakışma hata)."""
    moves = _list_of_tables(moves, where)
    out = []
    for i, m in enumerate(moves):
        w = f"{where}[{i + 1}]"
        _check_keys(m, SCROLL_KEYS, w)
        if ("y" in m) == ("to" in m):
            raise PromoError(f"{w}: tam olarak biri gerekli — y = piksel  ya da  to = \"#seçici\"")
        d = {"t": _num(m.get("t", 0.0), f"{w}.t", lo=0.0), "dur": _num(m.get("dur", 1.0), f"{w}.dur", lo=0.0),
             "ease": _ease(m.get("ease", "inout"), w)}
        if "y" in m:
            d["y"] = _num(m["y"], f"{w}.y", lo=0.0)
        else:
            if not isinstance(m["to"], str) or not m["to"].strip():
                raise PromoError(f"{w}.to: CSS seçici (ör. \"#fiyat\") olmalı (gelen: {m['to']!r})")
            d["to"] = m["to"]
            d["offset"] = _num(m.get("offset", 0.0), f"{w}.offset")
        out.append(d)
    out.sort(key=lambda d: d["t"])
    for a, b in zip(out, out[1:]):
        if b["t"] < a["t"] + a["dur"] - 1e-9:
            raise PromoError(f"{where}: hareketler çakışıyor — t={a['t']:g} (dur {a['dur']:g} sn) bitmeden "
                             f"t={b['t']:g} başlıyor")
    return out


def _camera(cam, layout: str, where: str) -> list:
    cam = _list_of_tables(cam, where)
    for kf in cam:
        _check_keys(kf, {"t", "zoom", "at", "on", "ease", "dur"}, where)
        _num(kf.get("t", 0.0), f"{where}.t", lo=0.0)
        _num(kf.get("dur", 0.9), f"{where}.dur", lo=0.0)
        _num(kf.get("zoom", 1.0), f"{where}.zoom", above=0.0)
        if "ease" in kf:
            _ease(kf["ease"], where)
        if "at" in kf:
            at = kf["at"]
            if not isinstance(at, list) or len(at) != 2:
                raise PromoError(f"{where}.at: [u, v] olmalı (gelen: {at!r})")
            for a in at:
                _num(a, f"{where}.at")
        if "on" in kf:
            _enum(kf["on"], LAYOUT_DEVICES[layout], f"{where}.on (yerleşim '{layout}')")
    return cam


VOICE_KEYS = {"engine", "lang", "id", "model", "stability", "similarity", "style", "speed", "volume", "lexicon",
              "exaggeration", "cfg_weight", "temperature", "seed", "prompt"}


def _voice_cfg(vd) -> VoiceCfg:
    """`[voice]`: ses motoru + ses + ayarlar (promokit/voice.py). Aralıklar ElevenLabs'inkiler."""
    _check_keys(vd, VOICE_KEYS, "[voice]")
    c = VoiceCfg()
    if "engine" in vd:
        c.engine = _enum(str(vd["engine"]), VOICE_ENGINES, "[voice] engine")
    if "lang" in vd:
        c.lang = _enum(str(vd["lang"]), VOICE_LANGS, "[voice] lang")
    for k in ("id", "model"):
        if k in vd:
            setattr(c, k, str(vd[k]))
    for k in ("stability", "similarity", "style"):
        if k in vd:
            setattr(c, k, _num(vd[k], f"[voice] {k}", lo=0.0, hi=1.0))
    if "speed" in vd:
        c.speed = _num(vd["speed"], "[voice] speed", lo=0.7, hi=1.2)
    if "volume" in vd:
        c.volume = _num(vd["volume"], "[voice] volume", lo=0.0)
    if "lexicon" in vd:
        lx = vd["lexicon"]
        if not isinstance(lx, dict) or not all(isinstance(k, str) and isinstance(x, str) and k for k, x in lx.items()):
            raise PromoError('[voice] lexicon: { "Yazılış" = "okunuş", … } (metin → metin) olmalı')
        c.lexicon = dict(lx)
    if "exaggeration" in vd:
        c.exaggeration = _num(vd["exaggeration"], "[voice] exaggeration", lo=0.25, hi=2.0)
    if "cfg_weight" in vd:
        c.cfg_weight = _num(vd["cfg_weight"], "[voice] cfg_weight", lo=0.0, hi=1.0)
    if "temperature" in vd:
        c.temperature = _num(vd["temperature"], "[voice] temperature", lo=0.05, hi=2.0)
    if "seed" in vd:
        c.seed = int(_num(vd["seed"], "[voice] seed", lo=0))
    if "prompt" in vd:
        c.prompt = str(vd["prompt"])      # yol: yüklemenin sonunda senaryoya göre çözülür ve denetlenir
    return c


def load(path: str, aspect: str | None = None, short: int | None = None, fps: float | None = None) -> Story:
    path = os.path.abspath(path)
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except FileNotFoundError:
        raise PromoError(f"senaryo yok: {path}")
    except tomllib.TOMLDecodeError as e:
        raise PromoError(f"{os.path.basename(path)}: TOML sözdizimi hatası — {e}")
    base = os.path.dirname(path)
    _check_keys(doc, {"video", "scene", "voice"}, "senaryo kökü")
    vd = doc.get("video", {})
    _check_keys(vd, VIDEO_KEYS, "[video]")
    v = VideoCfg()
    for k, lo, above in (("fps", 1.0, None), ("music_volume", 0.0, None),
                         ("transition_dur", 0.0, None), ("caption_size", None, 0.0)):
        if k in vd:
            setattr(v, k, _num(vd[k], f"[video] {k}", lo=lo, above=above))
    for k in ("theme", "background", "font", "music", "transition", "laptop_finish", "tablet_color",
              "codec", "out", "title", "lang", "laptop_model", "music_mood"):
        if k in vd:
            setattr(v, k, str(vd[k]))
    if "crf" in vd:
        v.crf = int(_num(vd["crf"], "[video] crf", lo=0, hi=51))
    v.aspect = aspect or str(vd.get("aspect", "16:9"))
    v.width, v.height = size_for(v.aspect, _size(short if short is not None else vd.get("size", 1080)))
    if fps is not None:
        v.fps = _num(fps, "fps", lo=1.0)
    _enum(v.lang, VOICE_LANGS, "[video] lang")
    _enum(v.transition, TRANSITIONS, "[video] transition")
    _enum(v.theme, THEMES, "[video] theme")
    _enum(v.codec, CODECS, "[video] codec")
    from .musicbed import MOODS
    _enum(v.music_mood, sorted(MOODS), "[video] music_mood")
    from .devices import LAPTOP_MODELS
    _enum(v.laptop_model, tuple(LAPTOP_MODELS), "[video] laptop_model")
    model = LAPTOP_MODELS[v.laptop_model]
    v.laptop_finish = v.laptop_finish or model["finish"]       # kaplama MODELE göre (Windows'ta spaceblack yok)
    _enum(v.laptop_finish, tuple(model["finishes"]), f"[video] laptop_finish ({v.laptop_model})")
    _color(v.tablet_color, "[video] tablet_color")
    if v.background:
        _color(v.background, "[video] background")
    vc = _voice_cfg(doc.get("voice", {}))
    if "lang" not in doc.get("voice", {}):
        vc.lang = v.lang       # seslendirme videonun dilinde (unutulursa İngilizce metne Türkçe okunuş + model)
    scenes = []
    raw = doc.get("scene", [])
    if not isinstance(raw, list) or not raw:
        raise PromoError("senaryoda en az bir [[scene]] olmalı")
    for i, sd in enumerate(raw):
        where = f"[[scene]] #{i + 1}"
        _check_keys(sd, SCENE_KEYS, where)
        orient = "portrait" if v.portrait else "landscape"
        for o in ("portrait", "landscape"):
            if o in sd:
                _check_keys(sd[o], SCENE_KEYS - {"portrait", "landscape"}, f"{where}.{o}")
        if orient in sd:  # yönlendirmeye özel ezmeler
            sd = {**sd, **sd[orient]}
        sd = {k: val for k, val in sd.items() if k not in ("portrait", "landscape", "note")}
        sc = Scene(index=i)
        sc.type = _enum(str(sd.get("type", "device")), sorted(SCENE_TYPES), f"{where}: type")
        if "duration" in sd:
            sc.duration = _num(sd["duration"], f"{where}.duration", above=0.0)
        sc.layout = _enum(str(sd.get("layout", "tablet")), sorted(LAYOUTS), f"{where}: layout")
        sc.tablet = _media(sd.get("tablet"), f"{where}.tablet")
        sc.laptop = _media(sd.get("laptop"), f"{where}.laptop")
        for k in ("caption", "subcaption", "title", "subtitle", "badge"):
            if k in sd:
                setattr(sc, k, str(sd[k]))
        sc.captions = _list_of_tables(sd.get("captions", []), f"{where}.captions")
        for c in sc.captions:
            _check_keys(c, {"t", "d", "text", "sub"}, f"{where}.captions")
            _num(c.get("t", 0.0), f"{where}.captions.t", lo=0.0)
            if "d" in c:
                _num(c["d"], f"{where}.captions.d", above=0.0)
        sc.camera = _camera(sd.get("camera", []), sc.layout, f"{where}.camera")
        if "transition" in sd:
            sc.transition = _enum(str(sd["transition"]), TRANSITIONS, f"{where}: transition")
        if "transition_dur" in sd:
            sc.transition_dur = _num(sd["transition_dur"], f"{where}.transition_dur", lo=0.0)
        if "still" in sd:
            sc.still = _num(sd["still"], f"{where}.still", lo=0.0)
        if "page" in sd:
            sc.page = str(sd["page"])
        if sc.type == "html" and not sc.page:
            raise PromoError(f"{where}: type=\"html\" sahnede page = \"…html\" gerekli")
        sc.params = dict(sd.get("params", {}))
        sc.scroll = _scroll(sd.get("scroll", []), f"{where}.scroll")
        if "scroller" in sd:
            sc.scroller = str(sd["scroller"])
        if (sc.scroll or sc.scroller) and sc.type != "html":
            raise PromoError(f"{where}: scroll/scroller yalnız type=\"html\" sahnede — cihaz ekranında "
                             f"tablet = {{ src = \"…\", scroll = [ … ] }} yazın")
        sc.anim = _anim(sd.get("anim", {}), f"{where}.anim")
        sc.voice = _list_of_tables(sd.get("voice", []), f"{where}.voice")
        for e in sc.voice:
            # tr/en/zh = altyazı çevirisi · say = motora giden okunuş (altyazı yine `text`) · speed = YALNIZ bu satırın
            # hızı (atempo 0,5–2; genel [voice] speed'in üstüne — kullanıcı, 2026-10-30: "ilk cümleyi çok hızlı söylüyor")
            _check_keys(e, {"t", "text", "src", "say", "speed", *VOICE_LANGS}, f"{where}.voice")
            _num(e.get("t", 0.0), f"{where}.voice.t", lo=0.0)
            if "speed" in e:
                _num(e["speed"], f"{where}.voice.speed", lo=0.5, hi=2.0)
            if not str(e.get("text", "")).strip() and not e.get("src"):
                raise PromoError(f"{where}.voice: okunacak text = \"…\" (ya da kayıtlı ses için src = \"…\") gerekli")
            if "say" in e and (not isinstance(e["say"], str) or not e["say"].strip() or not str(e.get("text", "")).strip()):
                raise PromoError(f"{where}.voice: say = \"okunuş\" metinli satırda dolu bir dizgi olmalı (altyazı text'ten)")
        scenes.append(sc)
    story = Story(path=path, base=base, video=v, scenes=scenes, voice=vc)
    # ekran dili her HTML sayfaya (cihaz ekranı + tam ekran sahne); sahnenin kendi `params.lang`i ezer.
    for sc in scenes:
        for ref in (sc.tablet, sc.laptop):
            if ref and ref.path and ref.path.lower().endswith((".html", ".htm")):
                ref.params.setdefault("lang", v.lang)
        if sc.type == "html":
            sc.params.setdefault("lang", v.lang)
    for sc in scenes:
        for ref in (sc.tablet, sc.laptop):
            if ref and ref.path:
                ref.path = story.resolve(ref.path)
                if not os.path.exists(ref.path):
                    raise PromoError(f"[[scene]] #{sc.index + 1}: medya yok: {ref.path}")
        if sc.page:
            sc.page = story.resolve(sc.page)
            if not os.path.exists(sc.page):
                raise PromoError(f"[[scene]] #{sc.index + 1}: sayfa yok: {sc.page}")
        for e in sc.voice:
            if e.get("src"):
                e["src"] = story.resolve(str(e["src"]))
                if not os.path.exists(e["src"]):
                    raise PromoError(f"[[scene]] #{sc.index + 1}: seslendirme dosyası yok: {e['src']}")
    _resolve_durations(story)
    for sc in scenes:
        if sc.still is not None and sc.still > sc.duration:
            raise PromoError(f"[[scene]] #{sc.index + 1}.still = {sc.still:g} sn, sahne {sc.duration:g} sn")
    t = 0.0
    for sc in scenes:
        t -= story.overlap(sc)
        sc.start = t
        t += sc.duration
    for k in ("music", "font"):
        p = getattr(v, k)
        if k == "music" and p == "bed":      # o videoya özel üretilen fon müziği (musicbed.story_bed)
            continue
        if p:
            p = story.resolve(p)
            if not os.path.exists(p):
                raise PromoError(f"[video] {k}: dosya yok: {p}")
            setattr(v, k, p)
    if v.out:
        v.out = story.resolve(v.out)
    if story.voice.prompt:
        story.voice.prompt = story.resolve(story.voice.prompt)
        if not os.path.exists(story.voice.prompt):
            raise PromoError(f"[voice] prompt: referans kaydı yok: {story.voice.prompt}")
    return story


def _resolve_durations(story: Story):
    from .media import Probe
    for sc in story.scenes:
        if sc.duration is not None:
            continue
        ref = sc.tablet if sc.layout != "laptop" else sc.laptop
        if ref and ref.path:
            pr = Probe(ref.path)
            if pr.kind == "video":
                end = ref.clip_out if ref.clip_out is not None else (pr.duration or 0)
                sc.duration = max(0.5, (end - ref.clip_in) / ref.speed)
                continue
        sc.duration = 3.0 if sc.type != "device" else 4.0
