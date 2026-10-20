/* История запросов: полный список (`GET /api/history`), поиск, группы по месяцам; у строки —
   «открыть» и «отчёт для печати». RecentCards — последние отчёты карточками для входа.
   Всё, что открывает отчёт, — ссылки, а не кнопки: новая вкладка, Ctrl-клик, «копировать адрес». */
import { useEffect, useRef, useState } from "react";
import { Go } from "../appctx.jsx";
import { fmtStarted, plural, printURL } from "../lib/format.js";
import T from "../lib/timeline.js";
import { Icon } from "./Icon.jsx";

const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
/* Месяц — по часам читателя, как дата строки (`fmtStarted`): отчёт около полуночи UTC иначе
   попадал в соседний месяц. */
export function monthOf(iso) {
  const d = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso || "") ? new Date(iso) : null;
  if (d && !isNaN(d)) return MONTHS[d.getMonth()] + " " + d.getFullYear();
  const m = /^(\d{4})-(\d{2})/.exec(iso || "");
  return m ? MONTHS[+m[2] - 1] + " " + m[1] : "";
}
/* «/» — только когда фокус не в поле ввода и палитра закрыта. */
function typing() {
  const el = document.activeElement;
  return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
}
function counts(r) { return r.signals + " " + plural(r.signals, "сигнал", "сигнала", "сигналов") + ", " + r.excluded + " снято"; }
function newest(items) { return items.slice().sort((a, b) => (a.started < b.started ? 1 : -1)); }

function PrintLink({ r, className = "btn sm quiet icon" }) {
  return (
    <a className={className} href={printURL(r.name)} target="_blank" rel="noopener" aria-label="Отчёт для печати и PDF" title="Отчёт для печати и PDF">
      <Icon name="print" />
    </a>
  );
}

function Row({ r }) {
  const to = "report=" + r.name;
  return (
    <li>
      <Go to={to} className="q">{r.query}</Go>
      <span className="d mono">{fmtStarted(r.started)}</span>
      <span className="c">{counts(r)}</span>
      <span className="t mono" title="длительность прогона">{Math.round(r.seconds / 60)} мин</span>
      <span className="a">
        <Go to={to} className="btn sm quiet" tabIndex={-1}><Icon name="arrow-right" />открыть</Go>
        <PrintLink r={r} />
      </span>
    </li>
  );
}

export default function HistoryList({ items, typical }) {
  const [q, setQ] = useState("");
  const [shownQ, setShownQ] = useState("");
  const input = useRef(null);
  useEffect(() => { const t = setTimeout(() => setShownQ(q), 140); return () => clearTimeout(t); }, [q]);
  useEffect(() => {
    input.current.focus();
    // «/» ставит курсор в поиск
    function onKey(e) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || typing() || document.querySelector("dialog[open]")) return;
      e.preventDefault(); input.current.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const ql = shownQ.trim().toLowerCase();
  const f = newest(items).filter((r) => !ql || r.query.toLowerCase().indexOf(ql) >= 0);
  const groups = [];
  let cur = null;
  f.forEach((r) => { const m = monthOf(r.started); if (!cur || cur.m !== m) groups.push((cur = { m, rows: [] })); cur.rows.push(r); });
  return (
    <>
      <label className="hsearch">
        <Icon name="search" />
        <input ref={input} type="search" placeholder="Найти запрос" aria-label="Поиск по истории запросов" value={q} onChange={(e) => setQ(e.target.value)} />
        <kbd className="kbd" title="Клавиша / ставит курсор в поиск">/</kbd>
      </label>
      <div className="hlist" aria-live="polite">
        {!f.length ? <p className="empty">Такого запроса в истории нет. Его можно задать на главной: новый отчёт будет готов за {T.runMinutes(items, typical)}.</p> : null}
        {groups.map((g) => (
          <section className="hgroup" key={g.m}>
            <h3>{g.m} <span className="muted">{g.rows.length}</span></h3>
            <ul className="hrows">{g.rows.map((r) => <Row key={r.name} r={r} />)}</ul>
          </section>
        ))}
      </div>
    </>
  );
}

/* Последние отчёты карточками (вход): запрос, дата, три числа, печать. */
export function RecentCards({ items, limit = 6 }) {
  return newest(items).slice(0, limit).map((r) => (
    <article className="rcard" key={r.name}>
      <div className="rtop">
        <span className="rdate mono">{fmtStarted(r.started)}</span>
        <PrintLink r={r} className="btn sm quiet icon rprint" />
      </div>
      <Go to={"report=" + r.name} className="rq">{r.query}</Go>
      <dl className="rstats">
        <div><dt>сигналов</dt><dd className="mono">{r.signals}</dd></div>
        <div><dt>снято</dt><dd className="mono">{r.excluded}</dd></div>
        <div><dt>минут</dt><dd className="mono">{Math.round(r.seconds / 60)}</dd></div>
      </dl>
    </article>
  ));
}
