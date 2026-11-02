"""Kalem tutan gerçek el (fotoğraf) → dekupe RGBA + meta. Bir kez koşulur; çıktı repoda durur.

Kaynak: Pexels #4087472 (üstten, tablet üstünde stylus ile yazan sağ el) — Pexels lisansı:
ticari kullanım + düzenleme serbest, atıf gerekmez. https://www.pexels.com/photo/4087472/

Neden fotoğraf: yazı eli iki kez prosedürel çizildi, ikisi de "gerçekte olmayan tutuş / uzaylı eli"
diye reddedildi (kullanıcı, 2026-10-19). Gerçek tutuşu taklit etmek yerine gerçeğini kullanıyoruz.

Dekupe: arka plan siyah tablet + beyaz masa, el ise sıcak tonlu → R−B (sıcaklık) eşiği temiz ayırıyor.
Siyah kalem bu eşikte arka planla birlikte düşer → kalem gövdesi elle ölçülmüş bir kapsülle geri
eklenir. Ölçüler aşağıdaki kırpma (crop) koordinatında; ızgaralı yakın planla okunmuştur.

    python3 assets/hands/build_pen_hand.py <pexels-4087472.jpg (4000 px)>
"""
import json
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
CROP = (1880, 1600, 3000, 2667)          # 4000×2667 kaynakta el + önkol
# Kalem gövdesi: kenar çizgileri 6× ızgarada ölçüldü (perspektif → uca doğru incelir)
PEN_UPPER = ((220, 430.8), (560, 544.2))  # üst kenar
PEN_LOWER = ((220, 460.1), (560, 581.3))  # alt kenar
PEN_CAP = ((566, 565), 18)                # sarı kapak (yuvarlak uç)
PEN_DIR = (-0.942, -0.336)                # kuyruktan uca kalem ekseni
TIP = (200, 438)                          # mürekkep noktası: ekseni uzatınca işaret + başparmak uçları arası
FW = 58.0                                 # işaret parmağı genişliği (px) → videodaki fw ile ölçek
# tablet kenarının sıcak tonlu şeridi: önkolun sol kenarını izleyen çokgenle silinir
ERASE = [(430, 755), (519, 768), (526, 780), (533, 790), (538, 800), (430, 800)]
# önkoldaki dövme: kola paralel yukarıdan deri klonlanır (yumuşak kenarlı elips)
TATTOO = ((622, 930), (52, 62))
TATTOO_SRC = (-40, -80)


def _decontaminate(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    """Kenar renk arındırma: yarı saydam kenar bandının rengi zeminden (beyaz masa / siyah tablet)
    sızıyor → açık zeminde beyaz, koyuda siyah hale. Bant pikselleri, içerideki KESİN el
    piksellerinin normalize bulanıklığıyla (Σ renk·maske / Σ maske) değiştirilir."""
    from PIL import ImageMath
    inner = alpha.point(lambda v: 255 if v >= 250 else 0).filter(ImageFilter.MinFilter(5))
    den = inner.filter(ImageFilter.GaussianBlur(5))
    chans = []
    for ch in rgb.split():
        num = ImageChops.multiply(ch, inner).filter(ImageFilter.GaussianBlur(5))
        fill = ImageMath.lambda_eval(lambda a: (a["n"] * 255) / (a["d"] + 1), n=num, d=den).convert("L")
        chans.append(fill)
    filled = Image.merge("RGB", chans)
    band = alpha.point(lambda v: 255 if v < 250 else 0)
    out = rgb.copy()
    out.paste(filled, (0, 0), band)
    return out


def build(src: str):
    im = Image.open(src).convert("RGB")
    if im.size != (4000, 2667):
        im = im.resize((4000, 2667), Image.Resampling.LANCZOS)
    c = im.crop(CROP)
    r, g, b = c.split()
    warm = ImageChops.subtract(r, b).point(lambda v: 255 if v > 22 else 0)
    lit = r.point(lambda v: 255 if v > 85 else 0)
    m = ImageChops.multiply(warm, lit)
    m = m.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(7))   # delikleri kapat
    m = m.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))   # kırıntıyı sil
    d = ImageDraw.Draw(m)
    d.polygon(ERASE, fill=0)
    seed = (int(c.size[0] * 0.40), int(c.size[1] * 0.55))
    ImageDraw.floodfill(m, seed, 128)
    m = m.point(lambda v: 255 if v == 128 else 0)
    # kalem gövdesi (siyah → eşikte düştü): ölçülmüş kenarlar + yuvarlak kapak
    pen = Image.new("L", c.size, 0)
    pd = ImageDraw.Draw(pen)
    pd.polygon([PEN_UPPER[0], PEN_UPPER[1], PEN_LOWER[1], PEN_LOWER[0]], fill=255)
    (cx, cy), cr = PEN_CAP
    pd.ellipse((cx - cr, cy - cr, cx + cr, cy + cr), fill=255)
    m = ImageChops.lighter(m, pen)
    # kalemin gölgesindeki deri eşiği geçemiyor → kalemle deri arasında ince boşluk kalıyordu
    m = m.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))
    # dövme rötuşu: aynı koldan, bileğe yakın deri
    (tx, ty), (rx, ry) = TATTOO
    dx, dy = TATTOO_SRC
    patch = c.crop((tx - rx + dx, ty - ry + dy, tx + rx + dx, ty + ry + dy))
    feather = Image.new("L", patch.size, 0)
    ImageDraw.Draw(feather).ellipse((8, 8, patch.size[0] - 8, patch.size[1] - 8), fill=255)
    feather = feather.filter(ImageFilter.GaussianBlur(7))
    c.paste(patch, (tx - rx, ty - ry), feather)
    # kenar: 1 px içeri al (zeminle karışmış kenar pikselleri) + yumuşat
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    # önkolun fotoğraf kenarındaki kesiği: son 40 px yumuşak sönsün (kadraja girerse sert çizgi olmasın)
    fade = Image.linear_gradient("L").resize((c.size[0], 40)).point(lambda v: 255 - v)
    m.paste(ImageChops.multiply(m.crop((0, c.size[1] - 40, c.size[0], c.size[1])), fade), (0, c.size[1] - 40))
    c = _decontaminate(c, m)
    bbox = m.getbbox()
    out = c.convert("RGBA")
    out.putalpha(m)
    out = out.crop(bbox)
    ox, oy = bbox[0], bbox[1]
    out.save(os.path.join(HERE, "pen-hand.png"), optimize=True)
    meta = {
        "source": "https://www.pexels.com/photo/4087472/",
        "license": "Pexels License (ticari kullanım + düzenleme serbest, atıf gerekmez)",
        "tip": [TIP[0] - ox, TIP[1] - oy],
        "pen_dir": list(PEN_DIR),
        "fw": FW,
        "size": list(out.size),
    }
    with open(os.path.join(HERE, "pen-hand.json"), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print("✓", out.size, meta["tip"])


if __name__ == "__main__":
    build(sys.argv[1])
