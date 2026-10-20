/* АС ПЕЛИКАН — приложение. Маршруты в адресе: #job=<номер задания>,
   #report=<имя>[&view=radar|table][&sig=<ярлык>][&cut][&facet=<номер>], #history, #guide[=раздел]; пусто — вход.
   Данные — `pelican.api` на том же адресе (/api/…). Стиль «Прибор» — styles/system.css. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { AppCtx } from "./appctx.jsx";
import A from "./lib/api.js";
import { MAC, fmtStarted, pref, printURL, reduceMotion } from "./lib/format.js";
import T from "./lib/timeline.js";
import CommandPalette from "./components/CommandPalette.jsx";
import { GlossaryProvider } from "./components/Hint.jsx";
import { Icon, Kbd } from "./components/Icon.jsx";
import Splash from "./components/Splash.jsx";
import Home from "./screens/Home.jsx";
import History from "./screens/History.jsx";
import Wait from "./screens/Wait.jsx";
import Report from "./screens/Report.jsx";
import Guide from "./screens/Guide.jsx";

function hashNow() { return location.hash.slice(1); }
function param(h, p) { return decodeURIComponent(h.slice(p.length).split("&")[0]); }

/* Тема: системная по умолчанию, кнопка переключает и запоминает; ?theme= применяется и
   запоминается ещё в index.html (до первой отрисовки) — здесь та же логика для страниц без
   него (тесты). */
