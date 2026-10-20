/* Pelican UI v2 — знак: пеликан ныряет и бьёт клювом точно в центр мишени. Сетка 55 × 55.
   Птица задана в своей системе: кончик клюва в (0, 0), летит в сторону −x; вариант знака
   ставит её поворотом и масштабом так, что кончик клюва приходится в центр колец. Контур —
   одним штрихом (его прорисовывает заставка), точка попадания — жёлтый «блип», кольца под
   птицей вырезаны маской, чтобы линии не перекрещивались. Разметка — константная, из
   геометрии ниже, без данных: её вставляет заставка (components/Splash.jsx). */
  // кончик клюва → верх клюва → лоб → темя → спина → хвост → брюхо → горло → провисший мешок → кончик.
  // В нырке шея втянута, крылья сложены назад: тело — стрела, клюв почти с туловище.
  var BIRD = "M0 0 L19 -3.4 C20 -7.6 25.2 -7.8 26.2 -4.2 C31.5 -7 40 -6.2 45 -2.6 L51 0 L45 2.2 C38 6.4 30 6.8 25.2 3.9 C19 9.2 8 7.2 0 0 Z";
  var WING = "M28.5 -1.2 C34 -3.4 41 -2.9 47.5 -.6";
  var BILL = "M1.6 -.1 L19.4 -1.4";
  // лапы в нырке поджаты назад: голень от брюха вдоль тела, перепончатая лапа треугольником за хвостом
  var LEGS = "M36.5 5.4 L43.6 8.6 M43.6 8.6 L49.6 7 L48.6 9.6 L50.2 12 Z";
  var EYE = [22.3, -3.7];
  var WATER = "var(--water, #2F6FE4)";

  /* Варианты знака (выбор — logo-lab.html). a — угол нырка, s — масштаб птицы, at — центр мишени.
     under — пеликан уходит под воду: вода — чаша под линией поверхности (pool), entry — где тело
     пересекает поверхность, depth — сколько птицы (в её единицах, от кончика клюва) уже под водой.
     Над водой — контур чернилами, под водой — тот же контур цветом воды; кончик клюва ловит жёлтый
     блип на глубине (at считается из entry), по поверхности от входа — круги (flat — сжатие) и брызги. */
  var VARIANTS = {
    dive:  { name: "Нырок",   a: -50, s: .9, under: true, entry: [30, 25.5], depth: 29, pool: [27.5, 25.5, 25], rings: [5, 9.6, 14.2], flat: .22 },
    plumb: { name: "Отвесно", a: -90, s: .8,  at: [27.5, 44.5], rings: [3.6, 7.2, 10.4] },
    sight: { name: "Прицел",  a: -46, s: .46, at: [27.5, 27.5], rings: [7, 14.5, 22.5], ticks: true }
  };
  var DEFAULT = "dive";
  var uid = 0;

  function geo(v) {
    var r = v.a * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    if (v.under && !v.at) v.at = [v.entry[0] - v.depth * v.s * c, v.entry[1] - v.depth * v.s * s];   // кончик клюва — на глубине по линии нырка
    function pt(p) { return [v.at[0] + v.s * (p[0] * c - p[1] * s), v.at[1] + v.s * (p[0] * s + p[1] * c)]; }
    return { t: "translate(" + v.at[0] + " " + v.at[1] + ") rotate(" + v.a + ") scale(" + v.s + ")", eye: pt(EYE), dir: [-c, -s] };
  }

  /* o: {cls, stroke, variant, rings:false, eye:false}. Классы для заставки: ring-a/b/c, bird, hit, dive. */
  function svg(o) {
    o = o || {};
    var v = VARIANTS[o.variant || DEFAULT], g = geo(v), w = o.stroke || 1.6, id = "pm" + (++uid);
    var bw = (w / v.s).toFixed(2), h = [];
    h.push('<svg class="' + (o.cls || "logo") + '" viewBox="0 0 55 55" fill="none" stroke="currentColor" stroke-width="' + w + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">');
    if (v.under) return underWater(o, v, g, w, bw, id, h);
    if (o.rings !== false) {
      h.push('<defs><mask id="' + id + '" maskUnits="userSpaceOnUse" x="0" y="0" width="55" height="55"><rect width="55" height="55" fill="#fff"/>' +
        '<g transform="' + g.t + '" stroke="#000" stroke-width="' + (bw * 1 + 5 / v.s).toFixed(2) + '"><path d="' + BIRD + '" fill="#000"/><path d="' + WING + '"/><path d="' + LEGS + '" fill="#000"/></g></mask></defs>');
      var cx = v.at[0], cy = v.at[1];
      if (v.water) {
        // вода: заливка-гладь, круги по воде, брызги от входа клюва
        var R = v.rings[v.rings.length - 1];
        h.push('<g mask="url(#' + id + ')" stroke="' + WATER + '"><ellipse class="pool" cx="' + cx + '" cy="' + cy + '" rx="' + (R + 1.5) + '" ry="' + ((R + 1.5) * v.flat).toFixed(2) + '" fill="' + WATER + '" fill-opacity=".14" stroke="none"/>' +
          v.rings.map(function (r, k) {
            return '<ellipse class="ring-' + "abc".charAt(k) + '" pathLength="100" cx="' + cx + '" cy="' + cy + '" rx="' + r + '" ry="' + (r * v.flat).toFixed(2) + '" stroke-opacity="' + (k === 0 ? 1 : k === 1 ? .7 : .4) + '"/>';
          }).join("") +
          '<path class="drops" d="M' + (cx - 3.2) + " " + (cy - 2.4) + "l-2.4 -3.4M" + (cx - .6) + " " + (cy - 3.2) + "l-.6 -3.6M" + (cx + 4) + " " + (cy - 1.4) + 'l2.6 -1.6"/>');
      } else {
        h.push('<g mask="url(#' + id + ')">' + v.rings.map(function (r, k) {
          return '<circle class="ring-' + "abc".charAt(k) + '" pathLength="100" cx="' + cx + '" cy="' + cy + '" r="' + r + '" stroke-opacity="' + (k === 0 ? .9 : k === 1 ? .55 : .3) + '"/>';
        }).join(""));
      }
      if (v.ticks) h.push('<path class="ticks" stroke-opacity=".45" d="M27.5 1.5v4M27.5 49.5v4M1.5 27.5h4M49.5 27.5h4"/>');
      h.push("</g>");
    }
    h.push('<g class="dive" style="--dx:' + (g.dir[0] * -14).toFixed(1) + "px;--dy:" + (g.dir[1] * -14).toFixed(1) + 'px"><g transform="' + g.t + '" stroke-width="' + bw + '">' +
      '<path class="bird" pathLength="100" d="' + BIRD + '"/><path class="bird wing" pathLength="100" d="' + WING + '"/><path class="bird legs" pathLength="100" d="' + LEGS + '"/>' +
      (o.bill ? '<path class="bird bill" pathLength="100" stroke-opacity=".55" d="' + BILL + '"/>' : "") + "</g>" +
      '<circle class="eye-dot" cx="' + g.eye[0].toFixed(2) + '" cy="' + g.eye[1].toFixed(2) + '" r="' + (0.95 * Math.max(.7, v.s)).toFixed(2) + '" fill="currentColor" stroke="none"/></g>');
    if (o.eye !== false) h.push('<circle class="hit" cx="' + v.at[0] + '" cy="' + v.at[1] + '" r="' + (v.s < .6 ? 2.6 : 2.3) + '" fill="var(--accent, #F2C230)" stroke="none"/>');
    h.push("</svg>");
    return h.join("");
  }

  /* Пеликан под водой: чаша воды, поверхность с кругами от входа, птица над водой — чернилами,
     под водой — цветом воды (обрезка по линии поверхности), на глубине у клюва — жёлтый блип. */
  function underWater(o, v, g, w, bw, id, h) {
    var px = v.pool[0], py = v.pool[1], pr = v.pool[2], ex = v.entry[0], ey = v.entry[1];
    var dive = ' class="dive" style="--dx:' + (g.dir[0] * -14).toFixed(1) + "px;--dy:" + (g.dir[1] * -14).toFixed(1) + 'px"';
    function bird(extra) {
      return '<g transform="' + g.t + '" stroke-width="' + bw + '"' + extra + ">" +
        '<path class="bird" pathLength="100" d="' + BIRD + '"/><path class="bird wing" pathLength="100" d="' + WING + '"/><path class="bird legs" pathLength="100" d="' + LEGS + '"/>' +
        (o.bill ? '<path class="bird bill" pathLength="100" stroke-opacity=".55" d="' + BILL + '"/>' : "") +
        '<circle class="eye-dot" cx="' + EYE[0] + '" cy="' + EYE[1] + '" r="1.05" fill="currentColor" stroke="none"/></g>';
    }
    var bowl = "M" + (px - pr) + " " + py + "A" + pr + " " + pr + " 0 0 0 " + (px + pr) + " " + py + "Z";
    h.push("<defs>" +
      '<clipPath id="' + id + 'a"><rect x="0" y="-20" width="55" height="' + (py + 20) + '"/></clipPath>' +
      '<clipPath id="' + id + 'b"><rect x="0" y="' + py + '" width="55" height="' + (75 - py) + '"/></clipPath>' +
      '<linearGradient id="' + id + 'g" x1="0" y1="' + py + '" x2="0" y2="' + (py + pr) + '" gradientUnits="userSpaceOnUse">' +
        '<stop offset="0" stop-color="' + WATER + '" stop-opacity=".26"/><stop offset="1" stop-color="' + WATER + '" stop-opacity="0"/></linearGradient>' +
      '<mask id="' + id + '" maskUnits="userSpaceOnUse" x="0" y="0" width="55" height="55"><rect width="55" height="55" fill="#fff"/>' +
        '<g transform="' + g.t + '" stroke="#000" stroke-width="' + (bw * 1 + 4 / v.s).toFixed(2) + '"><path d="' + BIRD + '" fill="#000"/><path d="' + WING + '"/><path d="' + LEGS + '" fill="#000"/></g></mask></defs>');
    if (o.rings !== false) {
      h.push('<path class="pool" d="' + bowl + '" fill="url(#' + id + 'g)" stroke="none"/>');
      h.push('<g mask="url(#' + id + ')" stroke="' + WATER + '">' +
        '<path class="ring-c" pathLength="100" stroke-opacity=".55" d="M' + (px - pr) + " " + py + "H" + (px + pr) + '"/>' +
        v.rings.map(function (r, k) {
          return '<ellipse class="ring-' + "ab".charAt(Math.min(k, 1)) + '" pathLength="100" cx="' + ex + '" cy="' + ey + '" rx="' + r + '" ry="' + (r * v.flat).toFixed(2) + '" stroke-opacity="' + (k === 0 ? 1 : k === 1 ? .7 : .45) + '"/>';
        }).join("") +
        '<path class="drops" d="M' + (ex - 7) + " " + (ey - 1.6) + "l-2.6 -3.2M" + (ex - 4.2) + " " + (ey - 3.2) + "l-1 -4.2M" + (ex + 7.6) + " " + (ey - 1.2) + 'l3 -2.2"/></g>');
    }
    // под водой — цветом воды, над водой — чернилами; обе части едут вместе в нырке заставки
    h.push('<g clip-path="url(#' + id + 'b)" stroke="' + WATER + '" color="' + WATER + '"><g' + dive + ">" + bird(' stroke-opacity=".9"') + "</g></g>");
    h.push('<g clip-path="url(#' + id + 'a)"><g' + dive + ">" + bird("") + "</g></g>");
    if (o.eye !== false) h.push('<circle class="hit" cx="' + v.at[0].toFixed(2) + '" cy="' + v.at[1].toFixed(2) + '" r="2.3" fill="var(--accent, #F2C230)" stroke="none"/>');
    h.push("</svg>");
    return h.join("");
  }

  /* Словесный знак АС ПЕЛИКАН — логотип везде, кроме заставки: заглавные TikTok Sans
     широким начертанием, жирным, с разрядкой (стили .wm в system.css). */
  function wordmark(cls) {
    return '<span class="wm' + (cls ? " " + cls : "") + '">АС ПЕЛИКАН</span>';
  }

  /* Фавикон: «П» гротеском на чернилах с жёлтой точкой — монограмма словесного знака (шрифт — системный: веб-шрифт в data-URL не грузится). */
  function favicon() {
    var s = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 55 55"><rect width="55" height="55" rx="13" fill="#16150F"/>' +
      '<text x="25" y="40" text-anchor="middle" font-family="\'Segoe UI\', Arial, sans-serif" font-weight="800" font-size="34" fill="#F3F1EC">П</text>' +
      '<circle cx="42" cy="39" r="3.6" fill="#F2C230"/></svg>';
    var l = document.querySelector('link[rel="icon"]') || document.createElement("link");
    l.rel = "icon"; l.type = "image/svg+xml"; l.href = "data:image/svg+xml," + encodeURIComponent(s);
    if (!l.parentNode) document.head.appendChild(l);
  }

  Object.keys(VARIANTS).forEach(function (k) { geo(VARIANTS[k]); });   // у варианта «под воду» at считается из entry
  var HIT = VARIANTS[DEFAULT].at;
export default { svg: svg, favicon: favicon, wordmark: wordmark, VARIANTS: VARIANTS, DEFAULT: DEFAULT, HIT: { cx: HIT[0], cy: HIT[1] }, EYE: { cx: HIT[0], cy: HIT[1] } };
