# promokit — senaryodan ürün tanıtım videosu

Bir **TOML senaryosundan** MP4 üretir: tablet / dizüstü mockup'ı, içinde ekran içeriği (HTML sayfa, ekran
görüntüsü, video ya da gerçek kayıt), o ekrandaki **dokunuş jestleri** (dokunulan yerde daire), başlıklar,
kamera yakınlaştırması, sahne geçişleri, isteğe bağlı seslendirme + müzik + altyazı. 16:9 · 9:16 · 4:5 · 1:1.

```
 yakala (isteğe bağlı) ───────────► senaryo (TOML) ──────────► render
 capture mac / tablet / shot          sahneler, jestler,         MP4 (H.264 / HEVC / ProRes)
 + tabletteki gerçek dokunuşlar       başlıklar, kamera,         + .srt altyazı (seslendirme varsa)
   (adb getevent, kare-senkron)       seslendirme
```

Ekran içeriği iki türlü olabilir:

- **HTML sayfa** (`examples/ekran.html`): başsız tarayıcıda **kare kare** oynatılır. Sayfa, daireleri süren
  AYNI jest listesini alır → kaydırınca sayfa o karede kayar, dokununca düğme basılır, kalemde mürekkep çıkar.
  Senkron yapısal, elle zamanlama yok.
- **Görüntü / video / gerçek kayıt**: `tablet = "ekran.png"`, `tablet = { src = "kayit.mov", in = 2, out = 9 }` ya da
  `capture` ile alınmış bir çekim (`take = "work/takes/ad"`) — jestler dokunuşlarla birlikte kaydedilir.

