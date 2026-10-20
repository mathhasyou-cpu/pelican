/* Текст со ссылками на источники карточки: «… [1, 3].» → номера-ссылки. Наведение — название
   источника (ТЗ: проверяемость), клик — `onRef(n)`: карточка раскрывает «Источники» и ведёт
   к строке. Номер без такого источника остаётся текстом: ссылка в никуда хуже, чем никакой. */
import { fmtDate, reduceMotion } from "../lib/format.js";

const REF = /\[([\d,\s]+)\]/g;

function label(s) {
  return [s.title, [s.publisher, fmtDate(s.date)].filter(Boolean).join(", ")].filter(Boolean).join(" — ");
}

export default function Cited({ text, sources, onRef }) {
  if (!text) return null;
  const byN = {};
  (sources || []).forEach((s) => { byN[s.n] = s; });
  const out = [];
  let last = 0, m;
  REF.lastIndex = 0;
  while ((m = REF.exec(text))) {
    out.push(text.slice(last, m.index));
    const nums = m[1].split(",").map((x) => parseInt(x, 10)).filter((n) => !isNaN(n));
    out.push(
      <span className="refs" key={m.index}>
        [{nums.map((n, i) => (
          <span key={n}>
            {i ? ", " : ""}
            {byN[n] ? (
              <a href={"#src-" + n} className="ref" title={label(byN[n])} aria-label={"Источник " + n + ": " + label(byN[n])}
                onClick={(e) => { e.preventDefault(); if (onRef) onRef(n); }}>{n}</a>
            ) : n}
          </span>
        ))}]
      </span>,
    );
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

/* Перейти к источнику `n` внутри узла карточки: раскрыть свёрнутый раздел, прокрутить,
   подсветить. Работает и в списке, и в таблице источников (`data-src`). */
export function jumpToSource(root, n) {
  const el = root && root.querySelector('[data-src="' + n + '"]');
  if (!el) return;
  const fold = el.closest("details");
  if (fold) fold.open = true;
  if (el.scrollIntoView) el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth;   // перезапуск анимации при повторном клике
  el.classList.add("flash");
}
