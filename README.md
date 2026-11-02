# promokit — HTML'den animasyonlu video

Bir **HTML sayfasını** (CSS animasyonları, kendi JavaScript'i, kaydırma) başsız bir tarayıcıda **kare kare** oynatıp
MP4'e çevirir; üstüne başlık, seslendirme, fon müziği ve altyazı ekler. Ekran kaydı yoktur: sayfa gerçek zamanda
çalışmaz, sabit bir kare saatinde ilerler → her render birebir aynıdır, paralel render parçaları dikişsiz birleşir.

Tipik kullanımlar: bir web sitesinin tanıtım videosu · HTML/CSS ile yazılmış hareketli grafik · tablet/dizüstü
çerçevesi içinde bir arayüz gösterimi.

```
senaryo.toml ──►  sahneler (HTML sayfa / başlık kartı / cihaz çerçevesi) ──► python3 promo.py render ──► MP4 (+ .srt)
```

Python 3.11+ · Pillow · ffmpeg · bir Chromium tarayıcı. Başka bağımlılık yok (numpy bile yok).

## Gereksinim

| | |
|---|---|
| Python 3.11+ | `tomllib` için. Paket: yalnız **Pillow** (`pip install -r requirements.txt`). |
| ffmpeg + ffprobe | kodlama (`brew install ffmpeg`). Yol bulunamazsa `FFMPEG=` / `FFPROBE=` ortam değişkeni. |
| Chrome / Brave / Chromium / Edge | HTML sayfaları oynatmak için (başsız çalıştırılır). Bulunamazsa `PROMO_BROWSER=<yol>`. |
| Lexend (isteğe bağlı) | varsayılan başlık yazı tipi ([Google Fonts](https://fonts.google.com/specimen/Lexend), OFL). Yoksa sistem fontu; başka font: `[video] font = "yol.ttf"`. |
| Seslendirme (isteğe bağlı) | [aşağıda](#seslendirme-müzik-altyazı). Taslak için Mac'in `say`'i yeter. |

Geliştirildiği ve denendiği platform **macOS**. Windows/Linux için tarayıcı yolları aranır ama orada denenmedi.

## Hızlı başlangıç

```bash
pip install -r requirements.txt
python3 promo.py render examples/animasyon.toml --preview      # 540p/30 fps taslak → work/out/en/animasyon-preview.mp4
python3 promo.py render examples/site.toml                     # 1080p60: gerçek bir site, kaydırmalı, sesli
python3 promo.py sheet  examples/site.toml --every 1.5         # tüm akış tek PNG'de (zamanlama kontrolü)
python3 promo.py still  examples/site.toml -t 6                # tek kare
```

Çıktılar `work/out/[<dil>/]` altına yazılır (`.gitignore`'lu). Komutların tamamı: `python3 promo.py -h`.

- `examples/animasyon.html` + `animasyon.toml` — **saf HTML animasyonu**: tam ekran sayfa, CSS keyframes + `promo.on('frame')`,
  TOML'dan gelen parametre, seslendirme + müzik.
- `examples/site/` + `site.toml` — **gerçek bir web sitesi** (ayrı HTML/CSS/JS/SVG dosyaları; sticky menü, CSS giriş
  animasyonu, görününce-beliren kartlar) dizüstü çerçevesinde, senaryodaki kaydırma çizelgesiyle gezdirilir.

## Sayfa nasıl oynar

Sayfa **gerçek zamanda çalışmaz**: araç her video karesi için sayfaya "k. kareye git" der, ekran görüntüsü alır. Zaman
`k / fps`'dir. Sayfanıza şunlar yeter:

- **CSS animasyonları ve geçişleri serbesttir** — araç onları kare saatine sabitler (Web Animations API). `@keyframes`,
  `transition`, `animation-delay` olduğu gibi çalışır.
- **Kod gereken hareket için** `promo.on('frame', ({ t, k }) => …)`: her karede bir kez çağrılır, `t` = sahne içi sayfa
  zamanı (sn). Sayıcılar, canvas, veriye bağlı yerleşim… `t`'nin SAF fonksiyonu olmalı.
- `promo.params` = senaryodaki `params` (+ `lang`); sayfa betikleri çalışırken **hazırdır**.
  `promo.ease(x, "inout")` = Python tarafındaki aynı yumuşatma eğrileri. `promo.scrollY` = o karedeki kaydırma.
- `window.promo` yalnız araç altında vardır → sayfanız normal tarayıcıda da aynen çalışır (`if (window.promo) …`).
- ⛔ **`setTimeout` / `setInterval` / `requestAnimationFrame` / `Date.now` ile zaman tutmayın** — sayfa oynatılmaz, adım
  adım ilerletilir; gerçek zamanlı sayaçlar donar.
- Sayfa boyutu: `<meta name="promo-viewport" content="1470x956">` (CSS px; yoksa 1400×920) — **cihaz çerçevesi içindeki**
  sayfalar için. Tam ekran (`type = "html"`) sahnede sayfa video en-boyuna göre yerleşir (kısa kenar 1080 CSS px);
  `vmin/vw/vh` ile yazarsanız aynı dosya 16:9, 9:16, 1:1'de çalışır.
- `IntersectionObserver` (görününce belir) ve `scroll` olayları **kare saatine bağlanır**: render parçalarının her
  koşuda aynı karede tetiklenir. `scroll` dinleyicileriniz idempotent olmalı (olağan durum).
- `scroll-behavior: smooth` araç tarafından kapatılır (kaydırmayı zaten çizelge yumuşatır).

## Senaryo (TOML)

Göreli yollar **senaryo dosyasına göre** çözülür. Bilinmeyen alan, bozuk değer, çakışan kaydırma… render'dan ÖNCE
yükleme anında, yakın eşleşme önerisiyle hata verir.

```toml
[video]
aspect = "16:9"       # 16:9 | 9:16 | 4:5 | 1:1 | 4:3 | 3:4   (komut satırında --aspect ile ezilir)
size = 1080           # kısa kenar px (720 | 1080 | 1440 | "4k")
fps = 60
theme = "light"       # light | dark — başlık/kart renkleri (koyu sayfada "dark" kullanın)
lang = "en"           # tr | en | zh — çıktı klasörü, altyazı + seslendirme dili, promo.params.lang
music = "bed"         # "bed" = ffmpeg ile üretilen telifsiz fon · ya da dosya yolu
# diğerleri: background, font, music_volume, music_mood, transition, transition_dur, caption_size, laptop_model,
#            laptop_finish, tablet_color, codec (h264|hevc|prores), crf, out, title (dosya üst verisi)

[[scene]]
type = "html"                 # tam ekran HTML sayfa
page = "animasyon.html"
duration = 5.4
params = { headline = "Merhaba" }          # → promo.params.headline
scroll = [ { t = 1, to = "#fiyat", offset = -64, dur = 1.6 } ]    # uzun sayfa için (aşağıda)
caption = "Başlık"                         # üstte başlık bandı
subcaption = "Alt satır"
voice = [ { t = 0.4, text = "Bu cümle seslendirilir." } ]
```

**Sahne türleri:** `html` (tam ekran sayfa) · `title` / `outro` (kart: `title`, `subtitle`, `badge`) · cihaz sahnesi
(varsayılan; `layout = "tablet" | "laptop" | "duo"`, ekran içeriği = HTML sayfa **ya da** video/görüntü **ya da** `"#renk"`:
`laptop = { src = "site/index.html" }`, `tablet = "ekran.png"`).

**Başlıklar:** `caption` + `subcaption` tüm sahne boyunca; zamanlı için
`captions = [ { t = 0.5, d = 2.5, text = "…", sub = "…" } ]`. Başlık bandı her sahnede ayrılır ve en uzun sarılmış
başlığa göre ölçülür; cihaz gövdesi hiçbir anda bandın içine girmez.

**Geçişler:** `transition = "auto" | "morph" | "fade" | "cut" | "slide"` (sahne ya da `[video]` düzeyinde),
`transition_dur`. `auto`: iki cihaz sahnesi arasında ortak cihaz yeni yerine kayar, diğerleri çapraz geçer.

**Kare çıkarma:** sahnede `still = 3.2` → `python3 promo.py stills senaryo.toml [--format jpg --max-kb 1900]`.

### Kaydırma çizelgesi (`scroll`)

Sayfa **gerçekten kaydırılır** (`window.scrollTo`): `position: sticky/fixed`, `animation-timeline: scroll()`, sitenin kendi
scroll/IntersectionObserver kodu olduğu gibi çalışır.

```toml
scroll = [
  { t = 1.6, to = "#ozellikler", offset = -64, dur = 1.6 },   # CSS seçiciyle bir bölüme (öğenin üstü; offset px)
  { t = 8.0, y = 0, dur = 1.4, ease = "out" },                # ya da tam piksel konumuna
]
```

Her hareket `t`'de, o anki konumdan başlayıp `dur` sn'de hedefe varır (`ease`: linear · in · out · inout · sine ·
outback · quart; vars. `inout`). Hareketler zaman sırasında ve birbirini beklemeli (çakışma = hata). Hedef kaydırılabilir
aralığa sıkıştırılır. Yazılacağı yer: tam ekran sahnede sahnenin kendisi (`type = "html"` + `scroll`), cihaz çerçevesinde
ekran tablosu (`laptop = { src = "…", scroll = [ … ] }`). Sayfa değil bir kutu kayıyorsa `scroller = "#kutu"`.
Zaman, sayfa zamanıdır (`in`/`speed` verdiyseniz onlara göre).

### Cihaz çerçevesi, kamera, animasyon

`layout = "tablet" | "laptop" | "duo"` sayfayı/videoyu MatePad 11,5" ya da 14" dizüstü (`[video] laptop_model = "macbook" |
"windows"`, `laptop_finish`, `tablet_color`) mockup'ının ekranına yerleştirir.

**Kamera:** `camera = [ { t = 3.2, zoom = 1.12, at = [0.65, 0.3], dur = 0.8, on = "laptop" } ]` — `t`'de başlar, `dur` sn'de
yakınlaşır (`at` = cihaz ekranında 0–1 normalize nokta; `python3 promo.py grid medya.png` koordinat ızgarası çizer).

**Cihaz animasyonu:** `anim = { tablet = [ { t = 0, on = 0 }, { t = 1.8, on = 1 } ], laptop = [ { t = 0.8, lid = 1 }, { t = 1.75, lid = 0 } ] }`
— `x`/`y` (ekran yüksekliği birimi), `s` ölçek (ya da `"fit"`), `o` saydamlık, `on` ekran açık, `lid` dizüstü kapağı,
`rot` derece, `ease`. Bir karede yazılmayan özellik önceki değerini korur.

## Bir web sitesinin tanıtım videosu

`examples/site/` bunun çalışan örneği. Sitenize dokunmanız genelde gerekmez:

1. Cihaz çerçevesinde göstereceksiniz → `<head>`'e `<meta name="promo-viewport" content="1470x956">` (ekran oranı).
   Tam ekran sahnede gerekmez.
2. Senaryoya sahneyi + `scroll` çizelgesini yazın (yukarıda). Bölümlere `id` verin, `to = "#id"` ile gezin.
3. İsteğe bağlı: "şu anda şu açılsın" gibi zamanlı koreografi için sayfaya
   `if (window.promo) promo.on('frame', ({ t }) => el.classList.toggle('open', t >= 9.4));`

### Sınırlar (denendi, ölçüldü)

| | |
|---|---|
| Tıklama / dokunma / hover yok | Araç sayfaya fare/dokunma göndermez; `:hover` uygulanmaz. Açılır menü, modal, sekme gibi durumları `promo.on('frame')` ile `t`'ye bağlayın ya da CSS animasyonuyla yapın. |
| Başka sayfaya giden bağlantı | Render'ı durdurur (`__promoInit çağrılmadı`) — sayfa ilk yüklenenden başka belgeye gidemez. Tek sayfalık (ya da bölümlü) akış kurun. |
| `file://` kuralları | `<script type="module">` ve `fetch()` Chromium'da `file://` altında çalışmaz → klasik `<script src>` kullanın, veriyi betiğe gömün. |
| Zamanlayıcılar | `setTimeout/rAF/Date.now` ile hareket donar (yukarıda). Video/GIF öğeleri kare saatine uymaz. |
| Ağ | Uzak font/görsel/API isteği render'ı yavaşlatır ve her koşuda aynı olmayabilir → dosyaları yerel tutun. |
| Kesirli ölçek | Cihaz çerçevesinde sayfa CSS boyutundan farklı ölçekle çekilir; CSS `transform` animasyonunun kompozitör rasterı bir-iki kenar pikselinde paralel parça sınırında oynayabilir (görsel olarak fark edilmez; `-j 1` ile birebir). Tam ekran 1080p sahnelerde ölçek 1'dir. |

## Seslendirme, müzik, altyazı

```toml
[voice]
engine = "say"             # say (Mac, taslak) | chatterbox (yerel, ücretsiz) | google | elevenlabs
id = "Samantha"            # say: sistem sesi (say -v '?') · google/elevenlabs: ses kimliği
lang = "en"                # tr | en | zh
speed = 1.0                # 0,7–1,2
lexicon = { "SQL" = "sequel" }   # okunuş düzeltmesi (altyazıya dokunmaz)

[[scene]]
voice = [ { t = 0.1, text = "Hello there.",            # altyazı + varsayılan okunuş
            say = "Hello, there.",                     # ops.: motora giden okunuş
            speed = 0.85,                              # ops.: YALNIZ bu satırın hızı
            tr = "Merhaba." } ]                        # ops.: çeviri altyazı izi (tr/en/zh)
```

```bash
python3 promo.py voice  senaryo.toml          # üret (önbellekli) + zamanlama/doğrulama uyarıları
python3 promo.py subs   senaryo.toml --lang tr   # seslendirmeden SRT
python3 promo.py music                        # telifsiz yer tutucu fon → work/music/bed-….wav
python3 promo.py render senaryo.toml --music bed
#   → work/out/senaryo.mp4 + senaryo.en.srt + senaryo.tr.srt
python3 promo.py burn work/out/senaryo.mp4 --lang tr   # SRT'yi görüntüye yak (SRT kabul etmeyen platformlar için)
```

- **Motorlar:** `say` — Mac'in yerel sesi, kurulum yok, taslak için (`id` vermezseniz Türkçe sistem sesi Yelda denenir;
  İngilizce için `id = "Samantha"`). `chatterbox` — yerel açık kaynak model, hesap/kart yok; **ayrı** Python ortamında
  (torch ağır): `python3 -m venv work/tts-venv && work/tts-venv/bin/pip install chatterbox-tts 'setuptools<81' faster-whisper`
  (modeller ilk koşuda iner; `faster-whisper` üretilen sesi yazıya döküp metinle doğrular). `google` / `elevenlabs` — bulut;
  anahtar `GOOGLE_TTS_API_KEY` / `ELEVENLABS_API_KEY` ortam değişkeninden ya da `~/.config/promokit/google-tts.key` /
  `elevenlabs.key` dosyasından (anahtar repoya GİRMEZ; metinler servise gönderilir).
- **Zamanlama:** satırlar üst üste binmez (gerekirse kayar); `voice` komutu geç başlayan ve video bitiminden taşan
  satırı söyler. Önbellek `work/voice/`: aynı metin+ayar yeniden üretilmez.
- **Satır hızı:** `speed` (0,5–2) yalnız o satırı atempo ile yavaşlatır/hızlandırır, perde aynı.
- **Okunuş sözlüğü:** `voice.DEFAULT_LEXICON` İngilizce kelimelerin **Türkçe seslendirmedeki** okunuşudur; yalnız
  `lang = "tr"`de uygulanır; `[voice] lexicon` ezer/ekler.
- **Seviye:** her satır −20 LUFS'a, müzik onun ~10 dB altına ölçülerek oturur; konuşurken müzik ~8 dB daha kısılır.
  `music_volume` / `[voice] volume` bu dengenin çarpanı.
- **Müzik:** `[video] music = "bed"` o videoya ÖZEL fon üretir (ruh hâli `music_mood`: bright · night · drive · warm ·
  dreamy · uplift). Stdlib sentezi — telif sorusu yok. Gerçek parça için `--music dosya.wav`.

## Render

```bash
python3 promo.py render senaryo.toml [-o out.mp4] [--aspect 9:16] [--size 1080] [--fps 60] [-j 6] [--preview]
#   --preview  540p/30 fps/ultrafast taslak · --from/--to sn aralığı · --codec h264|hevc|prores · --crf 0-51
```

Paralel render: zaman çizelgesi `-j` parçaya bölünür, her parça ayrı süreçte kodlanır ve kayıpsız birleştirilir. Çıktı
BT.709 etiketli. Aynı senaryo `--aspect 9:16` ile dikey (Shorts/Reels) üretilir.

## Başka bir projede kullanma

Araç **bağımsız bir klasördür**: kökünde `promo.py`, yanında `promokit/` paketi. Kurulum klasörü bir yere koymaktan
ibarettir; projenizin kodunu ya da derleme sistemini etkilemez.

```
sizin-repo/
├─ tools/promokit/          ← bu reponun tamamı (kopya, submodule ya da subtree)
└─ videos/                  ← SİZİN senaryolarınız + sayfalarınız
    ├─ tanitim.toml
    └─ site/index.html
```

```bash
python3 tools/promokit/promo.py render videos/tanitim.toml -o dist/tanitim.mp4
```

Senaryodaki yollar senaryoya göre çözülür, yani senaryoları ve sayfaları aracın klasörünün DIŞINDA tutabilirsiniz.
Ya da bir kez kurup her yerden çağırın: `alias promo='python3 ~/araclar/promokit/promo.py'`.

CI'da: `pip install -r requirements.txt` + ffmpeg + bir Chromium (`PROMO_BROWSER=<yol>`); `bash run-tests.sh` testleri koşar.

## Platform notları

- **9:16 güvenli alan** (Reels/TikTok/Shorts arayüzü videonun ÜSTÜNE biner): başlık üstten **%13**'ün altında başlar,
  cihazlar alttan **%15**'in üstünde biter (otomatik). Tam ekran sayfalar aynı çizgileri `promo.params.captionTop` /
  `safeBottom` ile alır. Telefonda okunurluk: 9:16'da başlık ×1,15, alt satır ×1,5 büyür.
- **Satır kırma:** sığmayan metin önce ` · ` ifade sınırından kırılır, sonra kelime kelime. Kesin kırma için `\n`.
- **Altyazı yakma:** bazı platformlar SRT almaz → `promo.py burn` metni PNG'ye çizip bindirir (ffmpeg'de drawtext/libass
  olmayabilir).
- **Sessiz oto-oynatma:** başlıklar zaten görüntüye yakılı üretilir.

## Kod haritası

| Dosya | İş |
|---|---|
| `promo.py` | komut satırı (render, still, stills, sheet, info, voice, subs, burn, music, grid) |
| `promokit/story.py` | TOML ayrıştırma + doğrulama (yazım hatasında en yakın alanı önerir) |
| `promokit/render.py` | zaman çizelgesi, sahne çizimi, geçişler, paralel render, kodlama |
| `promokit/htmlscreen.py` · `runtime/promo.js` · `cdp.py` | sayfa oynatıcı: başsız Chromium (stdlib CDP), kare saati, kaydırma, IntersectionObserver |
| `promokit/layout.py` · `devices.py` | cihaz yerleşimi, kamera, mockup'lar (tablet / MacBook / Windows dizüstü) |
| `promokit/overlays.py` · `text.py` | arka plan, başlık, yazı |
| `promokit/voice.py` · `tts_chatterbox.py` · `asrcheck.py` · `subs.py` · `burn.py` · `musicbed.py` | seslendirme, doğrulama, altyazı, fon müziği |
| `examples/` | `animasyon.*` (saf HTML animasyonu) · `site.toml` + `site/` (gerçek site) |
| `tests/` | birim + tarayıcı testleri (`bash run-tests.sh`) |

## Testler

```bash
bash run-tests.sh          # birim + tarayıcı testleri; atlanan test = hata
```

Testlerin çoğu tarayıcısız ve hızlıdır; HTML sayfa testleri başsız Chromium açar. macOS'ta Chromium'un code-sign klonu
sızıntısına karşı koruma `promokit/cdp.py` içindedir ve `run-tests.sh` koşu sonunda denetler.