function initialTheme() {
  const q = /[?&]theme=(light|dark)/.exec(location.search);
  if (q) pref("pelican-theme", q[1]);
  const saved = q ? q[1] : pref("pelican-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
}
function isDark() {
  const c = document.documentElement.getAttribute("data-theme");
  return c ? c === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
}
function isGpb() { return document.documentElement.getAttribute("data-brand") === "gpb"; }

export default function App() {
  const [hash, setHash] = useState(hashNow);
  const [hist, setHist] = useState({ items: [], total: 0, loaded: false });
  const [dark, setDark] = useState(() => { initialTheme(); return isDark(); });
  const [gpb, setGpb] = useState(isGpb);
  const [toastMsg, setToast] = useState(null);
  const commands = useRef(null);   // команды текущего экрана для палитры
  const nextOpts = useRef({});      // вид отчёта, с которым его открыть (из запроса — радар)
  const palette = useRef(null);
  const booted = useRef(false);
  const asking = useRef(false);     // синхронный замок от двойной отправки
  const [sending, setSending] = useState(false);   // он же — для кнопок (NFR-16)

  const loadHistory = useCallback(() => A.history().then((d) => { setHist({ items: d.items, total: d.total, typical: d.typical_run_s, loaded: true }); return d.items; }), []);
  useEffect(() => { loadHistory().catch(() => { /* без истории работают вход и отчёты по ссылке */ }); }, [loadHistory]);

  /* Смена экрана — плавным переходом (View Transitions), где он есть; первый показ и «меньше движения» — сразу. */
  const show = useCallback((h) => {
    if (!booted.current || reduceMotion || !document.startViewTransition) { booted.current = true; setHash(h); return; }
    document.startViewTransition(() => flushSync(() => setHash(h)));
  }, []);
  useEffect(() => {
    booted.current = true;
    const onPop = () => show(hashNow());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [show]);
  useEffect(() => { commands.current = null; window.scrollTo(0, 0); }, [hash]);

  const navTo = useCallback((h, opts) => {
    nextOpts.current = opts || {};
    if (hashNow() !== h) {
      const url = h ? "#" + h : location.pathname + location.search;
      try { (opts && opts.replace ? history.replaceState : history.pushState).call(history, null, "", url); } catch { location.hash = h; }
    }
    show(h);
  }, [show]);

  const toast = useCallback((text, icon) => setToast({ text, icon: icon || "check", id: Date.now() }), []);
  useEffect(() => {
    if (!toastMsg) return undefined;
    const t = setTimeout(() => setToast((m) => (m && m.id === toastMsg.id ? { ...m, leaving: true } : m)), 2400);
    const u = setTimeout(() => setToast((m) => (m && m.id === toastMsg.id ? null : m)), 2800);
    return () => { clearTimeout(t); clearTimeout(u); };
  }, [toastMsg]);

  /* Поделиться: ссылка в буфер; без Clipboard API (file://, http) — запасные пути. */
  const copyLink = useCallback((url, done) => {
    done = done || "Ссылка на отчёт скопирована";
    function fallback() {
      const ta = document.createElement("textarea");
      ta.value = url; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      ta.remove();
      if (ok) toast(done, "link"); else window.prompt("Ссылка", url);
    }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(url).then(() => toast(done, "link"), fallback);
    else fallback();
  }, [toast]);

  const openReport = useCallback((item, o) => {
    o = o || {};
    navTo("report=" + item.name + (o.sig ? "&sig=" + encodeURIComponent(o.sig) : ""), { view: o.view });
  }, [navTo]);
  const openPrint = useCallback((name) => { window.open(printURL(name), "_blank", "noopener"); }, []);

  /* Новый запрос: готовый отчёт по тому же запросу открывается сразу, иначе — ожидание
     задания (в очереди или в работе). Отказ API (короткий запрос, лимит адреса) — тостом. */
  const startJob = useCallback((q) => {
    if (asking.current) return;
    asking.current = true;
    setSending(true);
    A.ask(q).then((d) => {
      asking.current = false;
      setSending(false);
      if (d.cached) { toast("По этому запросу уже есть отчёт — открываю его", "radar"); openReport({ name: d.report }, { view: "radar" }); return; }
      if (d.status === "done" && d.report) { openReport({ name: d.report }, { view: "radar" }); return; }
      toast("Запрос принят, задание " + d.id, "check");
      navTo("job=" + d.id);
    }, (err) => {
      asking.current = false; setSending(false); toast(err.message, "close");
      // 429: свои задания — в блоке «Сейчас в работе» (UX-03)
      if (err.status === 429) { const el = document.getElementById("active"); if (el) el.scrollIntoView({ block: "center" }); }
    });
  }, [navTo, openReport, toast]);

  const toggleTheme = useCallback(() => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    pref("pelican-theme", next);
    setDark(next === "dark");
  }, []);
  const toggleBrand = useCallback(() => {
    const next = isGpb() ? "pelican" : "gpb";
    if (next === "gpb") document.documentElement.setAttribute("data-brand", "gpb"); else document.documentElement.removeAttribute("data-brand");
    pref("pelican-brand", next);
    setGpb(next === "gpb");
  }, []);
  const brandLabel = gpb ? "Оформление: АС ПЕЛИКАН" : "Оформление: Газпромбанк";

  const sorted = useMemo(() => hist.items.slice().sort((a, b) => (a.started < b.started ? 1 : -1)), [hist.items]);
  /* Палитра: действия экрана, переходы, отчёты, новый запрос. */
  const items = (q) => {
    const ql = q.toLowerCase(), out = [];
    const hit = (s) => !ql || s.toLowerCase().indexOf(ql) >= 0;
    (commands.current ? commands.current(ql) : []).forEach((c) => { if (hit(c.label)) out.push(c); });
    [{ group: "Переходы", label: "Новый запрос", icon: "plus", href: "#", run: () => navTo("") },
      { group: "Переходы", label: "История запросов", icon: "history", href: "#history", run: () => navTo("history") },
      { group: "Переходы", label: "Руководство", icon: "help", href: "#guide", run: () => navTo("guide") },
    ].concat(ql ? [
      { group: "Оформление", label: dark ? "Светлая тема" : "Тёмная тема", icon: dark ? "sun" : "moon", run: toggleTheme },
      { group: "Оформление", label: brandLabel, icon: "palette", run: toggleBrand },
    ] : []).forEach((c) => { if (hit(c.label)) out.push(c); });
    // коротко без ввода, чтобы палитра помещалась без прокрутки
    sorted.filter((r) => hit(r.query)).slice(0, ql ? 6 : 3).forEach((r) => {
      out.push({ group: ql ? "Отчёты" : "Последние отчёты", label: r.query, hint: fmtStarted(r.started), icon: "radar", href: "#report=" + r.name, run: () => openReport(r) });
    });
    if (q.length >= 3) out.push({ group: "Новый запрос", label: "Найти сигналы: " + q, hint: Math.round(T.typicalRun(hist.items, hist.typical) / 60) + " мин", icon: "search", run: () => startJob(q) });
    return out;
  };

  const app = {
    hist, sorted, loadHistory, navTo, openReport, openPrint, startJob, sending, toast, copyLink,
    setCommands: (fn) => { commands.current = fn; }, nextOpts, mac: MAC,
  };

  let screen, nav = null;
  const h = hash;
  if (/^report=/.test(h)) {
    const vm = /[&?]view=(radar|table)/.exec(h);
    const sm = /[&?]sig=([^&]+)/.exec(h);
    const fm = /[&?]facet=(\d+)/.exec(h);
    screen = <Report key={"r" + param(h, "report=")} name={param(h, "report=")} sig={sm ? decodeURIComponent(sm[1]) : null}
      view={vm ? vm[1] : nextOpts.current.view || pref("pelican-view") || "table"} cut={/[&?]cut(&|$)/.test(h)}
      facet={fm ? parseInt(fm[1], 10) : null} />;
  } else if (/^job=\d+/.test(h)) {
    screen = <Wait key={"j" + param(h, "job=")} id={parseInt(param(h, "job="), 10)} />;
  } else if (h === "history") {
    nav = "history"; screen = <History key="h" />;
  } else if (/^guide(=|$)/.test(h)) {
    nav = "guide"; screen = <Guide key="g" section={h.indexOf("=") > 0 ? param(h, "guide=") : null} />;
  } else {
    nav = "enter"; screen = <Home key="e" />;
  }
  const link = (k, target) => ({
    href: target ? "#" + target : "#",
    "aria-current": nav === k ? "page" : undefined,
    onClick: (e) => { e.preventDefault(); navTo(target); },
  });

  return (
    <AppCtx.Provider value={app}>
      <Splash />
      <header className="topbar">
        <div className="app">
          <a className="brand" title="На главную" {...link(null, "")}><span className="wm">АС ПЕЛИКАН</span></a>
          <nav className="mainnav" aria-label="Разделы">
            <a className="navlink" {...link("enter", "")}><Icon name="plus" /><span>Новый запрос</span></a>
            <a className="navlink" {...link("history", "history")}><Icon name="history" /><span>История</span></a>
          </nav>
          <span className="grow" />
          <button className="cmdk-btn" type="button" aria-label="Поиск и команды" onClick={() => palette.current.open()}>
            <Icon name="search" /><span>Поиск и команды</span><Kbd>{MAC ? "⌘" : "Ctrl"}</Kbd><Kbd>K</Kbd>
          </button>
          <a className="btn quiet icon" aria-label="Руководство" title="Руководство" {...link("guide", "guide")}><Icon name="help" /></a>
          <button className="btn quiet icon" id="brandbtn" type="button" aria-label={brandLabel} title={brandLabel} aria-pressed={gpb} onClick={toggleBrand}><Icon name="palette" /></button>
          <button className="btn quiet icon" type="button" aria-label={dark ? "Светлая тема" : "Тёмная тема"} title={dark ? "Светлая тема" : "Тёмная тема"} onClick={toggleTheme}>
            <Icon name={dark ? "sun" : "moon"} />
          </button>
        </div>
      </header>
      <main className="app" id="view"><GlossaryProvider>{screen}</GlossaryProvider></main>
      <CommandPalette ref={palette} items={items} />
      {toastMsg ? <div className={"toast" + (toastMsg.leaving ? " leaving" : "")} role="status"><Icon name={toastMsg.icon} /><span>{toastMsg.text}</span></div> : null}
    </AppCtx.Provider>
  );
}
