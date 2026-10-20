/* АС ПЕЛИКАН — иконки. Сетка 21 × 21, штрих 1.5, цвет — текущий цвет текста.
   Спрайт <symbol> вставляется в документ один раз; компонент `Icon` (components/Icon.jsx)
   ссылается на символ через <use>. */
  var P = {
    radar: '<circle cx="10.5" cy="10.5" r="7.5"/><circle cx="10.5" cy="10.5" r="3.5"/><path d="M10.5 10.5L16 5"/>',
    table: '<rect x="3" y="4" width="15" height="13" rx="1.5"/><path d="M3 8.5h15M3 12.75h15M8 8.5V17"/>',
    print: '<path d="M6 8V3.5h9V8"/><rect x="3" y="8" width="15" height="7" rx="1.5"/><path d="M6 12.5h9v5H6z"/>',
    link: '<path d="M9 12a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 0 0-5-5l-1 1"/><path d="M12 9a3.5 3.5 0 0 0-5 0l-2.5 2.5a3.5 3.5 0 0 0 5 5l1-1"/>',
    history: '<path d="M3.5 10.5a7 7 0 1 0 2.05-4.95"/><path d="M3.5 3.5v3h3"/><path d="M10.5 6.5v4l2.5 2"/>',
    search: '<circle cx="9" cy="9" r="5.5"/><path d="M13 13l4.5 4.5"/>',
    "arrow-left": '<path d="M16.5 10.5h-12M9 6l-4.5 4.5L9 15"/>',
    "arrow-right": '<path d="M4.5 10.5h12M12 6l4.5 4.5L12 15"/>',
    sun: '<circle cx="10.5" cy="10.5" r="3.5"/><path d="M10.5 2.5v2M10.5 16.5v2M2.5 10.5h2M16.5 10.5h2M4.8 4.8l1.4 1.4M14.8 14.8l1.4 1.4M4.8 16.2l1.4-1.4M14.8 6.2l1.4-1.4"/>',
    moon: '<path d="M16.5 12.5A6.5 6.5 0 0 1 8.5 4.5a6.5 6.5 0 1 0 8 8z"/>',
    copy: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M14 7V5a1.5 1.5 0 0 0-1.5-1.5h-7A1.5 1.5 0 0 0 4 5v7a1.5 1.5 0 0 0 1.5 1.5H7"/>',
    external: '<path d="M12 3.5h5.5V9M17.5 3.5L10 11"/><path d="M15 12.5V16a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V7.5A1.5 1.5 0 0 1 5.5 6H9"/>',
    check: '<path d="M4.5 11l4 4 8-9"/>',
    close: '<path d="M5.5 5.5l10 10M15.5 5.5l-10 10"/>',
    chevron: '<path d="M6 8.5l4.5 4.5 4.5-4.5"/>',
    plus: '<path d="M10.5 4.5v12M4.5 10.5h12"/>',
    clock: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="M10.5 6v4.5l3 2"/>',
    palette: '<path d="M10.5 3a7.5 7.5 0 0 0 0 15c1 0 1.5-.7 1.5-1.4 0-.9-.8-1.2-.8-2.1 0-.8.7-1.5 1.5-1.5h1.8A3.5 3.5 0 0 0 18 9.5C18 5.9 14.6 3 10.5 3z"/><circle cx="6.8" cy="10" r=".9"/><circle cx="9" cy="6.6" r=".9"/><circle cx="13" cy="6.8" r=".9"/>',
    branch: '<circle cx="5.5" cy="4.5" r="2"/><circle cx="5.5" cy="16.5" r="2"/><circle cx="15.5" cy="8.5" r="2"/><path d="M5.5 6.5v8M15.5 10.5c0 3-4 2.5-8.6 5.2"/>',
    deeper: '<circle cx="10.5" cy="12" r="6"/><circle cx="10.5" cy="12" r="2.2"/><path d="M10.5 1.5v7M8 6.5l2.5 2.5L13 6.5"/>',
    autopilot: '<path d="M17 8.5A7 7 0 0 0 4.3 7M4 12.5A7 7 0 0 0 16.7 14"/><path d="M4 3.5V7h3.5M17 17.5V14h-3.5"/><circle cx="10.5" cy="10.5" r="1.6"/>',
    article: '<rect x="4" y="2.5" width="13" height="16" rx="1.5"/><path d="M7 6.5h7M7 9.5h7M7 12.5h4.5"/>',
    help: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="M8.3 8.2a2.3 2.3 0 0 1 4.4.9c0 1.6-2.2 1.9-2.2 3.3"/><circle cx="10.5" cy="14.8" r=".5"/>',
    pause: '<path d="M8 5.5v10M13 5.5v10"/>',
    play: '<path d="M7 4.5l9 6-9 6z"/>',
    bell: '<path d="M5.5 14.5V9.5a5 5 0 0 1 10 0v5l1.5 1.5H4z"/><path d="M9 18.5h3"/>',
    spark: '<path d="M10.5 3v4M10.5 14v4M3 10.5h4M14 10.5h4M5.6 5.6l2.2 2.2M13.2 13.2l2.2 2.2M5.6 15.4l2.2-2.2M13.2 7.8l2.2-2.2"/>'
  };

  function sprite() {
    if (document.getElementById("pelican-icons")) return;
    var s = '<svg xmlns="http://www.w3.org/2000/svg" id="pelican-icons" aria-hidden="true" style="position:absolute;width:0;height:0;overflow:hidden">' +
      Object.keys(P).map(function (k) { return '<symbol id="i-' + k + '" viewBox="0 0 21 21">' + P[k] + "</symbol>"; }).join("") + "</svg>";
    (document.body || document.documentElement).insertAdjacentHTML("afterbegin", s);
  }
  /* Строкой — для разметки, которую собирает модуль радара. */
  function svg(name, cls) {
    sprite();
    return '<svg class="ic' + (cls ? " " + cls : "") + '" aria-hidden="true" focusable="false"><use href="#i-' + name + '"/></svg>';
  }

export default { svg: svg, sprite: sprite, names: Object.keys(P) };
