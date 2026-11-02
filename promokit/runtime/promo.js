// promo.js — HTML ekran sayfalarının çalışma zamanı (tanıtım videosu aracı).
//
// Sayfa gerçek zamanda ÇALIŞMAZ: Python her kare için `__promoSeekFrame(k)` çağırır, ardından ekran
// görüntüsü alır. Durum SABİT bir kare ızgarasında ilerler — adım k = ((k−1)/fps, k/fps]. Bir kareye
// doğrudan atlamak da kare kare gelmek de AYNI adım dizisini koşar → paralel render parçalarının
// sınırında dikiş yok (kilit: tests/test_html.py `test_fresh_seek_equals_stepping`). Her adımda sırasıyla:
// jestler + tuş olayları + momentum → 'frame' olayı (sayfa koreografisi) → DOM'a yaz → yeni CSS animasyon/
// geçişlerini O ADIMIN zamanına sabitle ve hepsini o zamana sar. (Eskiden 'frame' + DOM yazımı yalnız hedef
// karede koşuyordu: doğrudan atlanan karede ⌘Tab paneli hiç görünmüyor, nabız/imleç fazı sıfırlanıyordu.)
// Momentum ve sınır kırpması yola bağlı olduğu için saf formül değil, sıralı adım.
// Aynı jest listesi Python tarafında dokunuş dairelerini de sürdüğünden sayfa ↔ daire senkronu yapısal.
//
// Jest anlamları README'deki «Jest anlamları» tablosuyla birebir. Eşikleri
// Python `classify` uygular; bu betik yalnız oynatır:
//   tek parmak: dokun = sol tık — Mac'e BIRAKIŞTA atomik gider (basılı görünüm yalnız o an) · sürükle =
//   kaydırma (12 dp eşiği aşıldıktan sonra; bırakınca momentum) · uzun bas = sağ tık (bırakışta; basılıyken
//   Mac hiçbir şey görmez) · uzun bas + sürükle = `drag` (sol tuş basılı)
//   iki parmak: sürükle = `pointer` (imleç) · dokun = `secondary` (çift-dokunma penceresi kadar GECİKMELİ) ·
//   çimdik = `pinch` (Mac native magnify kazancıyla) · çift dokunma = `smartzoom` (ikinci kalkışta; [data-zoom]
//   1 ↔ ×1,6 yumuşak geçiş)
//   üç parmak = `swipe` (eksen kilitli: Mission Control / App Exposé / Space) · dört parmak dikey = `keyboard`
//   (tabletin KENDİ ekran klavyesi; Mac görmez)
//   kalem: uç = sol tuş (tık/sürükle; temas boyunca basılı) — `canvas[data-ink]` üstünde mürekkep
//   tabletin KENDİ geri jesti (kenardan kaydırma, §4.3) = `back` (kalkışta; OS'un ok balonu kenarda çizilir) —
//   uygulamaya dokunuş olarak gelmez, kaydırma DEĞİL
// Sayfa sözleşmesi (yerleşik davranışlar — öznitelikle):
//   [data-scroll]            tek parmakla kaydırılır (ilk çocuk taşınır; "x"/"both" seçenekleri)
//   [data-zoom="1,3"]        çimdikle ölçeklenir (min,max)
//   canvas[data-ink]         kalem mürekkebi (basınca göre kalınlık); [data-ink-pick="#hex"] renk seçer
//   [data-drag]              uzun bas + sürükle ile taşınır (tutamak: [data-drag-handle] varsa yalnız oradan)
//   [data-tap-select="grup"] dokununca `selected` sınıfı grupta buna geçer
//   [data-tap-toggle="sınıf"] dokununca sınıfı aç/kapa · [data-tap-show="#id"] hedefe `promo-show` ekler
//   [data-on-keys="⌘ Tab"]   o kısayol süresince `promo-show` · [data-type-target] `type` jestini yazar
//   [data-text-field]        metin alanı gibi odak alır (içindeki [data-type-target]'a yazılır)
//   .osk, [data-local]       tabletin KENDİ arayüzü: dokunuş Mac tıkı değil, temas boyunca basılı görünür
//   body[data-cursor="on"]   Mac imlecini göster (dokunulan yere atlar). Varsayılan KAPALI: dokunuşu
//                            daire gösterir, imleç yanında fazlalıktı (kullanıcı, 2026-10-19)
// Dönüşüm farkındalığı: kaydırma/çimdik/sürükleme, görüntü alanındaki parmak hareketini öğenin YEREL
// eksenlerine çevirir (ata zincirindeki transform/rotate/scale) → döndürülmüş bir kapta da içerik parmağı
// izler. Dönüşümsüz sayfada matris birimdir ve ham delta aynen geçer (eski davranış bayt-birebir).
// Özel davranış: promo.on('tap'|'longpress'|'secondary'|'drag'|'scroll'|'pointer'|'pinch'|'smartzoom'|'swipe'|
//   'keyboard'|'pen'|'back'|'keys'|'frame', fn)
// ⛔ Sayfada requestAnimationFrame / setTimeout / Date.now ile ZAMAN tutmayın: her şey promo.t'den.
// CSS animasyon + geçişleri bu betik zamana sabitler (Web Animations API) — serbestçe kullanılabilir.
(() => {
  if (window.promo) return;
  const promo = window.promo = { t: 0, fps: 60, W: 0, H: 0, _h: {}, state: {} };
  promo.on = (type, fn) => { (promo._h[type] = promo._h[type] || []).push(fn); return promo; };
  const emit = (type, ev) => {
    for (const f of promo._h[type] || []) f(ev);
    if (ev && ev.target && ev.target.dispatchEvent) {
      ev.target.dispatchEvent(new CustomEvent('promo:' + type, { bubbles: true, detail: ev }));
    }
  };

  // ── yol örnekleme ──
  const at = (path, t) => {               // path: [[t, x, y, ...], ...] → doğrusal ara değer
    if (t <= path[0][0]) return path[0];
    const n = path.length;
    if (t >= path[n - 1][0]) return path[n - 1];
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (path[m][0] <= t) lo = m; else hi = m; }
    const a = path[lo], b = path[hi], k = (t - a[0]) / ((b[0] - a[0]) || 1);
    return a.map((v, i) => v + (b[i] - v) * k);
  };
  const hit = (x, y) => document.elementFromPoint(Math.max(0, Math.min(promo.W - 1, x)), Math.max(0, Math.min(promo.H - 1, y)));
  const closest = (x, y, sel) => { const e = hit(x, y); return e ? e.closest(sel) : null; };
  const pressable = (el) => el && (el.closest('[data-tap-select],[data-tap-toggle],[data-tap-show],[data-ink-pick],button,a,.tap,[data-tap]') || el);
  const isLocal = (el) => !!(el && el.closest && el.closest('.osk,[data-local]'));
  // Mac'in akıllı yakınlaştırması dokunulan sütunu ekrana sığdırır (tarayıcıdaki 760 px makale / 1280 px pencere ≈ ×1,6);
  // geçiş Mac'teki gibi yumuşak (kübik yavaşlama)
  const SMART_ZOOM = 1.6, SMART_ZOOM_S = 0.35;
  // Tabletin geri jesti: OS'un ok balonu parmak bu kadar (genişliğin payı) çekilince tam boyda — gestures.BACK_REACH
  const BACK_REACH = 0.11, BACK_FADE_S = 0.22;

  // ── dönüşüm farkındalığı ──
  // Öğenin ÇOCUK koordinatlarındaki bir vektörün görüntü alanındaki karşılığı = kökten öğeye zincirdeki her
  // `transform` + bağımsız `rotate`/`scale` özelliğinin DOĞRUSAL kısmının çarpımı (öteleme vektörü etkilemez).
  // null = birim (dönüşümsüz yol).
  function linearTo(el) {
    let m = null;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      let l = null;
      const mul = (x) => { l = l ? l.multiply(x) : x; };   // öğe dönüşümü = rotate · scale · transform
      if (cs.rotate && cs.rotate !== 'none') {
        const p = cs.rotate.trim().split(/\s+/);
        if (p.length === 1 || /^(z|0 0 1)$/.test(p.slice(0, -1).join(' '))) mul(new DOMMatrix().rotate(parseFloat(p[p.length - 1])));
      }
      if (cs.scale && cs.scale !== 'none') {
        const p = cs.scale.trim().split(/\s+/).map(parseFloat);
        mul(new DOMMatrix().scale(p[0], p.length > 1 ? p[1] : p[0]));
      }
      if (cs.transform && cs.transform !== 'none') mul(new DOMMatrix(cs.transform));
      if (l) m = m ? l.multiply(m) : l;
    }
    if (!m || (Math.abs(m.a - 1) < 1e-12 && Math.abs(m.b) < 1e-12 && Math.abs(m.c) < 1e-12 && Math.abs(m.d - 1) < 1e-12)) return null;
    return m;
  }
  // Görüntü alanındaki (dx, dy) → `el`in çocuk koordinatları (kaydırma içeriği el'in çocuğudur).
  function toLocal(el, dx, dy) {
    const m = el ? linearTo(el) : null;
    if (!m) return [dx, dy];
    const det = m.a * m.d - m.b * m.c;
    if (Math.abs(det) < 1e-12) return [dx, dy];
    return [(m.d * dx - m.c * dy) / det, (-m.b * dx + m.a * dy) / det];
  }
  // Görüntü alanındaki nokta → öğenin YEREL kutusunda yüzde (çimdik merkezi = transform-origin).
  function originAt(el, x, y) {
    const r = el.getBoundingClientRect();
    if (!linearTo(el) || !(el instanceof HTMLElement) || /^(CANVAS|IMG|VIDEO)$/.test(el.tagName)) {
      return [(x - r.left) / r.width * 100, (y - r.top) / r.height * 100];   // dönüşümsüz: kutudan doğrudan
    }
    // Döndürülmüş zincir: üç sonda (yerel 0,0 · 100,0 · 0,100) → afin eşleme → tersi
    const pos = getComputedStyle(el).position, keep = el.style.position;
    if (pos === 'static') el.style.position = 'relative';
    const probe = (lx, ly) => {
      const d = document.createElement('div');
      d.style.cssText = `position:absolute;left:${lx - el.clientLeft}px;top:${ly - el.clientTop}px;width:0;height:0;margin:0;padding:0;border:0`;
      el.appendChild(d);
      const q = d.getBoundingClientRect();
      d.remove();
      return [q.left, q.top];
    };
    const o = probe(0, 0), px = probe(100, 0), py = probe(0, 100);
    if (pos === 'static') el.style.position = keep;
    const ax = (px[0] - o[0]) / 100, ay = (px[1] - o[1]) / 100, bx = (py[0] - o[0]) / 100, by = (py[1] - o[1]) / 100;
    const det = ax * by - bx * ay, vx = x - o[0], vy = y - o[1];
    return [(by * vx - bx * vy) / det / el.offsetWidth * 100, (-ay * vx + ax * vy) / det / el.offsetHeight * 100];
  }

  // ── durum ──
  let S = null;
  const born = new WeakMap();
  // Bu adımda doğan animasyon/geçişler O ADIMIN zamanına sabitlenir; hepsi t'ye sarılır (adım başına).
  function scrub(t) {
    for (const a of document.getAnimations()) {
      if (!born.has(a)) born.set(a, t);
      a.pause();
      a.currentTime = Math.max(0, (t - born.get(a)) * 1000);
    }
  }

  function scrollState(el) {
    let s = S.scroll.get(el);
    if (!s) {
      const mode = el.dataset.scroll || 'y';
      s = { x: 0, y: 0, vx: 0, vy: 0, mode, active: false, lastMove: -9, hist: [] };
      S.scroll.set(el, s);
      if (!el.querySelector(':scope > .promo-scrollbar')) {
        const bar = document.createElement('div');
        bar.className = 'promo-scrollbar';
        el.appendChild(bar);
      }
    }
    return s;
  }
  function scrollLimits(el) {
    const c = el.firstElementChild;
    const maxY = Math.max(0, c.scrollHeight - el.clientHeight);
    const maxX = Math.max(0, c.scrollWidth - el.clientWidth);
    return { maxX, maxY };
  }
  function zoomState(el) {
    let z = S.zoom.get(el);
    if (!z) {
      const [mn, mx] = (el.dataset.zoom || '1,3').split(',').map(Number);
      z = { s: 1, min: mn || 1, max: mx || 3, ox: 50, oy: 50, base: 1 };
      S.zoom.set(el, z);
    }
    return z;
  }
  function moveCursor(x, y, t) { S.cursor.x = x; S.cursor.y = y; S.cursor.t = t; S.cursor.shown = true; }

  function press(el, t0, dur) { if (el) S.pressed.push({ el, t0, t1: t0 + dur }); }
  function tapActions(el, t, g) {
    if (!el) return;
    const sel = el.closest('[data-tap-select]');
    if (sel) {
      const grp = sel.dataset.tapSelect;
      document.querySelectorAll(`[data-tap-select="${grp}"]`).forEach(e => e.classList.toggle('selected', e === sel));
    }
    const tog = el.closest('[data-tap-toggle]');
    if (tog) {   // `promo-toggled`: dokunarak değişti (CSS yalnız bunda animasyon oynatsın, sahne başında değil)
      tog.classList.toggle(tog.dataset.tapToggle);
      tog.classList.add('promo-toggled');
    }
    const show = el.closest('[data-tap-show]');
    if (show) document.querySelectorAll(show.dataset.tapShow).forEach(e => e.classList.add('promo-show'));
    const hide = el.closest('[data-tap-hide]');
    if (hide) document.querySelectorAll(hide.dataset.tapHide).forEach(e => e.classList.remove('promo-show'));
    const pick = el.closest('[data-ink-pick]');
    if (pick) {
      S.inkColor = pick.dataset.inkPick;
      document.querySelectorAll('[data-ink-pick]').forEach(e => e.classList.toggle('selected', e === pick));
    }
    const field = el.closest('[data-type-target], input, textarea, [contenteditable], [data-text-field]');
    if (field) S.focus = (field.matches('[data-text-field]') && field.querySelector('[data-type-target]')) || field;
  }

  // ── jest adımları ──
  function stepGesture(g, t0, t1) {
    const began = g.t0 > t0 && g.t0 <= t1;
    const ended = g.t1 > t0 && g.t1 <= t1;
    const ta = Math.max(t0, g.t0), tb = Math.min(t1, g.t1);
    const p0 = at(g.path, g.t0);
    // Aynı adımda hem başlayıp hem biten jest önce 'begin', sonra 'end' alır (bitişi bekleyen işleyici kaçırmasın).
    // Artımlı deltalar (kaydırma/sürükleme) yalnız ilk olayda taşınır.
    const phases = began && ended ? ['begin', 'end'] : [began ? 'begin' : ended ? 'end' : 'move'];
    const lp = g.ta != null && g.ta > t0 && g.ta <= t1;   // uzun bas: kol "hazır" (halka yeşil) bu adımda
    switch (g.type) {
      case 'tap': case 'secondary': {
        if (began) {
          moveCursor(p0[1], p0[2], g.t0);
          g._el = hit(p0[1], p0[2]);
          if (g.type === 'tap') {
            // Parmak dokunuşu Mac'e BIRAKIŞTA atomik tık (OP_CLICK: warp+down+up) → basılı görünüm yalnız o an.
            // Kalem ucu sol tuşu temasla basar; tabletin KENDİ arayüzü (ekran klavyesi) temas boyunca basılıdır.
            if (g.tool === 'pen' || g.local || isLocal(g._el)) press(pressable(g._el), g.t0, g.t1 - g.t0 + 0.05);
            else press(pressable(g._el), g.t1, 0.1);
            if (g.tool === 'pen') { const cv = closest(p0[1], p0[2], 'canvas[data-ink]'); if (cv) inkDot(cv, p0); }
          }
        }
        if (lp && g.type === 'secondary') emit('longpress', { t: g.ta, x: p0[1], y: p0[2], target: g._el, g });
        const fire = g.tf != null ? g.tf : g.t1;    // iki parmak sağ tık: çift-dokunma penceresi kadar gecikmeli
        if (fire > t0 && fire <= t1) {
          if (g.type === 'tap') tapActions(g._el, fire, g);
          emit(g.type, { t: fire, x: p0[1], y: p0[2], target: g._el, double: !!g.double, local: !!g.local, g });
        }
        break;
      }
      case 'drag': {   // GRAB → sol tuş g.tb'de İNİŞ noktasında basılır; nesne parmağın inişten beri yolunu izler
        if (began) {
          moveCursor(p0[1], p0[2], g.t0);
          const d = closest(p0[1], p0[2], '[data-drag]');
          const h = d && d.querySelector('[data-drag-handle]');
          g._drag = d && (!h || h.contains(hit(p0[1], p0[2]))) ? d : null;
          g._el = hit(p0[1], p0[2]);
          if (g._drag) {
            if (!S.drag.has(g._drag)) S.drag.set(g._drag, { x: 0, y: 0 });
            const s = S.drag.get(g._drag);
            g._base = [s.x, s.y];
          }
        }
        if (lp) emit('longpress', { t: g.ta, x: p0[1], y: p0[2], target: g._el, g });
        if (tb >= ta) {
          const a = at(g.path, ta), b = at(g.path, tb);
          moveCursor(b[1], b[2], tb);
          if (g._drag && tb > g.tb) {
            const s = S.drag.get(g._drag);
            const [lx, ly] = toLocal(g._drag.parentElement, b[1] - p0[1], b[2] - p0[2]);   // translate ebeveyn ekseninde
            s.x = g._base[0] + lx; s.y = g._base[1] + ly;
          }
          phases.forEach((ph, i) => emit('drag', { t: tb, x: b[1], y: b[2], dx: i ? 0 : b[1] - a[1], dy: i ? 0 : b[2] - a[2],
                                                   phase: ph, target: g._el, g }));
        }
        break;
      }
      case 'pointer': {   // iki parmak sürükle: imleç merkeze ışınlanır ve izler — tık yok
        if (tb >= ta) {
          const b = at(g.path, tb);
          moveCursor(b[1], b[2], tb);
          for (const ph of phases) emit('pointer', { t: tb, x: b[1], y: b[2], phase: ph, target: hit(b[1], b[2]), g });
        }
        break;
      }
      case 'scroll': {
        if (began) {
          moveCursor(p0[1], p0[2], g.t0);
          g._el = closest(p0[1], p0[2], '[data-scroll]');
        }
        if (tb >= ta) {
          const b = at(g.path, tb);
          moveCursor(b[1], b[2], tb);   // üründe imleç kaydıran parmağı İZLER
          // İçerik ancak 12 dp eşiği aşılınca kayar (ürün: eşiğe kadarki yol Mac'e gitmez)
          const sa = Math.max(ta, g.ts != null ? g.ts : g.t0);
          let dx = 0, dy = 0;
          if (tb > sa) { const a = at(g.path, sa); dx = b[1] - a[1]; dy = b[2] - a[2]; }
          if (g._el && tb > sa) applyScroll(g._el, dx, dy, tb);
          phases.forEach((ph, i) => emit('scroll', { t: tb, dx: i ? 0 : dx, dy: i ? 0 : dy, phase: ph, target: g._el, g }));
        }
        if (ended && g._el) release(g._el, g, g.t1);
        break;
      }
      case 'pinch': {
        if (began) {
          moveCursor(p0[1], p0[2], g.t0);
          g._el = closest(p0[1], p0[2], '[data-zoom]');
          if (g._el) {
            const z = zoomState(g._el);
            if (Math.abs(z.s - 1) < 1e-3) { const o = originAt(g._el, p0[1], p0[2]); z.ox = o[0]; z.oy = o[1]; }
            z.base = z.s;
          }
        }
        if (tb >= ta) {
          const b = at(g.path, tb);
          // Mac native magnify: kol kurulana dek (yarıçap 6 dp) hiçbir şey gitmez; sonra ölçek ×exp(k·Δaralık)
          if (g._el && g.ta != null && tb >= g.ta) {
            const z = zoomState(g._el);
            z.s = Math.min(z.max, Math.max(z.min, z.base * Math.exp(g.k * (b[3] - g.da))));
          }
          for (const ph of phases) emit('pinch', { t: tb, scale: b[3] / (p0[3] || 1), x: b[1], y: b[2], phase: ph, target: g._el, g });
        }
        break;
      }
      case 'swipe': {   // 3 parmak: eksen kilidinden sonra Dock jesti · 4 parmak: klavye (`keyboard` olayı)
        if (tb >= ta) {
          const b = at(g.path, tb), c0 = g.c0 || [p0[1], p0[2]];
          for (const ph of phases) {
            emit('swipe', { t: tb, fingers: g.fingers, dx: b[1] - c0[0], dy: b[2] - c0[1], phase: ph,
                            axis: g.axis || null, locked: !!g.axis && tb >= g.tl, cancel: !!g.cancel, target: document.body, g });
          }
        }
        if (g.kb && g.kb.t > t0 && g.kb.t <= t1) emit('keyboard', { t: g.kb.t, show: g.kb.show, target: document.body, g });
        break;
      }
      case 'smartzoom': {   // iki parmak çift dokunma: Mac'in akıllı yakınlaştırması — [data-zoom] 1 ↔ SMART_ZOOM
        const fire = g.tf != null ? g.tf : g.t1;
        if (fire > t0 && fire <= t1) {
          const el = closest(p0[1], p0[2], '[data-zoom]');
          if (el) {
            const z = zoomState(el);
            const to = z.s > 1.05 ? 1 : Math.min(z.max, SMART_ZOOM);
            if (to > 1) { const o = originAt(el, p0[1], p0[2]); z.ox = o[0]; z.oy = o[1]; }
            z.anim = { t0: fire, from: z.s, to };
          }
          emit('smartzoom', { t: fire, x: p0[1], y: p0[2], target: el || hit(p0[1], p0[2]), g });
        }
        break;
      }
      case 'back': {   // tabletin KENDİ geri jesti: OS'un ok balonu kenarda büyür; kalkışta Mac'e sayfa geri
        if (tb >= ta) {
          const b = at(g.path, tb);
          S.back = { side: g.side, y: b[2], d: Math.abs(b[1] - p0[1]), t1: null };
        }
        if (ended) {
          S.back.t1 = g.t1;
          emit('back', { t: g.t1, side: g.side, target: document.body, g });
        }
        break;
      }
      case 'pen': {   // kalem sürüklemesi (sol tuş basılı): tuval üstünde mürekkep
        if (began) {
          g._canvas = closest(p0[1], p0[2], 'canvas[data-ink]');
          g._last = null;
          g._color = S.inkColor || (g._canvas && g._canvas.dataset.ink) || '#1d4ed8';
        }
        if (tb >= ta) {
          const b = at(g.path, tb);
          moveCursor(b[1], b[2], tb);
          if (g._canvas) drawInk(g, ta, tb);
          for (const ph of phases) emit('pen', { t: tb, x: b[1], y: b[2], p: b[3], phase: ph, target: g._canvas, g });
        }
        break;
      }
    }
  }

  function applyScroll(el, dx, dy, t) {
    const s = scrollState(el), lim = scrollLimits(el);
    [dx, dy] = toLocal(el, dx, dy);   // görüntü alanı → kabın yerel ekseni (döndürülmüş kapta içerik parmağı izler)
    if (s.mode !== 'x') s.y = Math.min(lim.maxY, Math.max(0, s.y - dy));
    if (s.mode === 'x' || s.mode === 'both') s.x = Math.min(lim.maxX, Math.max(0, s.x - dx));
    s.vx = s.vy = 0;
    s.active = true;
    s.lastMove = t;
  }
  function release(el, g, t1) {
    const s = scrollState(el);
    const w = Math.max(1e-3, Math.min(0.08, t1 - (g.ts != null ? g.ts : g.t0)));
    const a = at(g.path, t1 - w), b = at(g.path, t1);
    const [lx, ly] = toLocal(el, b[1] - a[1], b[2] - a[2]);
    s.vy = -ly / w;
    s.vx = -lx / w;
    s.active = false;
  }
  function momentum(dt, t) {
    for (const [el, s] of S.scroll) {
      if (s.active || (Math.abs(s.vx) < 4 && Math.abs(s.vy) < 4)) continue;
      const lim = scrollLimits(el);
      if (s.mode !== 'x') s.y = Math.min(lim.maxY, Math.max(0, s.y + s.vy * dt));
      if (s.mode === 'x' || s.mode === 'both') s.x = Math.min(lim.maxX, Math.max(0, s.x + s.vx * dt));
      const k = Math.exp(-dt / 0.32);
      s.vx *= k; s.vy *= k;
      s.lastMove = t;
    }
  }

  function inkCtx(canvas) {
    let c = S.ink.get(canvas);
    if (!c) {
      const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(r.width * dpr);
      canvas.height = Math.round(r.height * dpr);
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      c = { ctx, r };
      S.ink.set(canvas, c);
    }
    return c;
  }
  // basınç → kalınlık: hafif basınç kılcal (0,15·taban), tam basınç 1,25·taban — fırça kalemi gibi GÖRÜNÜR
  // fark. 0,35 tabanı [0.05, 1] ile bile ~1/3'te kalıyordu → videoda tek kalınlık (inceleme, 2026-10-20)
  const inkWidth = (base, p) => base * (0.15 + 1.1 * (p == null ? 1 : p));
  function drawInk(g, ta, tb) {
    const { ctx, r } = inkCtx(g._canvas);
    const base = parseFloat(g._canvas.dataset.inkWidth || '3.2');
    const pts = g.path.filter(p => p[0] > ta && p[0] < tb).concat([at(g.path, tb)]);
    if (!g._last) g._last = at(g.path, g.t0);
    ctx.strokeStyle = g._color;
    for (const p of pts) {
      const q = g._last;
      ctx.lineWidth = inkWidth(base, p[3]);
      ctx.beginPath();
      ctx.moveTo(q[1] - r.left, q[2] - r.top);
      ctx.lineTo(p[1] - r.left, p[2] - r.top);
      ctx.stroke();
      g._last = p;
    }
  }
  function inkDot(canvas, p) {   // kalemle tuvale dokunma: çizim uygulaması uç değdiği yere nokta koyar
    const { ctx, r } = inkCtx(canvas);
    const base = parseFloat(canvas.dataset.inkWidth || '3.2');
    ctx.fillStyle = S.inkColor || canvas.dataset.ink || '#1d4ed8';
    ctx.beginPath();
    ctx.arc(p[1] - r.left, p[2] - r.top, inkWidth(base, p[3]) / 2, 0, Math.PI * 2);
    ctx.fill();
  }

  // ── DOM'a yaz ──
  function apply(t) {
    for (const [el, s] of S.scroll) {
      el.firstElementChild.style.transform = `translate(${-s.x}px, ${-s.y}px)`;
      const bar = el.querySelector(':scope > .promo-scrollbar');
      if (bar) {
        const lim = scrollLimits(el), h = el.clientHeight;
        const th = Math.max(28, h * h / (h + lim.maxY));
        bar.style.height = th + 'px';
        bar.style.transform = `translateY(${lim.maxY ? (h - th - 6) * s.y / lim.maxY + 3 : 3}px)`;
        const idle = t - s.lastMove;
        bar.style.opacity = idle < 0.6 ? 1 : Math.max(0, 1 - (idle - 0.6) / 0.35);
      }
    }
    for (const [el, z] of S.zoom) {
      el.style.transformOrigin = `${z.ox}% ${z.oy}%`;
      el.style.transform = `scale(${z.s})`;
    }
    for (const [el, d] of S.drag) el.style.translate = `${d.x}px ${d.y}px`;
    const pressedOn = new Map();
    for (const p of S.pressed) pressedOn.set(p.el, (pressedOn.get(p.el) || false) || (t >= p.t0 && t <= p.t1));
    for (const [el, on] of pressedOn) el.classList.toggle('promo-pressed', on);
    for (const k of S.keys) {
      const on = t >= k.t && t <= k.t + k.dur;
      document.querySelectorAll('[data-on-keys]').forEach(e => {
        if (e.dataset.onKeys === k.label) {
          if (on) e.classList.add('promo-show');
          else if (t > k.t + k.dur && k._shown) e.classList.remove('promo-show');
        }
      });
      if (on) k._shown = true;
    }
    for (const ty of S.texts) {
      if (t < ty.t) continue;
      const tgt = ty._el || (ty._el = S.focus || document.querySelector('[data-type-target]'));
      if (!tgt) continue;
      if (ty._base === undefined) ty._base = tgt.value !== undefined && tgt.tagName !== 'DIV' ? tgt.value : tgt.textContent;
      const n = Math.max(0, Math.min(ty.text.length, Math.floor((t - ty.t) * ty.rate)));
      const txt = ty._base + ty.text.slice(0, n);
      if (tgt.value !== undefined && tgt.tagName !== 'DIV') tgt.value = txt; else tgt.textContent = txt;
      tgt.classList.toggle('promo-typing', n < ty.text.length);
    }
    const c = S.cursorEl;
    if (c) {
      c.style.transform = `translate(${S.cursor.x}px, ${S.cursor.y}px)`;
      c.style.opacity = S.cursor.shown ? 1 : 0;
    }
    const bk = S.back;   // geri jestinin ok balonu: kenara yapışık yarım hap, parmak çekildikçe uzar; kalkınca söner
    if (bk) {
      let el = S.backEl;
      if (!el) {
        el = S.backEl = document.createElement('div');
        el.className = 'promo-back';
        el.innerHTML = '<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7" fill="none" stroke="#fff" stroke-width="3"'
          + ' stroke-linecap="round" stroke-linejoin="round"/></svg>';
        document.body.appendChild(el);
      }
      const reach = Math.min(1, bk.d / (promo.W * BACK_REACH));
      const fade = bk.t1 == null ? 1 : Math.max(0, 1 - (t - bk.t1) / BACK_FADE_S);
      const w = 16 + 40 * reach;
      el.classList.toggle('right', bk.side === 'right');
      el.style.width = w + 'px';
      el.style.top = (bk.y - 70) + 'px';
      el.style.left = (bk.side === 'right' ? promo.W - w : 0) + 'px';
      el.style.opacity = reach > 0.02 ? fade : 0;
      el.firstChild.style.opacity = Math.min(1, reach * 1.6);
    }
  }

  function step(t0, t1) {
    for (const g of S.gestures) {
      if (g.t0 > t1 || g.te <= t0) continue;   // bitmiş jest bir SONRAKİ adımda yeniden işlenmez (sınır kapalı)
      stepGesture(g, t0, t1);
    }
    for (const k of S.keys) {
      if (k.t > t0 && k.t <= t1) emit('keys', { t: k.t, label: k.label, dur: k.dur, target: document.body });
    }
    momentum(t1 - t0, t1);
    for (const z of S.zoom.values()) {   // akıllı yakınlaştırma geçişi (zamanın saf fonksiyonu)
      if (!z.anim) continue;
      const k = Math.min(1, Math.max(0, (t1 - z.anim.t0) / SMART_ZOOM_S));
      z.s = z.anim.from + (z.anim.to - z.anim.from) * (1 - Math.pow(1 - k, 3));
      if (k >= 1) z.anim = null;
    }
  }
  // Izgaranın k. adımı: ((k−1)/fps, k/fps] — zaman TAMSAYIDAN türetilir, geliş yolundan bağımsız.
  function advance(k) {
    const t0 = (k - 1) / promo.fps, t1 = k / promo.fps;
    promo.t = t1;
    step(t0, t1);
    emit('frame', { t: t1 });
    apply(t1);
    scrub(t1);
    S.k = k;
    S.t = t1;
  }

  const BASE_CSS = `
    [data-scroll]{position:relative;overflow:hidden}
    .promo-scrollbar{position:absolute;top:0;right:3px;width:7px;border-radius:4px;background:rgba(120,120,128,.55);
      opacity:0;pointer-events:none;z-index:50}
    .promo-back{position:fixed;height:140px;z-index:2147483646;pointer-events:none;opacity:0;display:grid;place-items:center;
      background:rgba(24,24,30,.6);border-radius:0 70px 70px 0;box-shadow:0 2px 14px rgba(0,0,0,.25)}
    .promo-back.right{border-radius:70px 0 0 70px}
    .promo-back svg{width:24px;height:24px}
    .promo-back.right svg{transform:scaleX(-1)}
    `;  // ⚠️ [data-zoom]'a will-change KOYMA: katman ilk ölçekte rasterleşir, yakınlaşınca bulanıklaşır (ölçüldü)
  // ⚠️ Kaydırma içeriği de AYRI katmana alınmaz (will-change yok, 2B translate): her karede dönüşümü değişen
  // katmanı Chromium "canlı" sayıp ucuz raster'la bırakabiliyor → aynı DOM durumunda iki ekran görüntüsü yük
  // altında FARKLI çıkıyordu (ölçüldü: 6 paralel süitte 4/18 dikiş testi, hep aynı fark 0,6768; katmansız 18/18).
  // Her kare yeniden boyanır → görüntü yalnız duruma bağlı.

  window.__promoInit = (data) => {
    const st = document.createElement('style');
    st.textContent = BASE_CSS;
    document.head.prepend(st);
    promo.fps = data.fps || 60;
    promo.params = data.params || {};
    promo.W = window.innerWidth;
    promo.H = window.innerHeight;
    const gestures = (data.gestures || []).slice().sort((a, b) => a.t0 - b.t0);
    for (const g of gestures) g.te = Math.max(g.t1, g.tf != null ? g.tf : g.t1);   // son işleneceği an
    S = {
      k: 0, t: 0, gestures, keys: data.keys || [],
      texts: data.texts || [], scroll: new Map(), zoom: new Map(), drag: new Map(), ink: new Map(),
      pressed: [], cursor: { x: promo.W * 0.62, y: promo.H * 0.55, t: 0, shown: false }, focus: null, inkColor: null,
      back: null, backEl: null,
    };
    promo.state = S;
    document.querySelectorAll('[data-scroll]').forEach(scrollState);
    // Mac imleci: sayfa (`body[data-cursor="on"]`) ya da sahne (`params.cursor = true`, ör. iki parmakla imleç) açar
    if (data.cursor === true || promo.params.cursor === true || document.body.dataset.cursor === 'on') {
      const c = document.createElement('div');
      c.id = 'promo-cursor';
      c.innerHTML = '<svg width="26" height="34" viewBox="0 0 26 34"><path d="M2 2 L2 27 L8.2 21.2 L12.4 31 L17 29 L12.8 19.4 L21.5 19.4 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>';
      c.style.cssText = 'position:fixed;left:-2px;top:-2px;z-index:2147483647;pointer-events:none;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.35));opacity:0';
      document.body.appendChild(c);
      S.cursorEl = c;
      if (data.cursor_start) { S.cursor.x = data.cursor_start[0]; S.cursor.y = data.cursor_start[1]; S.cursor.shown = true; }
    }
    emit('init', { data });
    promo.t = 0;
    step(-1e-6, 0);           // tam t=0'da başlayan jestler
    emit('frame', { t: 0 });
    apply(0);
    scrub(0);
    return true;
  };

  window.__promoSeekFrame = (k) => {
    if (!S) throw new Error('__promoInit çağrılmadı');
    k = Math.max(0, Math.floor(k));
    while (S.k < k) advance(S.k + 1);
    return S.k;
  };
  window.__promoSeek = (t) => window.__promoSeekFrame(Math.floor(t * promo.fps + 1e-3));
})();
