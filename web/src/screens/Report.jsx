/* Отчёт: сводка (запрос, прочитано источников, сигналы, уверенность выше 75 %, кандидаты,
   исключено), фильтр направлений, стол | радар, исключённое с причиной, карточка справа.
   Отчёт по имени: загрузка с сервера (в памяти вкладки — повторно мгновенно), затем экран. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Fail, Go, Loading, useApp } from "../appctx.jsx";
import Card from "../components/Card.jsx";
import Cited, { jumpToSource } from "../components/Cited.jsx";
import Hint from "../components/Hint.jsx";
import { LiveLine, Shortfall } from "../components/RunNotes.jsx";
import { Icon, Kbd, StageIcon } from "../components/Icon.jsx";
import Radar from "../components/Radar.jsx";
import A from "../lib/api.js";
import { candidatesOf, docURL, excludedKey, excludedSummary, fmtStarted, pct, placeOf, plural, pref, printURL, reduceMotion } from "../lib/format.js";
import R from "../lib/radar.js";
import { stageShort } from "../lib/stages.js";
import T from "../lib/timeline.js";

export default function Report({ name, view, sig, cut, facet }) {
  const [rep, setRep] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let live = true;
    A.report(name).then((r) => { if (live) setRep(r); }, (err) => {
      if (!live) return;
      setError(err.status === 404 ? { title: "Такого отчёта нет", text: "Ссылка устарела или набрана с ошибкой. Все готовые отчёты — в истории." }
        : { title: "Отчёт не открылся", text: err.message });
    });
    return () => { live = false; };
  }, [name]);
  if (error) return <Fail title={error.title} text={error.text} />;
  if (!rep) return <Loading text="Открываю отчёт" />;
  return <Result rep={rep} view={view} want={sig} cutWanted={cut} facetWanted={facet} />;
}

/* Адрес состояния отчёта (NFR-49): вид, выбранный сигнал, раскрытое исключённое, направление. */
export function reportHash(name, { mode, sig, cut, facet } = {}) {
  return "report=" + name + (mode === "radar" ? "&view=radar" : "") + (sig ? "&sig=" + encodeURIComponent(sig) : "")
    + (cut ? "&cut" : "") + (facet != null ? "&facet=" + facet : "");
}

