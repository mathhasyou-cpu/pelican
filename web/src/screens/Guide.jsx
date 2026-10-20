/* Руководство (#guide[=раздел]). Иллюстрации — живые детали той же системы: радар на последнем
   отчёте истории, мини-мишени стадий, чипы, клавиши. Нет отчётов — раздел без иллюстрации. */
import { useEffect, useRef, useState } from "react";
import { Loading, useApp } from "../appctx.jsx";
import { Icon, Kbd, StageIcon } from "../components/Icon.jsx";
import Radar from "../components/Radar.jsx";
import A from "../lib/api.js";
import { STAGES, STAGE_SHORT } from "../lib/stages.js";
import { reduceMotion } from "../lib/format.js";
import T from "../lib/timeline.js";

const SECTIONS = [
  ["start", "Что делает АС ПЕЛИКАН"], ["ask", "Как спросить"], ["wait", "Пока идёт поиск"], ["radar", "Как читать радар"],
  ["card", "Карточка сигнала"], ["cut", "Что исключено и почему"], ["share", "PDF, ссылка, история"], ["keys", "Клавиши"],
];
const CUT = [
  ["зрелое", "технология уже массово внедрена: миллионы пользователей, нет роста научного следа"],
  ["стандарт", "отраслевой стандарт или регуляторное требование: внедряют, потому что обязаны"],
  ["хайп", "пик упоминаний прошёл, пилоты свёрнуты, за словами нет работ и сделок"],
  ["шум", "в заголовки попало не название технологии (конференция, тикер, рейтинг) или свидетельства не о предмете запроса: модель проверяет соответствие от 0 до 3, ниже 2 — шум"],
  ["слишком общее", "рыночная категория вроде «цифровой банкинг» — под ней десятки разных технологий"],
];

function Sec({ id, title, children }) { return <section className="gsec" id={"g-" + id}><h2>{title}</h2>{children}</section>; }

export default function Guide({ section }) {
  const app = useApp();
  const [sample, setSample] = useState(undefined);   // undefined — грузится, null — отчётов нет
  useEffect(() => {
    let live = true;
    const first = app.sorted[0];
    (first ? A.report(first.name) : app.loadHistory().then((items) => (items.length ? A.report(items.slice().sort((a, b) => (a.started < b.started ? 1 : -1))[0].name) : null)))
      .then((r) => { if (live) setSample(r); }, () => { if (live) setSample(null); });
    return () => { live = false; };
  }, []);   // образец берётся один раз на вход в руководство
  if (sample === undefined) return <Loading text="Открываю руководство" />;
  return <Body section={section} sample={sample} />;
}

