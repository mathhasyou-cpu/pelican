/* АС ПЕЛИКАН — радар. Пояса — стадии зрелости: в центре «раннее внедрение» (ближе всего
   к рынку), у края «концепция» (самое слабое и самое многочисленное — ему самый просторный
   пояс), сектора — направления запроса, за кругом — снятое. Сетка и подписи — SVG, точки —
   HTML поверх (CSS-переходы, номер внутри, фокус, клик, подсказка). Одна геометрия
   (chrome + positions) рисует и экран, и печать (toSVG). Модуль императивный: React-обёртка —
   components/Radar.jsx. Текст из отчёта попадает в разметку только через esc(). */
import { stageIndex } from "./stages.js";

  var RING_R = [12, 22, 32, 42];               // внешние границы поясов, от центра; площади растут к краю, как и население стадий
  var RING_SHORT = ["концепция", "прототип", "пилот", "раннее внедрение"];   // по индексу стадии (stages.STAGES)
  var BAND_LABEL = ["раннее внедрение", "пилот", "прототип", "концепция"];   // по индексу пояса, от центра
  function bandOf(stage) { return 3 - stage; }
  var CORE_R = 3;                              // центры точек центрального пояса не ближе; подпись в центре обходится прямоугольником (clampCell)
  var OUT_R = 44.5, OUTER_R = 47, FLABEL_R = 50.5;
  var VB = { x: -24, y: -2, w: 148, h: 104 };  // центр (50, 50) — в центре коробки; по бокам место подписям секторов
  var SECTOR_START = -90;                      // верхняя спица — полоса подписей колец, точек рядом нет
  var NOMINAL_PX = 760;                        // ширина радара, для которой заданы размеры точек
  var LETTERS = "АБВГДЕЖЗИКЛМ";

  function polar(deg, r) { var a = deg * Math.PI / 180; return { x: 50 + r * Math.cos(a), y: 50 + r * Math.sin(a) }; }
  function toDeg(rad) { return rad * 180 / Math.PI; }
  function near(a, b) { return Math.abs(a - b) < 0.01; }
  function rnd(i) { var x = Math.sin(i * 9301 + 49297) * 233280; return x - Math.floor(x); }
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function f2(n) { return String(Math.round(n * 100) / 100); }
  function px(x) { return ((x - VB.x) / VB.w * 100).toFixed(3) + "%"; }
  function py(y) { return ((y - VB.y) / VB.h * 100).toFixed(3) + "%"; }
  function clamp(lo, x, hi) { return Math.max(lo, Math.min(hi, x)); }
  function noRefs(s) { return String(s || "").replace(/\s*(\[\d+\])+/g, ""); }
  function plural(n, one, few, many) { var a = n % 100, b = n % 10; if (a > 10 && a < 20) return many; if (b > 1 && b < 5) return few; if (b === 1) return one; return many; }
  function confClass(c) { return c >= 0.75 ? "hi" : c >= 0.5 ? "mid" : "lo"; }
  /* Диаметр точки в px при масштабе k: топ — по числу источников, вне топа — мелкая, снятое — точка. */
  function sizePx(d, k) {
    k = k || 1;
    if (d.state === "out" || d.state === "pool") return 5;
    if (!d.rank) return Math.round(11 * Math.max(k, 0.8));
    return Math.round((d.sources >= 6 ? 34 : d.sources >= 4 ? 26 : 21) * k);
  }
  function laneW(k, rl) { return BAND_LABEL[k].length * 0.6 * rl / 2 + 0.4; }
  /* Средняя ширина буквы подписи в долях кегля. ⚠️ Оценка, не замер шрифта: с запасом, чтобы
     подпись скорее перенеслась лишний раз, чем вылезла за край радара. */
  var CHAR_EM = 0.6;
  /* Перенос по словам под ширину `maxChars`; первая строка короче на `lead` (буква сектора).
     Слово длиннее строки остаётся целым — рвать его посередине хуже. */
  function wrapLabel(s, maxChars, lead) {
    var lines = [], cur = "", limit = maxChars - lead;
    String(s).split(/\s+/).forEach(function (w) {
      if (!w) return;
      if (cur && (cur + " " + w).length > limit) { lines.push(cur); cur = w; limit = maxChars; }
      else cur = cur ? cur + " " + w : w;
    });
    if (cur) lines.push(cur);
    return lines.length ? lines : [""];
  }

  /* Сетка: «за кругом», пояса стадий, клинья секторов (для подсветки), спицы, подписи.
     fs — кегли в единицах viewBox: fl — сектора, rl — кольца, fk — буквы секторов на узком экране. */
  function chrome(names, fs) {
    fs = fs || {};
    var fl = fs.fl || 2.75, rl = fs.rl || 1.9, fk = fs.fk || 4;
    var n = names.length, step = 360 / Math.max(1, n), h = [], i, k, p, q;
    h.push('<circle class="outer" cx="50" cy="50" r="' + OUTER_R + '"/>');
    for (k = 3; k >= 0; k--) h.push('<circle class="band b' + k + '" cx="50" cy="50" r="' + RING_R[k] + '"/>');
    for (i = 0; i < n; i++) {
      p = polar(SECTOR_START + i * step, RING_R[3]); q = polar(SECTOR_START + (i + 1) * step, RING_R[3]);
      h.push('<path class="wedge" data-f="' + i + '" d="M50 50L' + f2(p.x) + " " + f2(p.y) + "A42 42 0 " + (step > 180 ? 1 : 0) + " 1 " + f2(q.x) + " " + f2(q.y) + 'Z"/>');
    }
    if (n > 1) for (i = 0; i < n; i++) {
      p = polar(SECTOR_START + i * step, RING_R[0]); q = polar(SECTOR_START + i * step, RING_R[3]);
      h.push('<line class="spoke" x1="' + f2(p.x) + '" y1="' + f2(p.y) + '" x2="' + f2(q.x) + '" y2="' + f2(q.y) + '"/>');
    }
    var st = ' style="font-size:' + f2(rl) + 'px"';
    // центр — «раннее внедрение» в две строки, остальные пояса — вдоль верхней спицы у внутренней границы
    h.push('<text class="rl c" x="50" y="' + f2(50 - rl * 0.2) + '"' + st + '><tspan x="50">раннее</tspan><tspan x="50" dy="' + f2(rl * 1.1) + '">внедрение</tspan></text>');
    for (k = 1; k < 4; k++) h.push('<text class="rl l' + k + '" x="50" y="' + f2(50 - RING_R[k - 1] - 0.7) + '"' + st + ">" + BAND_LABEL[k] + "</text>");
    h.push('<text class="rl o" x="50" y="' + f2(50 - OUT_R + rl * 0.36) + '"' + st + ">снято</text>");
    for (i = 0; i < n; i++) {
      var mid = SECTOR_START + i * step + step / 2, c = Math.cos(mid * Math.PI / 180), s = Math.sin(mid * Math.PI / 180);
      p = polar(mid, FLABEL_R);
      var anchor = Math.abs(c) < 0.35 ? "middle" : c > 0 ? "start" : "end";
      // Сколько места до края коробки радара: у боковой подписи — в одну сторону, у верхней и
      // нижней — в обе от точки привязки. По нему — длина строки, а не по числу букв.
      // `slack` — отступ карточки вокруг радара (единицы viewBox, мерится в `_measure`): SVG
      // рисуется с overflow: visible, и подпись вправе занять его.
      var slack = fs.slack || 0;
      var room = anchor === "start" ? VB.x + VB.w + slack - p.x : anchor === "end" ? p.x - VB.x + slack : 2 * Math.min(p.x - VB.x, VB.x + VB.w - p.x) + 2 * slack;
      // Слово длиннее строки не рвётся — вместо этого у подписи кегль меньше, ровно чтобы оно влезло.
      var longest = Math.max.apply(null, String(names[i]).split(/\s+/).map(function (w) { return w.length; }));
      var flk = Math.min(fl, (room - 1) / (CHAR_EM * Math.max(longest, 8)));
      var lines = wrapLabel(names[i], Math.max(8, Math.floor((room - 1) / (flk * CHAR_EM))), 3), lh = flk * 1.2;
      var y0 = s < -0.35 ? p.y - (lines.length - 1) * lh : s > 0.35 ? p.y + flk * 0.75 : p.y - (lines.length - 1) * lh / 2 + flk * 0.35;
      h.push('<text class="fl" data-f="' + i + '" text-anchor="' + anchor + '" style="font-size:' + f2(flk) + 'px">' + lines.map(function (ln, j) {
        return '<tspan x="' + f2(p.x) + '" y="' + f2(y0 + j * lh) + '">' + (j ? "" : '<tspan class="flt">' + LETTERS.charAt(i) + "</tspan>  ") + esc(ln) + "</tspan>";
      }).join("") + "</text>");
      h.push('<text class="fk" data-f="' + i + '" x="' + f2(p.x) + '" y="' + f2(p.y + fk * 0.36) + '" style="font-size:' + f2(fk) + 'px">' + LETTERS.charAt(i) + "</text>");
    }
    return h.join("");
  }

  /* Держит точку в своей ячейке: угол — внутри сектора с полями, радиус — внутри пояса. */
  function clampCell(c) {
    var a = toDeg(Math.atan2(c.y - 50, c.x - 50)), r = Math.sqrt((c.x - 50) * (c.x - 50) + (c.y - 50) * (c.y - 50)), mid = (c.lo + c.hi) / 2;
    while (a < mid - 180) a += 360;
    while (a > mid + 180) a -= 360;
    var p = polar(clamp(c.lo, a, c.hi), clamp(c.rmin, r, c.rmax));
    c.x = p.x; c.y = p.y;
    if (c.core) {   // центр: обходим прямоугольник двухстрочной подписи, а не круг — так в поясе больше места
      var hx = 5.9 + c.rad, hy = 2.1 + c.rad, dy = c.y - 50.3, dx = c.x - 50;
      if (Math.abs(dx) < hx && Math.abs(dy) < hy) {
        // выталкиваем по ближней стороне прямоугольника
        if (hx - Math.abs(dx) < hy - Math.abs(dy)) c.x = 50 + (dx < 0 ? -hx : hx); else c.y = 50.3 + (dy < 0 ? -hy : hy);
      }
    }
  }
  /* Раскладка (общая для экрана и печати): в ячейке «сектор × пояс» точки веером, затем
     расталкивание без выхода из ячейки; снятое — веером за кругом в своём секторе.
     unit — px на единицу viewBox, sizeOf(d) — диаметр точки в px, rl — кегль подписей колец. */
  function positions(items, n, unit, sizeOf, rl) {
    var step = 360 / Math.max(1, n), res = {}, cells = {}, outs = {}, ring = [], i, k, it;
    items.forEach(function (d) {
      if (d.state === "out") (outs[d.facet] = outs[d.facet] || []).push(d);
      else if (d.state === "ring") (cells[d.facet + ":" + bandOf(d.stage)] = cells[d.facet + ":" + bandOf(d.stage)] || []).push(d);
    });
    Object.keys(cells).forEach(function (key) {
      var list = cells[key], f = +key.split(":")[0], s = +key.split(":")[1], m = list.length;
      var a0 = SECTOR_START + f * step, a1 = a0 + step;
      list.forEach(function (d, j) {
        var rad = sizeOf(d) / 2 / unit + 0.25;
        var rmin = (s === 0 ? CORE_R : RING_R[s - 1] + rad), rmax = RING_R[s] - rad;
        if (rmin > rmax) rmin = rmax = (rmin + rmax) / 2;
        var mg = toDeg(Math.asin(Math.min(1, (s === 0 ? rad * 0.5 : rad + 0.3) / (s === 0 ? Math.max(rmin, 8) : rmin))));
        var lo = a0 + mg, hi = a1 - mg;
        if (s > 0) {   // полоса подписей колец вверху (−90°): у соседних с ней секторов поле шире
          var lane = toDeg(Math.atan((laneW(s, rl) + rad) / rmin));
          if (near(a0, -90)) lo = Math.max(lo, a0 + lane);
          if (near(a1, 270)) hi = Math.min(hi, a1 - lane);
        }
        if (lo > hi) lo = hi = (lo + hi) / 2;
        var t = m > 1 ? j / (m - 1) : 0.5, a = lo + (hi - lo) * (m > 1 ? 0.1 + 0.8 * t : 0.5);
        var r = (rmin + rmax) / 2 + (m > 1 ? (j % 2 ? 1 : -1) * (rmax - rmin) / 3 : 0);
        var p = polar(a, r);
        ring.push({ d: d, rad: rad, lo: lo, hi: hi, rmin: rmin, rmax: rmax, x: p.x, y: p.y, core: s === 0 });
      });
    });
    for (it = 0; it < 120; it++) {
      var moved = false;
      for (i = 0; i < ring.length; i++) for (k = i + 1; k < ring.length; k++) {
        var A = ring[i], B = ring[k], dx = B.x - A.x, dy = B.y - A.y, dist = Math.sqrt(dx * dx + dy * dy), need = A.rad + B.rad;
        if (dist >= need) continue;
        if (dist < 1e-4) { dx = 0.01; dy = 0.01 * (k - i); dist = Math.sqrt(dx * dx + dy * dy); }
        var push = (need - dist) / 2 + 0.01;
        A.x -= dx / dist * push; A.y -= dy / dist * push; B.x += dx / dist * push; B.y += dy / dist * push; moved = true;
      }
      ring.forEach(clampCell);
      if (!moved) break;
    }
    ring.forEach(function (c) { res[c.d.label] = { x: c.x, y: c.y }; });
    Object.keys(outs).forEach(function (key) {
      var list = outs[key], f = +key, m = list.length, a0 = SECTOR_START + f * step, a1 = a0 + step;
      var lo = a0 + (near(a0, -90) ? 8 : 3), hi = a1 - (near(a1, 270) ? 8 : 3);
      list.forEach(function (d, j) {
        var t = m > 1 ? j / (m - 1) : 0.5, p = polar(lo + (hi - lo) * t, OUT_R + (m > 1 ? (j % 2 ? 0.9 : -0.9) : 0));
        res[d.label] = { x: p.x, y: p.y };
      });
    });
    return res;
  }

  /* Направление сигнала (Signal.facet). У отчётов до привязки и при сбое эмбеддера его нет:
     тогда сектор выбирается по ярлыку — точки не валятся в один сектор, — но подсказка и
     фильтр направлений такую точку направлением не называют (known = false). */
  function facetOf(s) { return s.facet != null ? s.facet : s.extra && typeof s.extra.facet === "number" ? s.extra.facet : null; }
  function known(f, n) { return typeof f === "number" && f >= 0 && f < n && f === Math.floor(f); }
  function sectorOf(f, label, n) {
    if (known(f, n)) return f;
    var h = 0, s = String(label || "");
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return h % Math.max(1, n);
  }

  /* Отчёт → точки: топ с номером и уверенностью, вне топа, снятое (включая «не о предмете»). */
  function itemsOf(report) {
    var rankOf = {};
    report.signals.forEach(function (s, k) { rankOf[s.label] = { rank: k + 1, conf: s.confidence }; });
    return report.signals.concat(report.thin || [], report.excluded || []).map(function (s) {
      var r = rankOf[s.label];
      return { label: s.label, title: s.title, facet: facetOf(s), stage: s.stage,
        kind: s.kind, kind_label: s.kind_label, sources: (s.sources || []).length, why: s.why,
        offTopic: s.kind === "emerging" && s.relevance != null && s.relevance < 2, rank: r ? r.rank : null, conf: r ? r.conf : null };
    });
  }

  /* Легенда с настоящими образцами; буквы секторов видны только на узком экране. */
  function keyHTML(facets) {
    function it(i, t) { return '<span class="it">' + i + "<span>" + t + "</span></span>"; }
    return '<div class="grp"><span class="h">стадия, от центра к краю</span>' + BAND_LABEL.map(function (s, k) { return it('<i class="sw b' + k + '"></i>', s); }).join("") + "</div>" +
      '<div class="grp"><span class="h">размер — число источников</span>' + it('<i class="dk lo"></i>', "до 3") + it('<i class="dk lo s2"></i>', "4–5") + it('<i class="dk lo s3"></i>', "6 и больше") + "</div>" +
      '<div class="grp"><span class="h">заливка — уверенность</span>' + it('<i class="dk hi"></i>', "выше 75 %") + it('<i class="dk mid"></i>', "50–75 %") + it('<i class="dk lo"></i>', "ниже 50 %") + "</div>" +
      '<div class="grp"><span class="h">вне топа</span>' + it('<i class="dk thin"></i>', "зарождается") + it('<i class="dk out"></i>', "снято, за кругом") + "</div>" +
      (facets && facets.length ? '<div class="grp letters"><span class="h">сектора</span>' + facets.map(function (f, i) { return it('<b class="lt">' + LETTERS.charAt(i) + "</b>", esc(f)); }).join("") + "</div>" : "");
  }

  function Radar(root, opts) {
    opts = opts || {};
    this.root = root; this.opts = opts;
    this.onSelect = opts.onSelect || function () {};
    this.onHover = opts.onHover || function () {};
    this.onFacet = opts.onFacet || function () {};
    this.nFacets = 1; this.facets = [];
    this.dots = {}; this.pool = []; this.order = []; this.selected = null; this.hot = null;
    this.focusF = null; this.pinned = null; this.poolSeed = 0; this.ranked = false; this._bulk = false; this.w = 0;
    root.innerHTML =
      '<div class="radar">' +
      '<svg class="radar-svg" viewBox="' + [VB.x, VB.y, VB.w, VB.h].join(" ") + '" role="img" aria-label="радар сигналов"><g class="chrome"></g></svg>' +
      '<div class="sweep" hidden></div><div class="layer-dots"></div><div class="tip" role="tooltip" hidden></div>' +
      "</div>" + (opts.key ? '<div class="radar-key"></div>' : "");
    this.el = root.querySelector(".radar");
    this.svg = root.querySelector("svg");
    this.chromeEl = root.querySelector(".chrome");
    this.dotsEl = root.querySelector(".layer-dots");
    this.sweepEl = root.querySelector(".sweep");
    this.tip = root.querySelector(".tip");
    this.keyEl = root.querySelector(".radar-key");
    var self = this;
    this.dotsEl.addEventListener("click", function (e) {
      var d = self._dotOf(e.target); if (!d) return;
      self.select(d.label); self.onSelect(d.label);
    });
    this.dotsEl.addEventListener("mouseover", function (e) { var d = self._dotOf(e.target); if (d) self._hover(d, 233); });
    this.dotsEl.addEventListener("mouseout", function (e) { if (self._dotOf(e.target)) self._unhover(144); });
    this.dotsEl.addEventListener("focusin", function (e) { var d = self._dotOf(e.target); if (d) self._hover(d, 0); });
    this.dotsEl.addEventListener("focusout", function () { self._unhover(0); });
    function facetOf(t) { var el = t && t.closest ? t.closest(".fl, .fk") : null; return el ? parseInt(el.getAttribute("data-f"), 10) : null; }
    this.svg.addEventListener("mouseover", function (e) { var f = facetOf(e.target); if (f != null) self.focus(f); });
    this.svg.addEventListener("mouseout", function (e) { if (facetOf(e.target) != null) self.focus(self.pinned); });
    this.svg.addEventListener("click", function (e) {
      var f = facetOf(e.target); if (f == null) return;
      self.pin(self.pinned === f ? null : f); self.onFacet(self.pinned);
    });
    this._measure(false);
    this._draw();
    if (window.ResizeObserver) { this._ro = new ResizeObserver(function () { self._measure(true); }); this._ro.observe(this.el); }
  }

  /* Масштаб: размеры точек и кегли подписей — в px экрана, геометрия — в единицах viewBox. */
  Radar.prototype._measure = function (relayout) {
    // узко — по ширине контейнера (не самого радара: на узком экране он шире контейнера, см. .radar.narrow)
    var narrow = (this.root.clientWidth || NOMINAL_PX) < 520;
    if (narrow !== this.narrow) { this.narrow = narrow; this.el.classList.toggle("narrow", narrow); }
    var w = this.el.clientWidth || NOMINAL_PX;
    if (relayout && Math.abs(w - this.w) < 1) return;
    this.w = w; this.unit = w / VB.w; this.k = clamp(0.6, w / NOMINAL_PX, 1);
    this.fs = { fl: clamp(11, 14 * w / NOMINAL_PX, 14) / this.unit, rl: clamp(9, 10 * w / NOMINAL_PX, 10) / this.unit, fk: 13 / this.unit };
    // Место по бокам радара внутри карточки (отступ и поля по max-width) — подписям секторов,
    // минус 6 px до рамки. Карточки нет или радар скрыт — запаса нет.
    var host = this.root.closest ? this.root.closest(".surface") : null;
    if (host && this.el.clientWidth) {
      var a = this.el.getBoundingClientRect(), b = host.getBoundingClientRect();
      this.fs.slack = Math.max(0, Math.min(a.left - b.left, b.right - a.right) - 6) / this.unit;
    }
    if (this.keyEl) this.keyEl.classList.toggle("narrow", this.narrow);
    if (relayout) { this._draw(); this._restyle(); this.layout(); }
  };
  Radar.prototype._draw = function () {
    var n = this.facets.length;
    this.chromeEl.innerHTML = chrome(this.facets, this.fs);
    this.svg.setAttribute("aria-label", "радар: " + n + " " + plural(n, "направление", "направления", "направлений") + " запроса, кольца — стадии зрелости");
    if (this.keyEl) this.keyEl.innerHTML = keyHTML(this.facets);
    this._focusChrome();
  };
  Radar.prototype._restyle = function () { var self = this; Object.keys(this.dots).forEach(function (l) { self._style(self.dots[l]); }); };
  Radar.prototype._dotOf = function (t) {
    var b = t && t.closest ? t.closest(".dot") : null;
    if (!b || b.classList.contains("pool")) return null;
    return this.dots[b.getAttribute("data-label")] || null;
  };
  Radar.prototype._hover = function (d, delay) {
    var self = this; clearTimeout(this._tt); this.onHover(d.label);
    this._tt = setTimeout(function () { self._tipShow(d); }, delay);
  };
  Radar.prototype._unhover = function (delay) {
    var self = this; clearTimeout(this._tt); this.onHover(null);
    this._tt = setTimeout(function () { self.tip.hidden = true; }, delay);
  };
  /* Подсказка: название, место и стадия, источники, уверенность, направление, почему. */
  Radar.prototype._tipShow = function (d) {
    if (d.x == null) return;
    var meta = [], top = d.rank && d.state !== "out";
    if (top) meta.push("№ " + d.rank);
    meta.push(d.state === "out" ? "снято" + (d.kindLabel ? ": " + d.kindLabel : "") : RING_SHORT[d.stage]);
    if (d.sources) meta.push(d.sources + " " + plural(d.sources, "источник", "источника", "источников"));
    if (top && d.conf != null) meta.push(Math.round(d.conf * 100) + " %");
    var t = this.tip;
    t.innerHTML = "<b>" + esc(d.title) + '</b><span class="m">' + esc(meta.join(", ")) + "</span>" +
      (d.known && this.facets[d.facet] ? '<span class="f">' + esc(this.facets[d.facet]) + "</span>" : "") +
      (d.why ? '<span class="w">' + esc(noRefs(d.why)) + "</span>" : "");
    t.hidden = false; t.style.left = "0px"; t.style.top = "0px";
    var W = this.el.clientWidth, H = this.el.clientHeight, x = (d.x - VB.x) / VB.w * W, y = (d.y - VB.y) / VB.h * H, r = this._size(d) / 2;
    var tw = t.offsetWidth, th = t.offsetHeight, ty = y - r - 8 - th;
    if (ty < -21) ty = y + r + 8;
    t.style.left = clamp(0, x - tw / 2, Math.max(0, W - tw)) + "px"; t.style.top = ty + "px";
  };

  /* Сектора: спицы и подписи направлений. */
  Radar.prototype.setFacets = function (names) {
    this.facets = names.slice(); this.nFacets = Math.max(1, names.length);
    this._draw(); this.layout(); this.turn();
  };
  /* Подсветка сектора: клин и подпись ярче, точки других секторов приглушены. pin — закрепить. */
  Radar.prototype.focus = function (f) { this.focusF = f == null ? null : f; this._focusChrome(); this._restyle(); };
  Radar.prototype.pin = function (f) { this.pinned = f == null ? null : f; this.focus(this.pinned); };
  Radar.prototype._focusChrome = function () {
    var f = this.focusF;
    this.chromeEl.querySelectorAll("[data-f]").forEach(function (el) { el.classList.toggle("on", f != null && +el.getAttribute("data-f") === f); });
  };

  /* Засечки стадий процесса раньше стояли на радаре; теперь прогресс — в отдельном блоке. Метод оставлен для совместимости. */
  Radar.prototype.tick = function () {};
  Radar.prototype.sweep = function (on) { this.sweepEl.hidden = !on; };
  /* Один оборот развёртки на событие прогона (NFR-10: движение — только от событий; постоянная
     анимация одна — пульс статуса). Событие посреди оборота новый не начинает. */
  Radar.prototype.turn = function () {
    var el = this.sweepEl;
    if (el.hidden || el.classList.contains("turn")) return;
    el.classList.add("turn");
    el.addEventListener("animationend", function () { el.classList.remove("turn"); }, { once: true });
  };

  /* N безымянных кандидатов у края, в полосе «за кругом», — после сведения. */
  Radar.prototype.scatter = function (n) {
    for (var i = 0; i < n; i++) {
      var b = document.createElement("button");
      b.className = "dot pool"; b.type = "button"; b.tabIndex = -1;
      var p = polar(rnd(this.poolSeed + 1) * 360, 43.6 + rnd(this.poolSeed + 99) * 2.8);
      this.poolSeed++;
      b.style.left = px(p.x); b.style.top = py(p.y);
      this.dotsEl.appendChild(b); this.pool.push(b);
    }
    this.turn();
  };

  /* Вердикт: кандидат получает имя и место — в поясе своей стадии или за кругом. */
  Radar.prototype.place = function (label, info) {
    var d = this.dots[label];
    if (!d) {
      var b = this.pool.shift();
      if (!b) { b = document.createElement("button"); b.type = "button"; var p0 = polar(-90, OUTER_R); b.style.left = px(p0.x); b.style.top = py(p0.y); this.dotsEl.appendChild(b); }
      d = this.dots[label] = { el: b, label: label }; this.order.push(label);
    }
    d.known = known(info.facet, this.nFacets); d.facet = sectorOf(info.facet, label, this.nFacets);
    d.stage = Math.max(0, stageIndex(info.stage)); d.title = info.title || label;
    d.sources = info.sources || 0; d.kind = info.kind || "emerging"; d.kindLabel = info.kind_label || ""; d.why = info.why || "";
    d.state = d.kind === "emerging" ? "ring" : "out";
    d.el.setAttribute("data-label", label);
    this._style(d);
    if (!this._bulk) {
      this.layout();
      d.el.classList.add("arrive");
      setTimeout(function () { d.el.classList.remove("arrive"); }, 610);
      this.turn();
    }
  };
  Radar.prototype.outside = function (label) {
    var d = this.dots[label]; if (!d) return;
    d.state = "out"; if (d.kind === "emerging") d.kindLabel = "шум";
    this._style(d); if (!this._bulk) this.layout();
  };

  /* Скоринг: номер, размер и заливка у топа; остальные зарождающиеся — пунктирные. Tab идёт по топу. */
  Radar.prototype.rank = function (labels, confidence) {
    var self = this;
    this.ranked = true;
    Object.keys(this.dots).forEach(function (l) { self.dots[l].rank = null; self.dots[l].conf = null; });
    labels.forEach(function (l, k) { var d = self.dots[l]; if (d) { d.rank = k + 1; d.conf = confidence[k]; } });
    labels.forEach(function (l) { var d = self.dots[l]; if (d) self.dotsEl.appendChild(d.el); });
    this._restyle();
    if (!this._bulk) this.layout();
  };

  Radar.prototype._size = function (d) { return d.state === "out" && this.selected === d.label ? 9 : sizePx(d, this.k); };
  Radar.prototype._style = function (d) {
    var el = d.el, sz = this._size(d), top = !!(d.rank && d.state !== "out"), cls = "dot";
    if (d.state === "out") cls += " out";
    else if (!this.ranked) cls += " new";
    else cls += top ? " " + confClass(d.conf) : " thin";
    if (this.selected === d.label) cls += " sel";
    if (this.hot === d.label) cls += " hot";
    if (this.focusF != null && (!d.known || d.facet !== this.focusF)) cls += " dim";
    el.className = cls;
    el.style.width = sz + "px"; el.style.height = sz + "px";
    el.style.fontSize = top ? Math.max(9, Math.round(11 * this.k)) + "px" : "";
    el.textContent = top ? String(d.rank) : "";
    el.tabIndex = top ? 0 : -1;
    el.setAttribute("aria-label", d.title + (top ? ", № " + d.rank : "") + (d.state === "out" ? " — снято" : ", " + RING_SHORT[d.stage]));
    el.removeAttribute("title");
  };

  /* Раскладка — общая функция positions(); +4 px на кольцо выбора вокруг точки. */
  Radar.prototype.layout = function () {
    var self = this, items = this.order.map(function (l) { return self.dots[l]; });
    var pos = positions(items, this.nFacets, this.unit, function (d) { return self._size(d) + 4; }, this.fs.rl);
    items.forEach(function (d) {
      var p = pos[d.label]; if (!p) return;
      d.x = p.x; d.y = p.y; d.el.style.left = px(p.x); d.el.style.top = py(p.y);
    });
    this._name();
  };

  Radar.prototype.select = function (label) { this.selected = label; this._restyle(); this._name(); };
  /* Подсветка извне: строка указателя или стола. */
  Radar.prototype.hover = function (label) {
    var prev = this.hot; this.hot = label || null;
    if (prev && this.dots[prev]) this._style(this.dots[prev]);
    if (label && this.dots[label]) this._style(this.dots[label]);
  };
  Radar.prototype._name = function () {
    var old = this.el.querySelector(".name"); if (old) old.remove();
    // подпись выбранной точки — только по opts.names: карточка справа и так её называет, а на радаре подпись перекрывает шкалу колец
    var d = this.dots[this.selected]; if (!d || d.x == null || this.narrow || !this.opts.names) return;
    var n = document.createElement("span"); n.className = "name"; n.textContent = d.title;
    var xp = (d.x - VB.x) / VB.w * 100, r = this._size(d) / 2;
    n.style.top = "calc(" + py(d.y) + " + " + Math.round(r + 8) + "px)";
    if (d.x <= 50) n.style.left = "calc(" + xp.toFixed(3) + "% - 13px)"; else n.style.right = "calc(" + (100 - xp).toFixed(3) + "% - 13px)";
    this.el.appendChild(n);
  };

  /* Готовый отчёт разом. */
  Radar.prototype.setData = function (report) {
    var self = this;
    this.dotsEl.innerHTML = ""; this.dots = {}; this.pool = []; this.order = []; this.ranked = false;
    this._bulk = true;
    this.setFacets(report.facets);
    this.sweep(false);
    itemsOf(report).forEach(function (it) { self.place(it.label, it); if (it.offTopic) self.outside(it.label); });
    this.rank(report.signals.map(function (s) { return s.label; }), report.signals.map(function (s) { return s.confidence; }));
    this._bulk = false;
    this.layout();
  };
  Radar.prototype.clear = function () { this.dotsEl.innerHTML = ""; this.dots = {}; this.pool = []; this.order = []; this.selected = null; this.ranked = false; this.tip.hidden = true; this._name(); };
  Radar.prototype.destroy = function () { if (this._ro) this._ro.disconnect(); clearTimeout(this._tt); };

  /* Статичный SVG для печати: та же сетка и раскладка, точки — кругами с номером. */
  function toSVG(report, opts) {
    opts = opts || {};
    var unit = NOMINAL_PX / VB.w, fs = { fl: 2.75, rl: 1.9, fk: 4 };
    var items = itemsOf(report).map(function (it) {
      return { label: it.label, facet: sectorOf(it.facet, it.label, report.facets.length), stage: Math.max(0, stageIndex(it.stage)), rank: it.rank, conf: it.conf, sources: it.sources,
        state: it.kind === "emerging" && !it.offTopic ? "ring" : "out" };
    });
    var pos = positions(items, report.facets.length, unit, function (d) { return sizePx(d) + 4; }, fs.rl);
    function weight(d) { return d.state === "out" ? 0 : !d.rank ? 1 : 2 + (100 - d.rank) / 1000; }
    items.sort(function (a, b) { return weight(a) - weight(b); });
    var dots = items.map(function (d) {
      var p = pos[d.label]; if (!p) return "";
      var top = d.rank && d.state !== "out", r = sizePx(d) / 2 / unit;
      var cls = d.state === "out" ? "out" : top ? confClass(d.conf) : "thin";
      return '<g class="pd ' + cls + '"><circle cx="' + f2(p.x) + '" cy="' + f2(p.y) + '" r="' + f2(r) + '"/>' +
        (top ? '<text x="' + f2(p.x) + '" y="' + f2(p.y + 0.74) + '" style="font-size:2.06px">' + d.rank + "</text>" : "") + "</g>";
    }).join("");
    return '<svg class="radar-svg radar-print" xmlns="http://www.w3.org/2000/svg" viewBox="' + [VB.x, VB.y, VB.w, VB.h].join(" ") + '"' +
      (opts.width ? ' width="' + opts.width + '"' : "") + ' role="img" aria-label="радар сигналов">' + chrome(report.facets, fs) + dots + "</svg>";
  }

export default {
    create: function (root, opts) { return new Radar(root, opts); },
    toSVG: toSVG, keyHTML: keyHTML, RING_SHORT: RING_SHORT, BAND_LABEL: BAND_LABEL, LETTERS: LETTERS,
    facetOf: facetOf, known: known, itemsOf: itemsOf, positions: positions
  };