function Result({ rep, view, want, cutWanted, facetWanted }) {
  const app = useApp();
  const thin = rep.thin || [], cut = rep.excluded || [], watch = rep.watch || [];
  const unassessed = rep.unassessed || [], categories = rep.categories || [];
  const map = useMemo(() => {
    const m = {};
    rep.signals.concat(thin, watch, cut).forEach((s) => { m[s.label] = s; });
    return m;
  }, [rep]);   // thin, watch и cut — части rep
  const sum = excludedSummary(cut);
  const confident = rep.signals.filter((s) => s.confidence > 0.75).length;
  // Число оценённых — из прогона: сумма списков занизила бы его на неоценённых (сбой оценки).
  const nCand = candidatesOf(rep);
  const nF = rep.facets.length;
  // Исключённое в порядке таблицы: классы по `EXCLUDED_ORDER`, затем вне топа, затем «наблюдать».
  const cutSorted = sum.flatMap((g) => cut.filter((x) => excludedKey(x) === g.key));
  /* Направление сигнала (Signal.facet); у отчётов до привязки его нет — такие сигналы в
     фильтре направлений не считаются и при выборе направления приглушаются. */
  const facetOf = (s) => { const f = R.facetOf(s); return R.known(f, nF) ? f : -1; };
  const perFacet = rep.facets.map((f, i) => rep.signals.filter((s) => facetOf(s) === i).length);
  const anyFacet = perFacet.some((n) => n > 0);
  const models = [...new Set((rep.models || []).map((m) => m.model))];

  const first = rep.signals[0] || thin[0] || cut[0];
  const [mode, setModeState] = useState(view === "radar" ? "radar" : "table");
  const [pinned, setPinnedState] = useState(facetWanted != null && facetWanted < rep.facets.length ? facetWanted : null);
  const [current, setCurrent] = useState(want && map[want] ? want : first ? first.label : null);
  const [cutOpen, setCutOpen] = useState(cutWanted || !!(want && map[want] && !rep.signals.some((s) => s.label === want)));
  const [hot, setHot] = useState(null);
  const radar = useRef(null), side = useRef(null), tool = useRef(null), cutreg = useRef(null);

  function setMode(m, remember) { setModeState(m); if (remember) pref("pelican-view", m); }
  function setPinned(f) { setPinnedState(f); if (radar.current) radar.current.pin(f); }
  function select(label, byUser) {
    setCurrent(label);
    if (radar.current) radar.current.select(label);
    if (byUser && window.innerWidth <= 900 && side.current) side.current.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }
  /* Ссылка [n] из строки списка: карточка справа перерисуется под кандидата, затем — к источнику. */
  function toSource(label, n) {
    select(label, false);
    setTimeout(() => jumpToSource(side.current, n), 0);
  }
  /* KPI ведут к списку: «кандидатов» и «исключено» раскрывают исключённое под столом. */
  function toCandidates(e, target) {
    e.preventDefault();
    setCutOpen(true);
    if (mode !== "table") setMode("table", false);
    setTimeout(() => { const el = target === "cut" ? cutreg.current : tool.current; if (el) el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }); }, 0);
  }
  const here = reportHash(rep.name, { mode, sig: current !== (first && first.label) ? current : null, cut: cutOpen, facet: pinned });
  function share() { app.copyLink(location.href.split("#")[0] + "#" + here); }
  /* Выбор живёт в адресе: replaceState не плодит шагов «назад» и не перерисовывает экран. */
  useEffect(() => {
    if (location.hash.slice(1) === here) return;
    try { history.replaceState(null, "", "#" + here); } catch { /* file:// в старых браузерах */ }
  }, [here]);

  // обработчики радара создаются один раз — им нужны свежие функции
  const handlers = useRef({});
  handlers.current = { select, setPinned, pinned };
  const radarOpts = useRef({
    key: true,
    onSelect: (label) => handlers.current.select(label, true),
    onHover: (label) => setHot(label),
    onFacet: (f) => handlers.current.setPinned(f),
  });

  useEffect(() => {
    app.setCommands((q) => [
      { group: "Этот отчёт", label: "Поделиться ссылкой на отчёт", icon: "link", run: share },
      { group: "Этот отчёт", label: "Сохранить PDF", icon: "print", run: () => app.openPrint(rep.name) },
      { group: "Этот отчёт", label: mode === "radar" ? "Показать столом" : "Показать радаром", icon: mode === "radar" ? "table" : "radar", run: () => setMode(mode === "radar" ? "table" : "radar", true) },
    ].concat(!q ? [] : rep.facets.map((f, i) => ({ group: "Направления", label: "Только «" + f + "»", hint: perFacet[i] + " в топе", icon: "radar", run: () => setPinned(i) }))));
  });

  /* ↑↓←→ листают сигналы; при раскрытом исключённом — и его, в порядке таблицы. С
     модификатором клавиша не наша: Alt+← — «назад» браузера. */
  useEffect(() => {
    function onKey(e) {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      if (/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName) || document.querySelector("dialog[open]")) return;
      const next = e.key === "ArrowDown" || e.key === "ArrowRight", prev = e.key === "ArrowUp" || e.key === "ArrowLeft";
      if (!next && !prev) return;
      let order = rep.signals.map((s) => s.label);
      if (cutOpen) order = order.concat(cutSorted.map((s) => s.label), thin.map((s) => s.label), watch.map((s) => s.label));
      const i = order.indexOf(current), n = next ? Math.min(order.length - 1, i + 1) : Math.max(0, i - 1);
      e.preventDefault();
      select(order[n], false);
      const tr = tool.current && tool.current.querySelector('tr[data-label="' + CSS.escape(order[n]) + '"]');
      if (tr && mode === "table") tr.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  /* Карточка липнет к окну, только если помещается в него целиком, — своей прокрутки у неё нет. */
  useLayoutEffect(() => {
    const el = side.current;
    if (!el) return undefined;
    const fit = () => el.classList.toggle("stick", el.offsetHeight < window.innerHeight - 92);
    fit();
    el.addEventListener("toggle", fit, true);   // раскрыли раздел карточки — пересчитать
    return () => el.removeEventListener("toggle", fit, true);
  }, [current]);

  const dim = (s) => pinned != null && facetOf(s) !== pinned;
  const s = current ? map[current] : null;
  const isSig = s && s.kind === "emerging" && !(s.relevance != null && s.relevance < 2) && !thin.some((x) => x.label === current);
  const fSel = s ? facetOf(s) : -1;
  const kpi = (n, label, opts = {}) => (
    <div key={label}>
      <dt>{label}{opts.hint ? <Hint k={opts.hint} right={opts.right} /> : null}</dt>
      <dd className="mono">
        {opts.sw ? <i className="sw" aria-hidden="true" /> : null}
        {opts.to ? <a href="#" onClick={(e) => toCandidates(e, opts.to)} title="Показать список">{n}</a> : n}
      </dd>
    </div>
  );

  return (
    <>
      <header className="rhead">
        <div className="rhead-main">
          <Go to="history" className="back"><Icon name="arrow-left" />История</Go>
          <h1 className="h1">{rep.query}</h1>
          <p className="meta">Отчёт от {fmtStarted(rep.started)}. Прочитано {rep.sources_processed} {plural(rep.sources_processed, "источник", "источника", "источников")} за {T.minutes(rep.seconds)}.</p>
        </div>
        <div className="actions">
          <button className="btn" type="button" onClick={share}><Icon name="link" />Поделиться</button>
          <a className="btn primary" href={printURL(rep.name)} target="_blank" rel="noopener"><Icon name="print" />Сохранить PDF</a>
        </div>
      </header>
      <dl className="kpis">
        {kpi(rep.signals.length, "сигналов в топе")}
        {kpi(confident, "с уверенностью выше 75 %", { sw: true, hint: "confident" })}
        {kpi(nCand, "кандидатов оценено", { to: "tool", hint: "candidates" })}
        {kpi(cut.length, "исключено с причиной", { to: "cut", hint: "excluded", right: true })}
      </dl>
      <dl className="kpis sub">
        {kpi(thin.length, "за пределами ТОП", { to: thin.length ? "cut" : null, hint: "thin" })}
        {kpi(categories.length, "снято как категория", { to: categories.length ? "cut" : null, hint: "categories" })}
        {kpi(rep.sources_processed, "источников обработано", { hint: "sources" })}
        {kpi(rep.works_found, "научных работ найдено")}
        {kpi(rep.headlines_seen, "новостей о раундах и пилотах")}
        {rep.history_runs ? kpi(rep.stable, "держатся в ≥ половине из " + rep.history_runs + " прошлых прогонов", { hint: "held", right: true }) : null}
      </dl>
      <LiveLine rep={rep} />
      <Shortfall rep={rep} />
      <div className="facetbar" role="group" aria-label="Направления запроса: наведите, чтобы подсветить сектор, нажмите, чтобы оставить только его"
        onMouseOut={(e) => { if (e.target.closest(".fchip") && radar.current) radar.current.focus(pinned); }}>
        <button className="fchip all" type="button" aria-pressed={pinned == null} onClick={() => setPinned(null)}>
          Все направления<span className="n">{rep.signals.length}</span>
        </button>
        {rep.facets.map((f, i) => (
          <button key={i} className="fchip" type="button" aria-pressed={pinned === i}
            onClick={() => setPinned(pinned === i ? null : i)} onMouseOver={() => radar.current && radar.current.focus(i)}>
            <b className="lt">{R.LETTERS.charAt(i)}</b>{f}{anyFacet ? <span className="n">{perFacet[i]}</span> : null}
          </button>
        ))}
      </div>
      <div className="main2">
        <section className="surface tool" ref={tool}>
          <div className="bar-top">
            <span className="switch" role="group" aria-label="Вид отчёта">
              <button type="button" aria-pressed={mode === "table"} onClick={() => setMode("table", true)}><Icon name="table" />Стол</button>
              <button type="button" aria-pressed={mode === "radar"} onClick={() => setMode("radar", true)}><Icon name="radar" />Радар</button>
            </span>
            <span className="hint">{mode === "radar" ? "Наведите на точку — подсказка, на сектор — подсветка" : <><Kbd>↑</Kbd><Kbd>↓</Kbd><Kbd>←</Kbd><Kbd>→</Kbd> листают сигналы</>}</span>
            <Go to="guide=radar" className="help-q" title="Как читать радар и стол" aria-label="Как читать радар и стол"><Icon name="help" /></Go>
          </div>
          <div hidden={mode !== "radar"}>
            <Radar opts={radarOpts.current} onReady={(r) => {
              radar.current = r; r.setData(rep); if (current) r.select(current); if (pinned != null) r.pin(pinned);
            }} />
            <ol className="index" onMouseLeave={() => radar.current && radar.current.hover(null)}>
              {rep.signals.map((x, k) => (
                <li key={x.label} className={(current === x.label ? "sel" : "") + (hot === x.label ? " hot" : "") + (dim(x) ? " dim" : "")}
                  onMouseOver={() => radar.current && radar.current.hover(x.label)} onClick={() => select(x.label, true)}>
                  <span className="n">{k + 1}</span><span className="t">{x.title}</span>
                  <span className="stg" title={stageShort(x.stage)}><StageIcon stage={x.stage} /></span>
                </li>
              ))}
            </ol>
          </div>
          <div className="reg" hidden={mode !== "table"}>
            <table>
              <thead><tr><th className="n">№</th><th>Технология</th><th className="st">Стадия</th><th className="co">Кто делает</th><th className="cf">Уверенность</th></tr></thead>
              <tbody>
                {rep.signals.map((x, k) => (
                  <tr key={x.label} data-label={x.label} tabIndex={0} className={(current === x.label ? "sel" : "") + (dim(x) ? " dim" : "")} onClick={() => select(x.label, true)} onKeyDown={(e) => activate(e, () => select(x.label, true))}>
                    <td className="n">{k + 1}</td>
                    <td><div className="ttl">{x.title}</div><div className="lab">{x.label}</div>
                      {/* BR-20: как в строке ТОП отчёта-документа (`page.py` _row) */}
                      {x.corroborated === false ? <div className="lab warn">пониженная доверенность</div> : null}</td>
                    <td className="st"><span className="stg"><StageIcon stage={x.stage} />{stageShort(x.stage)}</span></td>
                    <td className="co">{(x.companies || []).slice(0, 3).join(", ")}</td>
                    <td className="cf"><div><span className="mono">{pct(x.confidence)}</span><span className={"bar" + (x.confidence > 0.75 ? " hi" : "")}><i style={{ width: Math.round(x.confidence * 100) + "%" }} /></span></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="cutbar">
            <button type="button" aria-expanded={cutOpen} aria-controls="cutreg" onClick={() => setCutOpen(!cutOpen)}><Icon name="chevron" />Исключено {cut.length}</button>
            <span className="sum">{[
              sum.map((g) => g.label + " " + g.n).join(", "),
              thin.length ? "зарождающихся вне топа " + thin.length : "",
              watch.length ? "наблюдать " + watch.length : "",
              unassessed.length ? "оценка не удалась " + unassessed.length : "",
              categories.length ? "рыночных категорий " + categories.length : "",
            ].filter(Boolean).join("; ")}</span>
          </div>
          <div className="reg cutreg" id="cutreg" ref={cutreg} hidden={!cutOpen}>
            {cutOpen ? (
              <table>
                <thead><tr><th className="n" /><th>Технология</th><th className="st">Стадия</th><th>Почему исключено</th></tr></thead>
                <tbody>
                  {sum.map((g) => [
                    <tr key={"g" + g.key}><td colSpan={4} className="cutgroup">{g.label}, {g.n}</td></tr>,
                    ...cut.filter((x) => excludedKey(x) === g.key).map((x) => <CutRow key={x.label} x={x} why={x.why} current={current} select={select} toSource={toSource} />),
                  ])}
                  {thin.length ? [
                    <tr key="thin"><td colSpan={4} className="cutgroup">зарождающиеся, не вошедшие в топ-15 по уверенности, {thin.length}</td></tr>,
                    ...thin.map((x) => <CutRow key={x.label} x={x} why={"уверенность " + pct(x.confidence) + " — " + (x.why || "")} current={current} select={select} toSource={toSource} />),
                  ] : null}
                  {watch.length ? [
                    <tr key="watch"><td colSpan={4} className="cutgroup">зарождающееся — наблюдать (нет двух независимых подтверждений), {watch.length}</td></tr>,
                    ...watch.map((x) => <CutRow key={x.label} x={x} why={x.layer_reason || x.why} current={current} select={select} toSource={toSource} />),
                  ] : null}
                  {unassessed.length ? (
                    <tr><td colSpan={4} className="cutgroup">оценка зрелости не удалась (сбой модели), {unassessed.length}: {unassessed.join(", ")}</td></tr>
                  ) : null}
                  {categories.length ? (
                    <tr><td colSpan={4} className="cutgroup">снято как рыночная категория (название без слов сверх запроса): {categories.join(", ")}</td></tr>
                  ) : null}
                </tbody>
              </table>
            ) : null}
          </div>
        </section>
        <aside className="side-card" ref={side} aria-live="polite">
          {s ? (
            <div className={"sheet" + (isSig ? " signal" : "")}>
              <div className="sheet-top">
                <span className="mono">{placeOf(rep, s)}</span>
                {fSel >= 0 ? <span className="chip"><b className="lt">{R.LETTERS.charAt(fSel)}</b>{rep.facets[fSel]}</span> : null}
                <a className="btn sm" href={docURL(rep.name, s.label)} target="_blank" rel="noopener"><Icon name="print" />Открыть как документ</a>
              </div>
              <Card sig={s} fold runs={rep.history_runs} />
            </div>
          ) : (
            <div className="sheet"><p className="muted">В этом отчёте нет ни сигналов, ни исключённых кандидатов: поиск не нашёл, что оценивать. Попробуйте сформулировать направление шире.</p></div>
          )}
        </aside>
      </div>
      <footer className="foot">
        <details className="models">
          <summary>Модели: {models.join(", ")}</summary>
          <ul>{(rep.models || []).map((x, i) => <li key={i}><b className="mono">{x.model}</b> — {x.role}{x.endpoint ? <span className="muted"> ({x.endpoint})</span> : null}</li>)}</ul>
        </details>
        <Go to="guide=card">Как читать отчёт</Go>
      </footer>
    </>
  );
}

/* Строка-кнопка с клавиатуры (NFR-72): Enter и пробел — как щелчок; из ссылки внутри строки — нет. */
function activate(e, run) {
  if ((e.key !== "Enter" && e.key !== " ") || e.target !== e.currentTarget) return;
  e.preventDefault();
  run();
}

/* Строка исключённого; ссылка [n] в причине — выбрать кандидата и перейти к его источнику
   в карточке справа (`toSource`). */
function CutRow({ x, why, current, select, toSource }) {
  return (
    <tr className={"cutrow" + (current === x.label ? " sel" : "")} data-label={x.label} tabIndex={0} onClick={() => select(x.label, true)}
      onKeyDown={(e) => activate(e, () => select(x.label, true))}>
      <td className="n">—</td>
      <td><div className="ttl">{x.title}</div><div className="lab">{x.label}</div></td>
      <td className="st"><span className="stg"><StageIcon stage={x.stage} />{stageShort(x.stage)}</span></td>
      <td className="why"><Cited text={why} sources={x.sources} onRef={(n) => toSource(x.label, n)} /></td>
    </tr>
  );
}
