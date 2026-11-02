"""Altyazı (SRT) dışa aktarımı — seslendirme satırlarının GERÇEK zamanlarıyla (seslendirilmiş süre +
`voice.schedule` kaydırması). Kullanıcı (2026-10-20): videolar İngilizce seslendirilir; Türkçe
YouTube'a kendi altyazı izi olarak eklenir. Satırdaki çeviri: `voice = [ { …, tr = "…" } ]`.

    python3 promo.py subs examples/animasyon.toml --lang tr
"""
from __future__ import annotations

import textwrap

from .util import PromoError

MAX_CHARS = 42          # YouTube/yayın altyazısı için yaygın satır uzunluğu
MAX_LINES = 2


def _ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str) -> list:
    lines = textwrap.wrap(text, MAX_CHARS)
    if len(lines) > MAX_LINES:      # dengeli iki satır: genişlik kademeli artar
        w = MAX_CHARS
        while len(lines) > MAX_LINES:
            w += 4
            lines = textwrap.wrap(text, w)
    return lines


def srt(lines: list, lang: str, default_lang: str | None = None) -> str:
    """Satırlar → SRT. `lang` seslendirme diliyse (`default_lang`) satırın kendi metni, değilse çevirisi."""
    out, missing = [], []
    for i, ln in enumerate(sorted(lines, key=lambda x: x.t), 1):
        text = ln.text if lang == default_lang else (ln.subs or {}).get(lang)
        if not text:
            missing.append(ln.text or str(ln.src))
            continue
        out.append(f"{i}\n{_ts(ln.t)} --> {_ts(ln.t + ln.dur)}\n" + "\n".join(_wrap(text)))
    if missing:
        raise PromoError(f"'{lang}' çevirisi eksik satırlar (satıra {lang} = \"…\" ekleyin):\n  " + "\n  ".join(missing))
    return "\n\n".join(out) + "\n"