function Body({ section, sample }) {
  const app = useApp();
  const scr = useRef(null);
  const [active, setActive] = useState(section);
  // Раздел, выбранный ссылкой: наблюдатель его не перебивает, пока читатель сам не прокрутит.
  // Последний раздел короче окна, и страница упирается в низ раньше, чем он дойдёт до полосы
  // наблюдателя, — без замка подсвечивался предыдущий (PQA-008).
  const pinned = useRef(null);

  function jump(id, smooth) {
    const el = scr.current && scr.current.querySelector("#g-" + id);
    if (!el) return;
    pinned.current = id;
    el.scrollIntoView({ behavior: smooth && !reduceMotion ? "smooth" : "auto", block: "start" });
    setActive(id);
  }
  useEffect(() => { if (section) setTimeout(() => jump(section, false), 0); }, [section]);
  // текущий раздел в оглавлении — по прокрутке
  useEffect(() => {
    if (!("IntersectionObserver" in window)) return undefined;
    const secs = [...scr.current.querySelectorAll(".gsec")];
    const io = new IntersectionObserver((ents) => {
      if (pinned.current) return;
      const doc = document.documentElement;
      // У самого верха — первый раздел: он короче отступа полосы, и в неё попадал второй.
      if (window.scrollY < 8) { setActive(secs[0].id.slice(2)); return; }
      if (window.scrollY + window.innerHeight >= doc.scrollHeight - 2) { setActive(secs[secs.length - 1].id.slice(2)); return; }
      ents.forEach((en) => { if (en.isIntersecting) setActive(en.target.id.slice(2)); });
    }, { rootMargin: "-20% 0px -70% 0px" });
    secs.forEach((s) => io.observe(s));
    const release = () => { pinned.current = null; };
    const kinds = ["wheel", "touchmove", "keydown"];
    kinds.forEach((k) => window.addEventListener(k, release, { passive: true }));
    return () => { io.disconnect(); kinds.forEach((k) => window.removeEventListener(k, release)); };
  }, []);

  return (
    <div className="scr" ref={scr}>
      <section className="page-head">
        <p className="kicker"><Icon name="help" />Руководство</p>
        <h1 className="h1">Как работать с АС ПЕЛИКАН</h1>
        <p className="lead">Пять минут чтения: как спросить, что происходит, пока идёт поиск, как читать радар и карточку.</p>
      </section>
      <div className="guide">
        <nav className="gtoc" aria-label="Разделы руководства">
          <ol>
            {SECTIONS.map(([id, title]) => (
              <li key={id}>
                <a href={"#guide=" + id} aria-current={active === id ? "true" : undefined} onClick={(e) => {
                  e.preventDefault();
                  try { history.replaceState(null, "", "#guide=" + id); } catch { /* ок */ }
                  jump(id, true);
                }}>{title}</a>
              </li>
            ))}
          </ol>
        </nav>
        <div className="gbody">
          <Sec id="start" title="Что делает АС ПЕЛИКАН">
            <p>АС ПЕЛИКАН ищет технологии на ранней стадии — <b>слабые сигналы</b>: о них уже пишут учёные и в них уже идут деньги, но массового внедрения ещё нет. На вход — направление, на выходе — отчёт: топ сигналов на радаре зрелости, карточка с доказательствами на каждый и всё исключённое с причиной.</p>
            <ol className="gflow">
              <li><span>1</span><b>Запрос</b><small>направление своими словами</small></li>
              <li><span>2</span><b>{T.runText(app.hist.items, app.hist.typical)}</b><small>работы, новости, оценка, карточки</small></li>
              <li><span>3</span><b>Отчёт</b><small>радар, стол, карточки</small></li>
              <li><span>4</span><b>Дальше</b><small>PDF, ссылка коллеге, история</small></li>
            </ol>
          </Sec>
          <Sec id="ask" title="Как спросить">
            <p>Лучше всего работает <b>отрасль или задача</b>, а не компания и не одно модное слово. АС ПЕЛИКАН сам раскроет запрос в 4–6 направлений — они станут секторами радара.</p>
            <div className="gex">
              <div className="ok"><p className="gh"><Icon name="check" />Хорошо</p><ul><li>перспективные решения в финтехе</li><li>антифрод на устройстве клиента</li><li>платёжные системы для ИИ-агентов</li></ul></div>
              <div className="no"><p className="gh"><Icon name="close" />Хуже</p><ul><li>ИИ <span>— слишком широко, топ займут категории</span></li><li>Visa <span>— компания, а не технология</span></li><li>блокчейн 2021 <span>— АС ПЕЛИКАН смотрит на сегодня</span></li></ul></div>
            </div>
          </Sec>
          <Sec id="wait" title="Пока идёт поиск">
            <p>Прогон идёт {T.runText(app.hist.items, app.hist.typical)}, запросы выполняются по одному: если модель занята, запрос встанет в очередь и покажет своё место. Вкладку можно закрыть: ссылка на ожидание сохранит место, готовый отчёт появится в истории. На экране — этап, числа и радар, который заполняется на глазах:</p>
            <ol className="gstages">{T.BASE.map((s) => <li key={s.id}><span className="mono muted">{T.clock(s.at)}</span>{s.name}</li>)}</ol>
            <p className="muted small">Полоса прогресса поделена по типичной длительности этапов на последних прогонах — это не прогноз. Если идёт дольше обычного, полоса станет штриховой. С одного адреса в работе и в очереди может быть не больше трёх запросов.</p>
          </Sec>
          <Sec id="radar" title="Как читать радар">
            {sample ? (
              <>
                <div className="gradar"><Radar opts={{ key: true }} onReady={(r) => r.setData(sample)} /></div>
                <p className="muted small">Радар отчёта «{sample.query}».</p>
              </>
            ) : null}
            <dl className="gdl">
              <div><dt>Пояса — стадия</dt><dd>В центре — раннее внедрение, дальше пилот и прототип, у края — концепция. Чем ближе к центру, тем ближе к рынку.</dd></div>
              <div><dt>Сектора — направления запроса</dt><dd>Буквы А, Б, В… совпадают с чипами над отчётом. Наведите на сектор — он подсветится, нажмите чип — останется только он.</dd></div>
              <div><dt>Точка — сигнал</dt><dd>Номер — место в топе. Размер — сколько источников, заливка — уверенность модели роста: жёлтая — выше 75 %.</dd></div>
              <div><dt>За кругом — исключённое</dt><dd>Серые точки снаружи — кандидаты, которых модель сняла. Пунктир — зарождающиеся, не вошедшие в топ.</dd></div>
            </dl>
            <p className="muted small">Направление сигналу назначается автоматически: по тому, какое направление запроса нашло его научные работы, иначе — по близости смысла названия. У отчётов, собранных до этой привязки, сектор точки условный, а подсказка направление не называет.</p>
          </Sec>
          <Sec id="card" title="Карточка сигнала">
            <p>Нажмите строку стола или точку радара — справа откроется карточка. Сверху — то, что нужно для решения, ниже — свёрнутые доказательства:</p>
            <ul className="gparts">
              <li><b>Уверенность модели роста</b> — вероятность, что технология вырастет; под ней — что тянуло вверх и вниз.</li>
              <li><b>Почему это слабый сигнал</b> — объяснение со ссылками [1][2] на источники карточки.</li>
              <li><b>Стадия</b> {STAGES.map((s, i) => <span key={s} className="stg"><StageIcon stage={s} />{STAGE_SHORT[i]}</span>)} — мини-мишень показывает пояс радара.</li>
              <li><b>Кейс для банка и гипотеза</b> — как это могло бы работать у нас и что проверить первым.</li>
              <li><b>Проверки свидетельств, хронология, след в корпусе, источники</b> — свёрнуты; раскрываются по нажатию. У зарубежного источника — русское резюме с пометкой «машинное резюме».</li>
            </ul>
          </Sec>
          <Sec id="cut" title="Что исключено и почему">
            <p>Ничего не пропадает молча: каждый снятый кандидат лежит под столом с причиной. Классы:</p>
            <dl className="gcut">{CUT.map((c) => <div key={c[0]}><dt><span className="stamp cut">{c[0]}</span></dt><dd>{c[1]}</dd></div>)}</dl>
          </Sec>
          <Sec id="share" title="PDF, ссылка, история">
            <ul className="gparts">
              <li><b>Сохранить PDF</b> — отчёт для печати: титул, радар, топ, карточка каждого сигнала на своём листе, исключённое, методика.</li>
              <li><b>Поделиться</b> — ссылка на отчёт в буфер; по ней откроется тот же вид (стол или радар), выбранный сигнал и раскрытое исключённое. Адрес вкладки меняется вместе с выбором — его тоже можно скопировать.</li>
              <li><b>История</b> — все отчёты по месяцам, поиск по запросу; любой открывается сразу.</li>
              <li><b>Повтор запроса</b> — если по нему уже есть отчёт, он откроется сразу, без нового прогона.</li>            </ul>
          </Sec>
          <Sec id="keys" title="Клавиши">
            <dl className="gkeys">
              <div><dt><Kbd>{app.mac ? "⌘" : "Ctrl"}</Kbd><Kbd>K</Kbd></dt><dd>поиск отчётов, команды, новый запрос откуда угодно</dd></div>
              <div><dt><Kbd>/</Kbd></dt><dd>поиск в истории</dd></div>
              <div><dt><Kbd>↑</Kbd><Kbd>↓</Kbd><Kbd>←</Kbd><Kbd>→</Kbd></dt><dd>листать сигналы в отчёте</dd></div>
              <div><dt><Kbd>Enter</Kbd></dt><dd>открыть карточку строки стола</dd></div>
              <div><dt><Kbd>Esc</Kbd></dt><dd>закрыть окно</dd></div>
            </dl>
          </Sec>
        </div>
      </div>
    </div>
  );
}
