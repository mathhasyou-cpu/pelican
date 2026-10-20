/* Вход: герой с кольцами радара, поле запроса, примеры из задания, последние отчёты карточками. */
import { useState } from "react";
import { Go, useApp } from "../appctx.jsx";
import Active from "../components/Active.jsx";
import { Icon } from "../components/Icon.jsx";
import { RecentCards } from "../components/HistoryList.jsx";
import { reportsWord } from "../lib/format.js";
import T from "../lib/timeline.js";

/* Примеры запросов из задания: повтор готового запроса отдаёт отчёт сразу (кэш API). */
const EXAMPLES = ["перспективные решения в финтехе", "технологии в ИИ", "слабые сигналы в области кибербезопасности"];

/* Только тонкие кольца и один жёлтый блип у края — без спиц и россыпи точек. */
function HeroRings() {
  const a = -28 * Math.PI / 180, x = (270 * Math.cos(a)).toFixed(1), y = (270 * Math.sin(a)).toFixed(1);
  return (
    <svg className="hero-rings" viewBox="-310 -310 620 620" aria-hidden="true">
      {[90, 180, 270].map((r) => <circle key={r} r={r} />)}
      <circle className="halo" cx={x} cy={y} r="15" />
      <circle className="blip" cx={x} cy={y} r="6" />
    </svg>
  );
}

export default function Home() {
  const app = useApp();
  const [q, setQ] = useState("");
  const { hist } = app;
  return (
    <>
      <section className="hero">
        <HeroRings />
        <div className="hero-in">
          <p className="eyebrow">Автоматизированная система поиска слабых сигналов</p>
          <h1 className="display">Какое направление проверить на слабые сигналы?</h1>
          <p className="lead">АС ПЕЛИКАН читает научные работы и новости о раундах, пилотах и сделках, находит технологии на ранней стадии и раскладывает их на радаре по зрелости.</p>
          <form className="ask" role="search" onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 3) app.startJob(q.trim()); }}>
            <Icon name="search" />
            <input name="q" placeholder="Например, перспективные решения в финтехе" autoComplete="off" required minLength={3}
              aria-label="Направление для поиска сигналов" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn primary lg" type="submit" disabled={app.sending} aria-busy={app.sending}><Icon name="radar" /><span>{app.sending ? "Отправляю…" : "Найти сигналы"}</span></button>
          </form>
          <div className="examples">
            <span className="muted">Примеры из задания:</span>
            {EXAMPLES.map((x) => (
              <button key={x} className="chip pill" type="button" disabled={app.sending} onClick={() => app.startJob(x)}><Icon name="radar" />{x}</button>
            ))}
          </div>
          <p className="note"><Icon name="clock" /><span>Новый запрос идёт {T.runText(hist.items, hist.typical)}, готовый по тому же запросу открывается сразу. Радар заполняется на глазах, вкладку можно закрыть: ссылка сохранит место.</span></p>
        </div>
      </section>
      <Active />
      {hist.items.length ? (
        <section className="recent">
          <div className="sec-head">
            <h2 className="h2">Последние отчёты</h2>
            <Go to="history" className="btn sm"><Icon name="history" /><span>Вся история, {reportsWord(hist.total)}</span></Go>
          </div>
          <div className="rgrid">
            <RecentCards items={hist.items} />
          </div>
        </section>
      ) : null}
    </>
  );
}