> **Bu paket nedir?** Bir ürünün (Mac ekranını Android tablete yansıtan bir uygulama) tanıtım videosu için
> yazılmış aracın, o ürüne özel kısımlar ayıklanmış hâlidir. Kod yorumları ve hata iletileri **Türkçe**; yorumlarda
> özgün projenin karar tarihçesi (neden böyle yapıldığı, ölçülen arızalar) duruyor — silmeyin, işe yarar.
> Neyin çıkarıldığı en altta: [Bu paketten çıkarılanlar](#bu-paketten-çıkarılanlar).

---

## Gereksinim

| | |
|---|---|
| Python 3.11+ | `tomllib` için. Paket: yalnız **Pillow** (`pip install -r requirements.txt`; numpy YOK). |
| ffmpeg + ffprobe | kodlama + video okuma (`brew install ffmpeg`). Yol bulunamazsa `FFMPEG=` / `FFPROBE=` ortam değişkeni. |
| Chrome / Brave / Chromium / Edge | yalnız **HTML ekran** için (başsız çalıştırılır). Bulunamazsa `PROMO_BROWSER=<yol>`. |
| Lexend (isteğe bağlı) | varsayılan yazı tipi ([Google Fonts](https://fonts.google.com/specimen/Lexend), OFL). Kurulu değilse sistem yazı tipine düşer; başka font için `[video] font = "yol.ttf"`. |
| Mac kaydı (isteğe bağlı) | macOS 13+, Xcode Command Line Tools (`swiftc`). İlk `capture mac`te `sck_record.swift` kendiliğinden derlenir. |
| Tablet kaydı / dokunuşlar (isteğe bağlı) | `adb` (macOS'ta Android Studio SDK'sı `~/Library/Android/sdk/platform-tools/adb`, ya da `ADB=<yol>`). |
| Seslendirme (isteğe bağlı) | [aşağıda](#4-seslendirme-müzik-altyazı). Taslak için Mac'in `say`'i yeter. |

Geliştirildiği ve denendiği platform **macOS**'tur. Render hattı (Python + ffmpeg + başsız tarayıcı) Windows/Linux
yollarını da arar (`cdp.py`, `util.py`) ama bu paket orada denenmedi. `capture mac` yalnız macOS'ta çalışır.

## Hızlı başlangıç (kayıt gerektirmez)

```bash
pip install -r requirements.txt          # Pillow
python3 promo.py info   examples/ornek.toml              # zaman çizelgesi (render yok)
python3 promo.py sheet  examples/ornek.toml --every 0.75 # tüm akış tek PNG'de (kontak sayfası)
python3 promo.py still  examples/ornek.toml -t 6.4       # tek kare
python3 promo.py render examples/ornek.toml --preview    # 540p/30 fps taslak (birkaç sn)
python3 promo.py render examples/ornek.toml              # 1080p60 → work/out/ornek.mp4
python3 promo.py render examples/ornek.toml --aspect 9:16   # aynı senaryo, dikey (Shorts/Reels)
bash run-tests.sh                                        # testler (tarayıcı gerekir)
```

Çıktılar `work/out/` altına yazılır (`work/` gitignore'lu). `examples/ornek.toml` her özelliği küçük bir parçayla
gösterir: başlık kartı, tablet + HTML ekran (kaydırma, seçiciyle dokunma, yazma, kalem), kamera, dizüstü + tablet,
kapanış kartı. Sonra kendi senaryonuzu bunun üstüne kurun.

---

## Başka bir projede kullanma

Araç **bağımsız bir klasördür**: kökünde `promo.py`, yanında `promokit/` paketi. Kurulum, klasörü bir yere koymaktan
ibarettir; ürününüzün kodunu, derleme sisteminizi ya da paket yöneticinizi etkilemez. Üç düzen:

### A) Klasörü hedef repoya kopyalayın (en basit)

```
sizin-repo/
├─ tools/promokit/          ← bu klasörün tamamı
│   ├─ promo.py
│   ├─ promokit/ …
│   └─ .gitignore           ← work/ çıktıları git'e girmesin diye (kopyayla gelir)
└─ videos/                  ← SİZİN senaryolarınız + ekran sayfalarınız (aşağıda)
    ├─ tanitim.toml
    └─ screens/uygulama.html
```

```bash
python3 tools/promokit/promo.py render videos/tanitim.toml -o dist/tanitim.mp4
```

Senaryodaki yollar **senaryo dosyasına göre göreli** çözülür (`tablet = { src = "screens/uygulama.html" }`), yani
senaryoları ve ekranları aracın klasörünün DIŞINDA, kendi repo yapınızda tutabilirsiniz. Araca dokunmanız gerekmez.

### B) Git submodule / subtree (araç güncellenebilsin istiyorsanız)

Bu klasörü ayrı bir repo yapıp hedef repoya `git submodule add <repo-url> tools/promokit` (ya da `git subtree`)
ile bağlayın. Komutlar A ile aynıdır. Araçta yaptığınız değişiklikler kendi repo geçmişinde kalır.

### C) Bir kez kurun, birçok projeden çağırın

```bash
git clone <repo-url> ~/araclar/promokit          # ya da klasörü kopyalayın
alias promo='python3 ~/araclar/promokit/promo.py'
cd herhangi-bir-proje && promo render videos/tanitim.toml
```

### Kendi ürününüzün ekranını videoya sokmak — üç yol

1. **Ekran görüntüsü / video dosyası** — en hızlısı. `tablet = "ekran.png"` ya da
   `tablet = { src = "kayit.mp4", in = 2.0, out = 9.5 }`. Jestleri senaryoda yazarsınız
   (`gestures = [ { t = 1.2, tap = [0.4, 0.3] } ]`; konum `[x, y]` ekran-normalize, sol üst 0,0). Konum seçmek için
   `python3 promo.py grid ekran.png` görüntünün üstüne ızgara çizer.
2. **HTML sayfa** — ürününüzün ekranını (ya da basitleştirilmiş bir taklidini) HTML/CSS ile yazarsınız; sayfa jestlere
   gerçekten tepki verir ve jestleri **CSS seçiciyle** hedefleyebilirsiniz (`tap = "#kaydet-dugmesi"`: konum sayfadan
   ölçülür, öğe görünür değilse ya da başka bir öğenin altındaysa hata verir). Başlangıç noktası:
   `examples/ekran.html` (başındaki yorum sözleşmeyi anlatır). Ayrıntı: [HTML ekran sözleşmesi](#html-ekran-sözleşmesi).
   ⚠️ Sayfa `file://` ile açılır ve gerçek zamanda çalışmaz (kare kare adımlanır): `fetch`/ES-module kullanan, ağ
   isteği yapan, kendi `setTimeout`/`requestAnimationFrame` saatini süren sayfalar olduğu gibi çalışmaz. Çalışan bir
   web uygulamanızı doğrudan göstermek bu pakette **denenmedi** — statik, kendi içinde bir sayfa yazmak desteklenen yoldur.
3. **Gerçek kayıt** — `capture mac` (Mac ekranı) / `capture tablet` (adb) + isteğe bağlı gerçek dokunuşlar; ayrıntı:
   [Yakalama](#1-yakalama).

### Kendi marka görünümünüz

- **Renkler**: `promokit/util.py` → `BRAND` sözlüğü (zemin, metin, vurgu gradyanı `accent`/`accent2`, çerçeve).
  Senaryoda `[video] theme = "light" | "dark"`, `background = "#rrggbb"`.
- **Yazı tipi**: `[video] font = "yol/Font.ttf"` (değişken font ağırlık ekseni desteklenir) ya da Lexend kurun.
- **Cihaz**: `[video] laptop_model = "macbook" | "windows"`, `laptop_finish`, `tablet_color` (`devices.py`).
- **Başlık / kapanış kartı**: `type = "title"` ve `type = "outro"` sahneleri (`title`, `subtitle`, `badge`). Logo ya da
  marka adı çizimi bu pakette YOK; eklemek isterseniz `render.py` → `SceneRT._build_card`, kartın öğe listesine bir
  görüntü eklemekten ibarettir.
- **Dokunuş dairesi**: varsayılan `hand = "none"` (daire). `skin`/`skin-dark`/`ghost` çizilmiş el stilleri var ama
  özgün projede kullanılmadı (daire tercih edildi).

### CI / otomasyon

Başsız çalışır (ekran açmaz). Gerekenler: Python 3.11+, Pillow, ffmpeg, Chromium (yalnız HTML ekran için).
`bash run-tests.sh` testleri koşar; **atlanan test hata sayılır** (tarayıcı yoksa yeşil görünmesin diye). Render'ı
paralel yapmak için `-j N` (varsayılan: çekirdek sayısı − 2, en çok 6).

---

## 1) Yakalama

```bash
python3 promo.py capture list                     # Mac ekranları + adb cihazları
python3 promo.py capture mac kaydirma             # → work/takes/kaydirma/  (Enter'la durur)
python3 promo.py capture tablet baglanma          # tabletin kendi ekranı (adb screenrecord)
python3 promo.py capture shot mac masaustu        # ekran görüntüsü → work/shots/
python3 promo.py capture shot tablet splash
```

**`capture mac`** — Mac ekranını ScreenCaptureKit ile kaydeder (ffmpeg'in `avfoundation` girişi yeni macOS'larda
"Capture screen" aygıtını listelemeyebiliyor; ayrıca ScreenCaptureKit ilk karenin saatini verir → dokunuşlarla
kare-doğru senkron). `--display` verilmezse **ana ekran**. `--display` sözdizimi `capture mac` ve `capture shot`ta
AYNI: `N` = `capture list` sıra no · `id:N` = ekran kimliği · başka her şey ad parçası. Tablet adb ile bağlıysa
dokunuşları da kaydeder (`--no-touches` kapatır). Seçenekler: `--fps 60` · `--codec hevc|h264|prores` · `--mbps` ·
`--max-width 3840` · `--no-cursor` · `--seconds 20` · `--serial <adb seri>`.

> ⚠️ İzin: "Ekran ve Sistem Sesi Kaydı" izni komutu **çalıştıran uygulamaya** (Terminal / iTerm / IDE) verilir. İlk
> denemede reddedilirse Sistem Ayarları'ndan açıp uygulamayı yeniden başlatın.

**`capture tablet`** — tabletin gerçek görüntüsü (`adb screenrecord`). İlk karenin saati verilmediğinden senkron
**tahminidir** → çekimden sonra `touches <çekim> --auto` ile düzeltin. Ctrl-C ile durdurmak güvenli. Süre sınırı
Android 14+'da yok, eskilerde 180 sn. Cihazdaki kopya yalnız doğrulanmış bir çekmeden SONRA silinir.

**Çekimi kontrol et** (yön/senkron hatası en hızlı gözle yakalanır):

```bash
python3 promo.py touches work/takes/kaydirma                 # özet: temaslar, süreler
python3 promo.py touches work/takes/kaydirma --plot --at 3   # izler bir karenin üstünde → touches.png
python3 promo.py touches work/takes/baglanma --auto          # hareket korelasyonuyla ofset önerisi
python3 promo.py touches work/takes/baglanma --auto --write  # öneriyi take.json'a yaz
python3 promo.py touches work/takes/kaydirma --offset 0.05   # elle (+ = dokunuşlar daha geç)
python3 promo.py touches work/takes/kaydirma --rotation 3    # yön yanlışsa ham günlükten yeniden üret
```

Yön, kayıt anındaki ekran dönüşünden alınır. **Kayıt sırasında tableti döndürmeyin.** Kalem/avuç: `MT_TOOL_PEN`
temasları kalem, `MT_TOOL_PALM` yok sayılır. Kayıttan sonra dokunuş çözümü başarısız olsa bile `take.json` yazılmıştır.

## 2) Senaryo (TOML)

> TOML kuralı: satır içi tablo `{ ... }` **tek satırda** yazılır; diziler çok satırlı olabilir.

```toml
[video]
aspect = "16:9"        # 16:9 | 9:16 | 4:5 | 1:1 | 4:3 | 3:4   (CLI --aspect ezer)
size = 1080            # kısa kenar: 720 | 1080 | 1440 | 2160 ("4k")
fps = 60
theme = "light"        # light | dark
hand = "none"          # none (vars.: dokunulan yerde daire) | skin | skin-dark | ghost
transition = "auto"    # auto (cihaz↔cihaz morph, kartlar fade) | morph | fade | slide | cut
transition_dur = 0.6
music = ""             # ses dosyası ya da "bed" (o videoya özel telifsiz fon) — aşağı: 4
lang = "tr"            # tr | en | zh — çıktı klasörü + seslendirme/altyazı dili
laptop_model = "macbook"   # macbook (14" MacBook Pro) | windows (genel Windows dizüstü)
# diğerleri: background, font, caption_size, laptop_finish, tablet_color, ripple, codec (h264|hevc|prores),
#            crf, out (varsayılan çıktı yolu, senaryoya göreli), title (dosya üst verisi), latency (sn)

[[scene]]
type = "title"         # title | outro | html | device (varsayılan)
duration = 2.6
title = "Başlık"
subtitle = "Alt satır"
badge = "Etiket"       # (ops.) küçük rozet

[[scene]]
layout = "duo"                         # tablet | laptop | duo (dizüstü + tablet)
laptop = "ekran.png"                   # görüntü, video, "#renk" ya da tablo (aşağıda)
take = "work/takes/kaydirma"           # tablet ekranı + kaydedilmiş dokunuşlar
caption = "Başlık"
subcaption = "Alt başlık"
camera = [ { t = 1.5, zoom = 1.3, at = [0.6, 0.5], dur = 0.9 }, { t = 4.0, zoom = 1.0 } ]
portrait = { layout = "tablet" }       # dikey çıkışta bu sahneyi yalnız tabletle göster
```

**Medya tablosu:** `tablet = { src = "x.mov", in = 2.0, out = 9.5, speed = 1.0, loop = false, fit = "cover" }`
(`fit = "contain"` kırpmaz). `duration` verilmezse videonun `in`–`out` süresi kullanılır. `out` oynatmayı SINIRLAR
(son kare tutulur; `loop = true` ise [in, out] aralığında döner). Çekimle birlikte: `take = "…"` +
`tablet = { in = 1.0, out = 7.0 }`. HTML sayfada da `in`/`speed` geçerli: sayfa `in + t·speed` anına sarılır ve
jestler aynı sayfa zamanına eşlenir.

**Tam ekran HTML sahnesi:** `type = "html"`, `page = "kartlar.html"` — kartlar, açılış animasyonları (CSS animasyonu
zamana sabitlenir). Sayfa `promo.params.layouts.duo/tablet` ile cihaz sahnelerinin TAM yerleşimini alır ve
`promo.params.content` ile içerik kutusunu (`[x, y, w, h]`, CSS px — başlık bandının altı): cihazlarını kendisi
çizen sayfa bandı boş bırakır.

**Sayfa parametreleri:** `tablet = { src = "ekran.html", params = { ... } }` → sayfada `promo.params`.

### Jestler

Konum ya ekran-normalize `[x, y]` (sol üst 0,0 · sağ alt 1,1), ya da HTML sayfada **CSS seçici**: `tap = "#dugme"`
(+ `at = [fx, fy]` öğe içindeki yer, vars. orta). Yol jestlerinde `on = "#tuval"` → koordinatlar o öğeye göreli.
Seçicinin yeri, jestin başladığı andaki sayfa durumundan ölçülür; öğe o an ekranda değilse, görünmezse, başka bir
öğenin ALTINDAYSA ya da `pointer-events: none` ise hata verir (üstteki öğeyi adıyla söyler). Mesafeler ekran
**genişliği** biriminde.

#### Jest anlamları

Jestler "tablet ekranı = Mac'e dokunmatik giriş" sözleşmesini modeller (tablet dokunuşunu Mac'e fare/trackpad
olarak gönderen bir ürün). Videoda gösterilen jest, ürünün gerçekte yaptığıyla aynı olsun diye anlamlar sabittir;
`tests/test_html.py` + `tests/test_gestures.py` kilitler. Başka bir sözleşme istiyorsanız eşikler ve anlamlar
`promokit/htmlscreen.py` + `promokit/runtime/promo.js` başındadır.

| jest | örnek | anlamı |
|---|---|---|
| `tap` | `{ t = 1.0, tap = "#dugme" }` | sol tık — BIRAKIŞTA · `hold` |
| `double_tap` | `{ t = 1.0, double_tap = [0.4, 0.3] }` | çift tık · `gap` |
| `long_press` | `{ t = 1.0, long_press = [0.4, 0.3] }` | **sağ tık** (halka 0,4 sn'de yeşile döner) |
| `two_finger_tap` | `{ t = 1.0, two_finger_tap = [0.4, 0.3] }` | **sağ tık**, kalkıştan 0,35 sn SONRA · ikincisinin kalkışı ilkinden ≤ 0,35 sn sonra = **akıllı yakınlaştırma** (`smartzoom`: `[data-zoom]` ×1 ↔ ×1,6) |
| `scroll` | `{ t = 1.0, scroll = [[0.6,0.7],[0.6,0.3]] }` | **tek parmak** = kaydırma (12 dp eşiğinden sonra başlar, momentum) |
| `drag` | `{ t = 1.0, drag = [[0.2,0.5],[0.7,0.6]], dur = 1.0 }` | uzun bas (`hold` ≥ 0,4 ZORUNLU, vars. 0,45) **sonra** sürükle = seçme/taşıma |
| `pointer` | `{ t = 1.0, pointer = [[0.3,0.5],[0.6,0.4]] }` | iki parmak = imleci taşı (tıksız) |
| `pinch` | `{ t = 1.0, pinch = [0.55, 0.5], from = 0.05, to = 0.30 }` | yakınlaştırma (6 dp'de devreye girer) · `angle` (°) |
| `swipe3` | `{ t = 1.0, swipe3 = [[0.5,0.7],[0.5,0.3]] }` | ↑ Mission Control · ↓ App Exposé · ←→ masaüstü (eksen kilitli) |
| `swipe4` | `{ t = 1.0, swipe4 = [[0.5,0.7],[0.5,0.3]] }` | ↑/↓ tabletin ekran klavyesi (60 dp dikeyde) |
| `swipe` | `{ t = 1.0, swipe = [[0.8,0.5],[0.2,0.5]] }` | tek parmak hızlı fiske (= kaydırma) |
| `pen` | `{ t = 1.0, pen = [[0.1,0.7],[0.5,0.4]], on = "#tuval", pressure = [0.3, 0.9] }` | kalem = sol tık/sürükleme; basınç: 2 değer = uçlar ince, 1 değer = sabit, **3+ değer** = vuruş boyunca ince-kalın dalga |
| `back` | `{ t = 1.0, back = [0.0, 0.5] }` | **tabletin KENDİ geri jesti** (kenardan içeri kaydırma; x ≤ 0,03 ya da ≥ 0,97) — kalkışta sayfaya `back` olayı, kenarda OS'un ok balonu |
| `keys` | `{ t = 2.0, keys = "⌘ Tab", dur = 1.2 }` | tuş çipi + `data-on-keys` |
| `type` | `{ t = 2.0, type = "Merhaba", rate = 14 }` | odaktaki alana harf harf yazar (yalnız HTML) |
| `rotate` | `{ t = 1.0, rotate = [0.5,0.5], by = 45 }` | iki parmak döndürme |

> ⚠️ **Tepkiyi sayfa çizer:** `swipe3` (Mission Control/masaüstü), `swipe4` (ekran klavyesi), `keytaps` ve `keys` için araç
> yalnız jesti/olayı sayfaya iletir ve daireleri/tuş çiplerini çizer; "Mission Control açıldı" ya da "klavye çıktı"
> görüntüsü sayfanın işidir (`promo.on('swipe', …)`, `promo.on('keyboard', …)`). Özgün projede bunu paylaşılan bir Mac
> masaüstü kabuğu yapıyordu; o kabuk bu pakette YOK, yani bu jestleri kullanacaksanız tepkiyi kendi sayfanıza yazın.
> `tap`, `scroll`, `drag`, `pinch`, `pen`, `type`, `back`, `smartzoom` için yerleşik davranışlar öznitelikle çalışır
> (aşağıda [HTML ekran sözleşmesi](#html-ekran-sözleşmesi)).

Ortak: `t` (zorunlu, sahne içi sn) · `dur` · `ease` (`linear in out inout sine outback quart`) · `tool = "pen"`
(`tap`/`double_tap`/`long_press`: kalemle dokunma = tık, daire küçük). `keytaps = "merhaba"` ekran klavyesinin
tuşlarına harf harf dokunur. Yazım hatası **hata** verir ve en yakın alanı önerir (`'scrol' → 'scroll' mi?`).
`[video] latency = 0.03` → HTML ekran dokunuşa 1-2 kare sonra tepki verir (gerçekçi; vars. 0).

**Dokunuş dairesi (varsayılan):** temasla belirir, parmağı izler, kalkınca söner; uzun basmada halka 0,4 sn'de yeşile
döner; kalem dairesi küçük; çok parmaklı jestte her parmağa bir daire. Mac imleci dokunuşlarda gösterilmez (bir sayfada
gerekirse `<body data-cursor="on">`). `[video] ripple = false` göstergeleri kapatır.

### Başlıklar, kamera, animasyon

**Başlıklar:** `caption` + `subcaption` tüm sahne boyunca; zamanlı için
`captions = [ { t = 0.5, d = 2.5, text = "…", sub = "…" } ]`. Başlık bandı her sahnede ayrılır (cihazlar sahneler
arasında zıplamaz) ve yüksekliği senaryonun EN UZUN sarılmış başlığından ölçülür. **Bant kuralı:** hiçbir cihazın
GÖRÜNEN gövdesi hiçbir anda başlık bandına girmez — kamera yakınlaşması gövdeyi oraya iterse içerik aşağı kaydırılır
(`TestShippedExamples` her örnek senaryoyu iki en-boyda denetler).

**Kamera:** her hareket `t`'de başlar, `dur` saniyede `zoom`a ve `at` noktasına (cihaz ekranında,
`on = "tablet"|"laptop"`) varır. `at` yoksa sahne merkezi.

**Cihaz animasyonu:** `anim = { tablet = [ { t = 0, on = 0 }, { t = 1.8, on = 1 } ], laptop = [ { t = 0.8, lid = 1 }, { t = 1.75, lid = 0 } ] }`
— `x`/`y` (ekran yüksekliği birimi), `s` ölçek, `o` saydamlık, `on` ekran açık (0 = kapalı), `lid` dizüstü kapağı
(0 = kapalı); **`rot`** derece (+ = saat yönü), cihaz merkezinde döner (daireler, kablo, ses dalgaları birlikte).
**`s = "fit"`** dönmüş cihazı içerik kutusuna sığdırır → aynı senaryo 9:16 ve 16:9'da doğru:
`anim = { tablet = [ { t = 1.6, rot = 0, s = 1 }, { t = 2.7, rot = -90, s = "fit" } ] }`. Bir karede yazılmayan özellik
önceki değerini korur.

**Bağlantı animasyonları:** `cable = { t = 0.55, dur = 0.65 }` (tabletin sağ kenarındaki USB-C yuvasına kablo; `out = true`:
çekilir) · `wifi = { t = 1.1, dur = 3 }` (cihazlar arasında nabız atan simge) · `sound = { t = 0.5, dur = 3 }` (tabletin
iki kısa kenarından yayılan ses dalgaları).

**Geçişler:** `auto` ortak cihazı yeni yerine kaydırır; kart ↔ cihaz arası çapraz geçiş. Geçiş kareleri normal karelerle
AYNI çizim yolundan geçer.

**Kare çıkarma:** sahnede `still = 3.2` (sn) yazarsanız `promo.py stills senaryo.toml [--format jpg --max-kb 1900]` her
`still`li sahneden bir kare yazar (PNG alfasız / JPG boyut tavanlı). Geçişe denk gelen an hata verir (kare iki sahnenin
karışımı olur).

### HTML ekran sözleşmesi

Sayfa başında `<meta name="promo-viewport" content="1400x920">` (CSS px; yoksa 1400×920). `promo.js` (jest oynatıcı)
sayfaya **araç tarafından** enjekte edilir — sayfaya eklemezsiniz. Davranışlar öznitelikle:

| öznitelik | davranış |
|---|---|
| `data-scroll` | tek parmakla kaydırılır (ilk çocuk taşınır; `"x"`/`"both"` seçenekleri) |
| `data-zoom="1,3"` | çimdikle ölçeklenir (min,max) |
| `canvas[data-ink]` | kalem mürekkebi (basınca göre kalınlık); `[data-ink-pick="#hex"]` renk seçer |
| `data-drag` | uzun bas + sürükle ile taşınır (`[data-drag-handle]` varsa yalnız oradan) |
| `data-tap-select="grup"` | dokununca `selected` sınıfı grupta buna geçer |
| `data-tap-toggle="sınıf"` | dokununca sınıfı aç/kapa · `data-tap-show="#id"` hedefe `promo-show` ekler |
| `data-on-keys="⌘ Tab"` | o kısayol süresince `promo-show` |
| `data-text-field` / `[data-type-target]` | `type` jestinin yazacağı alan |
| `.osk`, `[data-local]` | tabletin KENDİ arayüzü: dokunuş Mac tıkı değil, temas boyunca basılı görünür |
| `body[data-cursor="on"]` | Mac imlecini göster (dokunulan yere atlar) |

Özel davranış için `promo.on('tap'|'scroll'|'pinch'|'swipe'|'back'|'keys'|'frame', fn)`. Tam sözleşme:
`promokit/runtime/promo.js` başı.

⛔ **Sayfada `requestAnimationFrame` / `setTimeout` / `Date.now` ile zaman tutmayın** — her şey `promo.t`'den. Sayfa
gerçek zamanda ÇALIŞMAZ: sabit kare ızgarasında adım adım ilerler (`__promoSeekFrame(k)`; her adımda jestler → `frame` →
`apply`), doğrudan seek ile kare-kare oynatma aynı sonucu verir (paralel render parçalarının sınırında dikiş yok).
CSS animasyon/geçişleri serbesttir; araç onları kareye sabitler. Kaydırılan içerik kendi kompozitör katmanına
alınmamalı (alınınca Chromium ucuz bir raster tutup ekran görüntüsünü yük altında değiştirebiliyor).

**Dil** (`[video] lang`): her sayfanın `promo.params.lang`'ine iner; sayfa metnini buna göre kendisi seçer.

## 3) Render

```bash
python3 promo.py render S.toml [-o out.mp4] [--aspect 9:16] [--size 1440] [--fps 60] [-j 6]
                         [--codec h264|hevc|prores] [--crf 16] [--preview] [--from 5 --to 12]
python3 promo.py still  S.toml -t 3.2           # tek kare
python3 promo.py sheet  S.toml --every 0.5      # kontak sayfası (zamanlama kontrolü)
python3 promo.py info   S.toml                  # zaman çizelgesi
```

Çıktı yolu: `-o` > `[video] out` (senaryoya göreli; en-boy/önizleme soneki eklenir) > `work/out/`. Uzantı etkin
codec'ten gelir; ProRes `.mov`/`.mkv` ister (render BAŞLAMADAN denetlenir). Senaryo değerleri (easing adları, renkler,
tema, codec, fps, boyut, crf 0–51, kamera, başlıklar, süreler…) YÜKLEMEDE denetlenir — yazım hatası render ortasında
değil başta hata verir. Paralel render: zaman çizelgesi `-j` parçaya bölünür, her parça ayrı süreçte kodlanır ve
kayıpsız birleştirilir. Çıktı BT.709 etiketli.

## 4) Seslendirme, müzik, altyazı

```toml
[voice]
engine = "say"             # say (Mac, taslak) | chatterbox (yerel, ücretsiz) | google | elevenlabs
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

- **Motorlar:** `say` — Mac'in yerel sesi, kurulum yok, taslak için. `chatterbox` — yerel açık kaynak model, hesap/kart
  yok; **ayrı** Python ortamında (torch ağır): `python3 -m venv work/tts-venv && work/tts-venv/bin/pip install chatterbox-tts 'setuptools<81' faster-whisper`
  (modeller ilk koşuda iner; `faster-whisper` üretilen sesi yazıya döküp metinle doğrular). `google` / `elevenlabs` —
  bulut; anahtar `GOOGLE_TTS_API_KEY` / `ELEVENLABS_API_KEY` ortam değişkeninden ya da `~/.config/promokit/google-tts.key` /
  `elevenlabs.key` dosyasından (anahtar repoya GİRMEZ; metinler servise gönderilir).
- **Zamanlama:** satırlar üst üste binmez (gerekirse kayar); `voice` komutu geç başlayan ve video bitiminden taşan satırı
  söyler. Önbellek `work/voice/`: aynı metin+ayar yeniden üretilmez.
- **Doğrulama (Chatterbox):** her deneme yerel tanıyıcıyla yazıya dökülüp metinle karşılaştırılır (`asrcheck.py`);
  cümleden sonra tek sözcük bile kalırsa (uydurma kuyruk, tekrar) sonraki tohumla yeniden üretilir (4 deneme).
  4 denemede de tutmayan satır her koşuda uyarılır → metni ya da `say`i değiştirin.
- **Satır hızı:** `speed` (0,5–2) yalnız o satırı atempo ile yavaşlatır/hızlandırır, perde aynı. Ham üretim önbellekte
  kalır → hızı değiştirmek modeli yeniden koşturmaz. Ölçüt: hece/sn (yeni başlayan İngilizce dinleyici için ~4).
- **Okunuş sözlüğü:** `voice.DEFAULT_LEXICON` İngilizce kelimelerin **Türkçe seslendirmedeki** okunuşudur (Mac, Wi-Fi…);
  yalnız `lang = "tr"`de uygulanır; `[voice] lexicon` ezer/ekler. Kendi terimlerinizi senaryoya yazın.
- **Seviye:** her satır −20 LUFS'a, müzik onun ~10 dB altına ölçülerek oturur; konuşurken müzik ~8 dB daha kısılır.
  `music_volume` / `[voice] volume` bu dengenin çarpanı.
- **Müzik:** `[video] music = "bed"` o videoya ÖZEL fon üretir (yumuşak giriş → akış → kapanış kartında çözülen final;
  ruh hâli `music_mood`: bright · night · drive · warm · dreamy · uplift). Stdlib sentezi — telif sorusu yok. Gerçek
  parça için `--music dosya.wav`.

## 5) Platform notları

- **9:16 güvenli alan** (Reels/TikTok/Shorts arayüzü videonun ÜSTÜNE biner): başlık üstten **%13**'ün altında başlar,
  cihazlar alttan **%15**'in üstünde biter (`layout.SAFE_TALL`, otomatik). Tam ekran HTML sayfalar aynı çizgileri
  `promo.params.captionTop` / `safeBottom` ile alır. 4:5 / 1:1 / yatayda örtü yok. Telefonda okunurluk: 9:16'da
  başlık ×1,15, alt satır ×1,5 büyür.
- **Satır kırma:** sığmayan metin önce ` · ` ifade sınırından kırılır, sonra kelime kelime; tek başına ayraç
  (`/ · — – |`) satır başlatmaz. Kesin kırma için `\n`.
- **Altyazı yakma:** bazı platformlar SRT almaz → `promo.py burn` metni PIL ile PNG'ye çizip `overlay` ile bindirir
  (ffmpeg'de drawtext/libass olmayabilir).
- **Sessiz oto-oynatma** (LinkedIn vb.): başlıklar zaten görüntüye yakılı üretilir.

---

## Kod haritası

| Dosya | İş |
|---|---|
| `promo.py` | komut satırı (render, still, stills, sheet, info, voice, subs, burn, music, grid, capture, touches) |
| `promokit/story.py` | TOML ayrıştırma + doğrulama (yazım hatasında en yakın alanı önerir) |
| `promokit/render.py` | zaman çizelgesi, sahne çizimi, paralel render, kodlama, sahne kareleri |
| `promokit/layout.py` · `devices.py` · `cable.py` | cihaz yerleşimi, mockup'lar (tablet/MacBook/Windows dizüstü), kablo/Wi-Fi/ses animasyonu |
| `promokit/gestures.py` · `htmlscreen.py` · `runtime/promo.js` | jest listesi → temas yolları → sayfa oynatıcı (başsız tarayıcı) |
| `promokit/cdp.py` | başsız Chromium sürücüsü (yalnız stdlib CDP/WebSocket istemcisi) |
| `promokit/overlays.py` · `text.py` · `hand.py` · `photohand.py` | dokunuş daireleri, başlık, tuş çipleri, yazı, el stilleri |
| `promokit/capture.py` · `getevent.py` · `sync.py` · `touches.py` · `sck_record.swift` | Mac/tablet kaydı, `adb getevent` çözümü, senkron |
| `promokit/voice.py` · `tts_chatterbox.py` · `asrcheck.py` · `subs.py` · `burn.py` · `musicbed.py` | seslendirme, doğrulama, altyazı, fon müziği |
| `examples/` | örnek senaryo + HTML ekran |
| `tests/` | birim + tarayıcı testleri (`bash run-tests.sh`) |
| `assets/hands/` | kalem tutan fotoğraf el (yalnız `hand = "skin"` + kalem; Pexels #4087472, ticari kullanım serbest, atıf gerekmez) |

## Testler

```bash
bash run-tests.sh          # birim + tarayıcı testleri; atlanan test = hata
```

Testlerin çoğu tarayıcısız ve hızlıdır; HTML sayfa testleri başsız Chromium açar. macOS'ta Chromium'un code-sign klonu
sızıntısına karşı koruma `promokit/cdp.py` içindedir ve `run-tests.sh` koşu sonunda denetler.

## Bu paketten çıkarılanlar

Özgün projeye (Mac→Android ekran yansıtma uygulaması) özel olduğu için **bilerek yok**:

- Mağaza (Google Play / AppGallery) listeleme yükleyicileri, YouTube API yükleyicileri (başlık/açıklama/altyazı/çeviri/
  zamanlama), site görseli ve küçük resim/kapak/banner üreticileri.
- Marka girişi sahnesi (`type = "intro"`) + sesi, logo/wordmark çizimi, marka renk belgesine bağlı kapak stilleri.
- Ürünün kendi uygulama ekranları, ortak Mac/Windows masaüstü HTML kabuğu (`mac.js`/`win.js`), tüm
  özgün senaryolar ve video fikir notları, ürün kaynağını okuyan eşik-paritesi testleri, mutasyon ölçüm aracı.
- Ürünün sanal ekranını otomatik bulan `capture mac` varsayılanı (artık **ana ekran**).

Tarihçe: kod yorumlarında "kullanıcı, 2026-…" ile başlayan notlar özgün projenin ürün sahibinin kararlarıdır
(ör. el yerine daire, 9:16 güvenli alan oranları). Kendi kararlarınız farklıysa değiştirmekte serbestsiniz.
