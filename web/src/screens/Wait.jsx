/* Ожидание: прогресс, радар с лучом, числа и лента вердиктов. Задание по номеру из адреса:
   готовое — сразу отчёт, иначе живой экран, который проигрывает события прогона (lib/live.js).
   Часы и полоса этапов идут на requestAnimationFrame прямой записью в DOM — перерисовывать
   экран 60 раз в секунду ради одной цифры незачем. */
import { useEffect, useRef, useState } from "react";
import { Fail, Go, Loading, useApp } from "../appctx.jsx";
import { Icon } from "../components/Icon.jsx";
import Radar from "../components/Radar.jsx";
import A from "../lib/api.js";
import { noRefs, plural, reduceMotion } from "../lib/format.js";
import { watch } from "../lib/live.js";
import R from "../lib/radar.js";
import T from "../lib/timeline.js";

/* Число со счётом вверх; при восстановлении (replay) — сразу. */
function Stat({ label, v }) {
  const el = useRef(null);
  useEffect(() => {
    if (!v || !el.current) return undefined;
    const n = v.n, node = el.current;
    if (v.replay || reduceMotion) { node.textContent = n; return undefined; }
    const t0 = performance.now();
    let raf = 0;
    const f = (now) => { const k = Math.min(1, (now - t0) / 700); node.textContent = Math.round(n * (1 - Math.pow(1 - k, 3))); if (k < 1) raf = requestAnimationFrame(f); };
    raf = requestAnimationFrame(f);
    return () => cancelAnimationFrame(raf);
  }, [v]);
  return (
    <div className={"stat" + (v ? " on" : "")}>
      <span className="v mono" ref={el}>—</span><span className="l">{label}</span><span className="s">{v ? v.sub : ""}</span>
    </div>
  );
}

export default function Wait({ id }) {
  const app = useApp();
  const [snap, setSnap] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let live = true;
    A.job(id).then((s) => {
      if (!live) return;
      if (s.status === "done" && s.report) {
        try { history.replaceState(null, "", "#report=" + s.report); } catch { /* ок */ }
        app.navTo("report=" + s.report, { view: "radar" });
        return;
      }
      T.setTypical(s.stage_typical, s.typical_run_s);
      setSnap(s);
    }, (err) => {
      if (!live) return;
      setError(err.status === 404 ? { title: "Такого запроса нет", text: "Ссылка устарела или набрана с ошибкой. Готовые отчёты — в истории." }
        : { title: "Запрос не открылся", text: err.message });
    });
    return () => { live = false; };
  }, [id]);   // снимок задания — один раз на номер
  if (error) return <Fail title={error.title} text={error.text} />;
  if (!snap) return <Loading text="Открываю запрос" />;
  return <Live snap={snap} />;
}

