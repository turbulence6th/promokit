"""Üretilen seslendirme METNİ söylüyor mu — yazıya dökülen sözcüklerle karşılaştırma (bağımlılıksız; Chatterbox
ortamındaki `tts_chatterbox.py` ve aracın testleri aynı kodu kullanır).

Ölçülen arızalar (2026-10-20, en-B sesi): cümle bitince UYDURMA konuşma ("Macs don't have a touchscreen.
What is a presenting in all of…") · TEKRAR ("now in open beta, now in open beta") · eksik sözcük.
Özel adlar (marka, ürün) tanıyıcıda bölünebilir ("Acmeify" → "acme if I") → ölçüt sözcük değil HARF benzerliği.
"""
import difflib
import re

RULE = 2              # ölçüt sürümü — önbellek anahtarına girer: ölçüt değişince eski kararlar yeniden üretilir
RATIO_OK = 0.8        # yazıya dökülenle metnin harf benzerliği (marka bölünmesi ≈ 0,82 — ölçüldü)
LEN_SLACK = 1.2       # duyulan metin bundan uzunsa fazladan konuşma var (tekrar / uydurma kuyruk)


def norm(s: str) -> str:
    s = s.lower().replace("-", " ").replace("'", "").replace("\u2019", "")
    return " ".join(re.findall(r"[^\W_]+", s))


def judge(target: str, words: list) -> dict:
    """words: [(sözcük, başlangıç, bitiş)]. Metne EN çok benzeyen ön ek bulunur (eşitlikte en kısası);
    `end` onun son sözcüğünün bitişi → çağıran oradan kırpar, `next` kuyruğun ilk sözcüğünün başı (kırpma
    payı onu geçmesin). `ok`: benzer VE ön ekten sonra HİÇ sözcük yok — "…its own speaker. Bad day!"
    kırpılabilir ama kuyruk araya sızabilir; yeniden üretmek daha temiz (ölçüldü 2026-10-20)."""
    words = [x for x in words if norm(x[0])]       # tanıyıcının noktalama "sözcükleri" ("-") sayılmaz
    t = norm(target)
    best = (-1.0, 0)
    for k in range(1, len(words) + 1):
        r = difflib.SequenceMatcher(None, t, norm(" ".join(w for w, _, _ in words[:k]))).ratio()
        if r > best[0] + 1e-9:
            best = (r, k)
    ratio, k = best
    heard = norm(" ".join(w for w, _, _ in words))
    ok = bool(words) and ratio >= RATIO_OK and k == len(words) and len(heard) <= len(t) * LEN_SLACK + 3
    return {"ok": ok, "ratio": round(max(ratio, 0.0), 3), "heard": " ".join(w.strip() for w, _, _ in words),
            "start": words[0][1] if words else 0.0, "end": words[k - 1][2] if words else 0.0,
            "next": words[k][1] if 0 < k < len(words) else None}