function Live({ snap }) {
  const app = useApp();
  const S = T.STAGES, typical = snap.typical_run_s || T.TOTAL;
  const radar = useRef(null);
  const clock = useRef(null), segs = useRef(null), progress = useRef(null), statusEl = useRef(null);
  // С первого кадра — по снимку: задание в очереди не должно мигнуть «идёт поиск».
  const [status, setStatus] = useState(snap.status === "running" ? "Идёт поиск сигналов" : "В очереди");
  const [cap, setCap] = useState("Полоса поделена по типичной длительности этапов — это не прогноз.");
  const [failed, setFailed] = useState(null);
  const [missing, setMissing] = useState(false);
  // Отмена (UX-02): идущий прогон останавливается на границе этапа — до неё «останавливается».
  const [stopping, setStopping] = useState(!!snap.cancel);
  const [cancelled, setCancelled] = useState(false);
  const [stage, setStage] = useState({ cur: 0, n: 1, of: S.length, name: S[0].name, at: {} });
  const [stagesOpen, setStagesOpen] = useState(false);
  const [facets, setFacets] = useState(null);
  const [stats, setStats] = useState({});
  const [feed, setFeed] = useState([]);
  const [feedAll, setFeedAll] = useState(false);
  const [tally, setTally] = useState({ seen: 0, sig: 0, cut: 0, total: 0 });
  const [rstatus, setRstatus] = useState("Сектора появятся, когда модель раскроет запрос в направления");

  useEffect(() => {
    app.setCommands(() => [{ group: "Этот прогон", label: "Скопировать ссылку на ожидание", icon: "link", run: share }]);
  });
  function share() { app.copyLink(location.href.split("#")[0] + "#job=" + snap.id, "Ссылка на ожидание скопирована"); }
  // Подтверждение — своё окно, а не `confirm` браузера: там «Отмена» отменяла отмену (UX-16).
  const ask = useRef(null);
  function cancel() { ask.current.showModal(); }
  function confirmCancel() {
    ask.current.close();
    setStopping(true);
    A.cancel(snap.id).then((d) => {
      if (d.status !== "cancelled" && d.status !== "running") setStopping(false);   // успел закончиться
    }, (err) => { setStopping(false); app.toast(err.message, "close"); });
  }

  useEffect(() => {
    const r = radar.current;
    r.sweep(true);
    const player = watch(snap.id);
    // `offline` — статус «нет связи» важнее «дольше обычного»: часы его не перебивают (PQA-019).
    let running = snap.status === "running", cur = 0, curAt = 0, wasOver = false, stopped = false, offline = false;
    // Этапов от исполнителя нет (событие `progress`): этап на полосе — оценка по времени.
    let rough = false;

    function paint(t) {
      if (!running || !clock.current) return;
      clock.current.textContent = T.clock(t);
      if (rough) {
        let k = 0;
        while (k + 1 < S.length && S[k + 1].at <= t) k++;
        if (k !== cur) { cur = k; curAt = S[k].at; setStage((s) => ({ ...s, cur: k, n: k + 1 })); }
      }
      [...segs.current.children].forEach((el, k) => {
        const a = S[k].at, end = k + 1 < S.length ? S[k + 1].at : T.TOTAL;
        const f = k < cur ? 1 : k > cur ? 0 : Math.min(1, (t - curAt) / (end - a));
        el.firstChild.style.width = (f * 100).toFixed(1) + "%";
        el.classList.toggle("active", k === cur);
      });
      const over = t > typical;
      if (over !== wasOver) {
        wasOver = over; progress.current.classList.toggle("over", over);
        if (!offline) statusEl.current.classList.toggle("over", over);
      }
      if (over) {
        setCap("Дольше обычного на " + T.clock(t - typical) + ": так бывает, когда в живом поиске много источников.");
        if (!offline) setStatus("Идёт дольше обычного");
      }
    }
    function queued(s) {
      const mins = Math.max(1, Math.round((s.eta_start_s || 0) / 60));
      // `position` включает идущий, только если он есть (`running_ahead`); снимок без поля —
      // старый сервер, там идущий считался всегда.
      const run = s.running_ahead !== false;
      const n = Math.max((s.position || 0) - (run ? 1 : 0), 0);
      const waiting = n ? "впереди " + n + " " + plural(n, "запрос", "запроса", "запросов") : "";
      setStatus("В очереди: " + (
        run ? (n ? waiting + " и идущий, " : "впереди идущий запрос, ") + "начнётся примерно через " + mins + " мин"
          : n ? waiting + ", начнётся примерно через " + mins + " мин"
            : "впереди никого, начнётся в ближайшие секунды"));
      if (clock.current) clock.current.textContent = "0:00";
      setCap("Модель одна, запросы идут по очереди. Вкладку можно закрыть: ссылка сохранит место.");
    }
    if (!running) queued(snap);

    player.on("queued", (e) => queued(e.snap));
    player.on("offline", () => { offline = true; setStatus("Нет связи с сервером, повторяю"); statusEl.current.classList.add("over"); });
    // Связь вернулась — статус тот, что был бы без обрыва: «дольше обычного» сохраняет штриховку.
    player.on("online", () => {
      offline = false;
      statusEl.current.classList.toggle("over", wasOver);
      setStatus(!running ? "В очереди" : wasOver ? "Идёт дольше обычного" : "Идёт поиск сигналов");
    });
    function start() {
      if (running) return;
      running = true;
      setStatus("Идёт поиск сигналов");
      setCap("Полоса поделена по типичной длительности этапов на последних прогонах — это не прогноз.");
    }
    player.on("running", start);
    player.on("stage", (e) => {
      start();
      rough = false;
      cur = e.n - 1; curAt = e.at;
      setStage((s) => ({ cur, n: e.n, of: e.of, name: e.name, at: { ...s.at, [e.id]: e.at }, rough: false }));
    });
    /* Строки прогресса вместо этапов: текст — последняя строка («581 с · свожу 69 кандидатов…»),
       полоса — по типичному времени этапов, и подпись говорит об этом прямо. */
    player.on("progress", (e) => {
      start();
      rough = true;
      const last = String(e.lines[e.lines.length - 1] || "").replace(/^\s*\d+\s*с\s*·\s*/, "");
      setStage((s) => ({ ...s, rough: true, name: last || s.name }));
      // «Дольше обычного» важнее — его не перебивать
      if (!wasOver) setCap("Подробных этапов нет: исполнитель стенда пишет только строки прогресса. Текст — последняя из них, этап на полосе — оценка по типичному времени.");
    });
    player.on("facets", (e) => {
      r.setFacets(e.facets_ru);
      setRstatus("Сектора — направления запроса; кандидаты появятся после сведения потоков");
      setFacets({ names: e.facets_ru, submarkets: e.submarkets || [] });
    });
    player.on("counts", (e) => {
      const next = {};
      if (e.works != null) next.works = { n: e.works, sub: "в " + e.works_named + " названа технология", replay: e.replay };
      if (e.headlines != null) next.head = { n: e.headlines, sub: e.directions + " " + plural(e.directions, "направление", "направления", "направлений"), replay: e.replay };
      if (e.candidates != null) {
        next.cand = { n: e.candidates, sub: "снято как категория: " + e.categories_removed, replay: e.replay };
        setTally((t) => ({ ...t, total: e.candidates }));
        r.scatter(e.candidates);
        setRstatus(e.candidates + " кандидатов у края: по вердикту каждый встанет в пояс своей стадии или уйдёт за круг");
      }
      setStats((s) => ({ ...s, ...next }));
    });
    player.on("verdict", (e) => {
      const sig = e.kind === "emerging";
      setTally((t) => ({ ...t, seen: t.seen + 1, sig: t.sig + (sig ? 1 : 0), cut: t.cut + (sig ? 0 : 1) }));
      r.place(e.label, { facet: e.facet, stage: e.stage, kind: e.kind, kind_label: e.kind_label, title: e.title, sources: e.sources, why: e.why });
      setFeed((f) => [{ label: e.label, title: e.title, why: e.why, sig, kindLabel: e.kind_label, fresh: !e.replay }, ...f]);
    });
    player.on("relevance", (e) => {
      if (e.relevance < 2) { r.outside(e.label); setTally((t) => ({ ...t, sig: t.sig - 1, cut: t.cut + 1 })); }
    });
    player.on("top", (e) => {
      r.rank(e.labels, e.confidence);
      setRstatus("Топ построен: номер — место по уверенности; дальше патенты и карточки");
    });
    /* Русские названия приходят под конец (карточки и перевод исключённых): ярлык в ленте и на
       радаре подменяется переводом, а не выдумывается заранее. */
    function retitle(titles) {
      Object.keys(titles).forEach((label) => { const t = titles[label]; if (t && t !== label && r.dots[label]) r.dots[label].title = t; });
      setFeed((f) => f.map((x) => (titles[x.label] && titles[x.label] !== x.label ? { ...x, title: titles[x.label] } : x)));
    }
    player.on("card", (e) => retitle({ [e.label]: e.title }));
    player.on("titles", (e) => retitle(e.titles || {}));
    player.on("done", (e) => {
      if (!e.replay) app.toast("Отчёт готов", "check");
      app.loadHistory().catch(() => {});
      app.navTo("report=" + e.report, { view: "radar" });
    });
    player.on("failed", (e) => {
      running = true; paint(player.lastAt || 0); running = false;   // часы и полоса — где прогон остановился
      statusEl.current.classList.add("over"); progress.current.classList.add("over");
      setStatus("Запрос не удался");
      setFailed({ error: e.error || "Прогон остановился на внутренней ошибке. Повторите запрос.", detail: e.detail });
      setRstatus("Прогон остановлен: отчёта по нему не будет");
      r.sweep(false);
    });
    player.on("cancelled", () => {
      running = true; paint(player.lastAt || 0); running = false;
      setStatus("Запрос отменён");
      setCancelled(true); setStopping(false);
      setCap("Запрос снят автором — отчёта по нему не будет.");
      setRstatus("Прогон остановлен: отчёта по нему не будет");
      r.sweep(false);
    });
    player.on("missing", () => setMissing(true));
    (function loop() {
      if (stopped) return;
      paint(player.now());
      requestAnimationFrame(loop);
    })();
    player.play();
    return () => { stopped = true; player.pause(); };
  }, [snap.id]);   // прогон подписывается один раз на экран

  if (missing) return <Fail title="Такого запроса нет" text="Задание пропало из очереди. Задайте запрос заново." />;
  return (
    <>
      <header className="split-head">
        <div>
          <p className="status" ref={statusEl}><span className="pulse" /><span>{status}</span></p>
          <h1 className="h1">{snap.query}</h1>
        </div>
        <div className="head-actions">
          {cancelled ? <Go to="" className="btn sm"><Icon name="plus" />Новый запрос</Go> : (
            <>
              <button className="btn sm" type="button" onClick={share}><Icon name="link" />Ссылка на ожидание</button>
              {!failed ? (
                <button className="btn sm quiet" type="button" onClick={cancel} disabled={stopping} aria-busy={stopping}
                  title="Ожидающий уходит из очереди сразу, идущий останавливается на границе этапа">
                  <Icon name="close" />{stopping ? "Останавливается…" : "Отменить"}
                </button>
              ) : null}
              <dialog className="ask-dlg" ref={ask} aria-labelledby="ask-dlg-h">
                <h2 id="ask-dlg-h" className="h3">Отменить запрос «{snap.query}»?</h2>
                <p className="muted small">Ожидающий уходит из очереди сразу, идущий остановится на границе этапа. Отчёта по нему не будет.</p>
                <div className="ask-dlg-btns">
                  <button className="btn sm" type="button" autoFocus onClick={() => ask.current.close()}>Продолжить ждать</button>
                  <button className="btn sm primary" type="button" onClick={confirmCancel}>Отменить запрос</button>
                </div>
              </dialog>
            </>
          )}
        </div>
      </header>
      <section className="surface progress" ref={progress} aria-live="polite">
        <div className="prow">
          <div className="pclock"><span className="el mono" ref={clock}>0:00</span><span className="of">прошло, обычно около {Math.round(typical / 60)} мин</span></div>
          <div className="pstage"><span className="k">{stage.rough ? "примерно этап " + stage.n + " из " + stage.of + " (по времени)" : "этап " + stage.n + " из " + stage.of}</span><b>{stage.name}</b></div>
        </div>
        <div className="segs" ref={segs}>
          {S.map((s, k) => {
            const end = k + 1 < S.length ? S[k + 1].at : T.TOTAL;
            return <i key={s.id} style={{ flexGrow: end - s.at }} title={s.name}><b /></i>;
          })}
        </div>
        <div className="pcap">
          <span id="pcap">{failed ? null : cap}</span>
          {failed ? (
            <>
              <span className="perr">{failed.error}</span>
              <button className="linkbtn" type="button" onClick={() => app.startJob(snap.query)}><Icon name="arrow-right" />Повторить запрос</button>
              {failed.detail ? <details className="perr-more"><summary>Подробности</summary><code>{failed.detail}</code></details> : null}
            </>
          ) : null}
          <button className="linkbtn" type="button" aria-expanded={stagesOpen} aria-controls="stages" onClick={() => setStagesOpen(!stagesOpen)}>Все этапы<Icon name="chevron" /></button>
        </div>
        <ol className="stages" id="stages" hidden={!stagesOpen}>
          {S.map((s, k) => (
            <li key={s.id} className={(k < stage.cur ? "done" : "") + (k === stage.cur ? " active" : "")}>
              <span className={"dot" + (k < stage.cur ? " done" : "")} /><span className="nm">{s.name}</span>
              <span className="at">{stage.at[s.id] != null ? T.clock(stage.at[s.id]) : ""}</span>
            </li>
          ))}
        </ol>
      </section>
      <div className="bento">
        <section className="surface radar-tile">
          <div className="tile-head">
            <h2>Радар <Go to="guide=radar" className="help-q" title="Как читать радар" aria-label="Как читать радар"><Icon name="help" /></Go></h2>
            <span className="hint">{rstatus}</span>
          </div>
          <Radar opts={{ key: true }} onReady={(r) => { radar.current = r; }} />
        </section>
        <div className="side">
          <div className="stats">
            <Stat label="работ в корпусе" v={stats.works} />
            <Stat label="заголовков о раундах и пилотах" v={stats.head} />
            <Stat label="кандидатов к оценке" v={stats.cand} />
          </div>
          <section className="surface">
            <div className="tile-head"><h2>Направления запроса</h2></div>
            {facets ? (
              <div id="facets">
                <div className="chips">{facets.names.map((x, i) => <span className="chip" key={i}><b className="lt">{R.LETTERS.charAt(i)}</b>{x}</span>)}</div>
                {facets.submarkets.length ? <p className="sub">Рынки: {facets.submarkets.join(", ")}</p> : null}
              </div>
            ) : <div id="facets" className="placeholder">Модель раскрывает запрос в направления…</div>}
          </section>
          <section className="surface">
            <div className="tile-head">
              <h2>Оценка зрелости</h2>
              <span className="muted mono small">{tally.seen ? tally.seen + (tally.total ? " из " + tally.total : "") + ", сигналов " + tally.sig + ", снято " + tally.cut : ""}</span>
            </div>
            {!feed.length ? <p className="placeholder">Вердикты пойдут после сведения кандидатов: каждый кандидат встанет в пояс своей стадии или уйдёт за круг.</p> : null}
            <ul className={"feed" + (feedAll ? " all" : "")} id="ticker">
              {/* без своей прокрутки: шесть последних, остальные — по кнопке (.feed:not(.all) в app.css) */}
              {feed.map((x) => (
                <li key={x.label} className={(x.fresh ? "fresh " : "") + (x.sig ? "sig" : "cut")}>
                  <span className="fi"><Icon name={x.sig ? "check" : "close"} /></span>
                  <div><b>{x.title}</b><span className="why">{noRefs(x.why)}</span></div>
                  <span className={"stamp " + (x.sig ? "signal" : "cut")}>{x.sig ? "сигнал" : x.kindLabel}</span>
                </li>
              ))}
            </ul>
            {feed.length > 6 ? (
              <button className="linkbtn feedmore" type="button" aria-expanded={feedAll} aria-controls="ticker" onClick={() => setFeedAll(!feedAll)}>
                {feedAll ? "Свернуть до шести последних" : "Показать все " + feed.length}<Icon name="chevron" />
              </button>
            ) : null}
          </section>
        </div>
      </div>
    </>
  );
}
