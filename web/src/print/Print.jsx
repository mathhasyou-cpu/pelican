/* Отчёт для печати (print.html#report=<имя>[&sig=<ярлык>][&print]; с sig= — один сигнал): титул, радар, топ, карточка каждого
   сигнала на своём листе, исключённое, методика. A4, в PDF — через печать браузера.
   Радар — статичный SVG из той же геометрии, что на экране (lib/radar.js: toSVG; текст
   отчёта в нём экранирован модулем). */
import { useEffect, useState } from "react";
import Card from "../components/Card.jsx";
import { Icon } from "../components/Icon.jsx";
import { LiveLine, Shortfall } from "../components/RunNotes.jsx";
import A from "../lib/api.js";
import { candidatesOf, excludedKey, excludedSummary, fmtStarted, pct, placeOf, plural } from "../lib/format.js";
import R from "../lib/radar.js";
import { stageShort } from "../lib/stages.js";
import T from "../lib/timeline.js";

const hash = location.hash.slice(1);
const m = /report=([^&]+)/.exec(hash);
const want = /(?:^|&)sig=([^&]+)/.exec(hash);   // один сигнал документом — из карточки отчёта

export default function Print() {
  const [rep, setRep] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!m) return;
    A.report(decodeURIComponent(m[1])).then(setRep, (err) => setError(err.status === 404 ? "Такого отчёта нет: ссылка устарела или набрана с ошибкой." : err.message));
  }, []);
  useEffect(() => {
    if (!rep) return undefined;
    document.title = "АС ПЕЛИКАН, " + rep.query;
    /* Колонтитулы: поля страницы не видят DOM, поэтому запрос и дату вписываем в правило @page. */
    const cssStr = (s) => '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    const st = document.createElement("style");
    st.textContent = "@page { @top-left { content: " + cssStr("АС ПЕЛИКАН, " + rep.query) + "; } @top-right { content: " + cssStr(fmtStarted(rep.started)) + "; }" +
      " @bottom-left { content: " + cssStr("отчёт " + rep.name) + "; } @bottom-right { content: counter(page) \" / \" counter(pages); } }" +
      " @page :first { @top-left { content: none; } @top-right { content: none; } @bottom-left { content: none; } @bottom-right { content: none; } }";
    document.head.appendChild(st);
    let t = 0;
    if (/(^|&)print(&|$)/.test(hash)) {
      (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(() => {
        requestAnimationFrame(() => { t = setTimeout(() => window.print(), 144); });
      });
    }
    return () => { clearTimeout(t); st.remove(); };
  }, [rep]);

  return (
    <>
      <div className="toolbar">
        <a href={rep ? "./#report=" + encodeURIComponent(rep.name) : "./"}><Icon name="arrow-left" />к отчёту</a>
        <span className="grow" />
        <span className="note">В диалоге печати выберите «Сохранить как PDF», формат A4, поля по умолчанию.</span>
        <button className="btn" type="button" onClick={() => window.print()}><Icon name="print" />Сохранить PDF</button>
      </div>
      <main id="doc">
        {!m ? <section className="page"><h1>Отчёт не указан</h1><p className="lead">Откройте отчёт в интерфейсе и нажмите «Сохранить PDF».</p></section>
          : error ? <section className="page"><h1>Отчёт не открылся</h1><p className="lead">{error}</p></section>
            : !rep ? <section className="page"><p className="lead">Собираю отчёт для печати…</p></section>
              : want ? <SignalDoc rep={rep} label={decodeURIComponent(want[1])} /> : <Doc rep={rep} />}
      </main>
    </>
  );
}

/* Инсайт документом (ТЗ 7.2): шапка с запросом и местом в топе, полная карточка с источниками
   таблицей, модели с ролями. Сигнал ищется во всех списках отчёта — открыть можно и исключённое. */
function SignalDoc({ rep, label }) {
  const thin = rep.thin || [], watch = rep.watch || [], cut = rep.excluded || [];
  const s = rep.signals.concat(thin, watch, cut).find((x) => x.label === label);
  if (!s) return <section className="page"><h1>Сигнала нет в отчёте</h1><p className="lead">Ярлык «{label}» не найден в отчёте «{rep.query}».</p></section>;
  const place = placeOf(rep, s);
  return (
    <section className="page cardpage">
      <div className="cardno"><span>АС ПЕЛИКАН, отчёт по запросу «{rep.query}», {fmtStarted(rep.started)}</span><span>{place}</span></div>
      <Card sig={s} runs={rep.history_runs} />
      <h3 className="ph">Модели</h3>
      <table><tbody>{(rep.models || []).map((x, i) => <tr key={i}><td>{x.role}</td><td className="mono">{x.model}</td></tr>)}</tbody></table>
    </section>
  );
}

function Doc({ rep }) {
  /* Направление сигнала — буква сектора; у отчётов до привязки направлений его нет — прочерк. */
  const facetOf = (s) => { const f = R.facetOf(s); return R.known(f, rep.facets.length) ? f : -1; };
  const n = rep.signals.length, cut = rep.excluded || [], thin = rep.thin || [];
  const confident = rep.signals.filter((s) => s.confidence > 0.75).length;
  const nCand = candidatesOf(rep);
  // Классы исключения — те, что есть в отчёте, в порядке приложения.
  const classes = excludedSummary(cut).map((g) => g.label).join(", ");
  const models = [...new Set((rep.models || []).map((x) => x.model))];
  const checks = ((rep.signals[0] || cut[0] || {}).checks || []);
  const Row = ({ s, why }) => (
    <tr><td style={{ width: "62mm" }}><b>{s.title}</b><div className="muted">{s.label}</div></td><td className="why">{why}</td></tr>
  );
  return (
    <>
      <section className="page title">
        <div className="brandline"><span className="wm">АС ПЕЛИКАН</span><span className="muted">Автоматизированная система поиска слабых сигналов</span></div>
        <div className="tmain">
          <p className="kicker">Отчёт по запросу</p>
          <h1>{rep.query}</h1>
          <p className="lead">{n} {plural(n, "сигнал", "сигнала", "сигналов")}, {confident} из них с уверенностью выше 75 %. Из {nCand} {plural(nCand, "кандидата", "кандидатов", "кандидатов")} исключено {cut.length}{classes ? ": " + classes : ""}. Каждый сигнал раскрыт на своей странице.</p>
          <div className="chips">{rep.facets.map((f, i) => <span className="chip" key={i}><b className="lt">{R.LETTERS.charAt(i)}</b>{f}</span>)}</div>
          <Shortfall rep={rep} />
          <LiveLine rep={rep} />
        </div>
        <div>
          <dl className="meta">
            <dt>Запуск</dt><dd>{fmtStarted(rep.started)} {rep.started.slice(0, 4)}</dd>
            <dt>Длительность</dt><dd>{T.minutes(rep.seconds)}</dd>
            <dt>Прочитано</dt><dd>{rep.sources_processed} {plural(rep.sources_processed, "источник", "источника", "источников")}, {rep.works_found} {plural(rep.works_found, "работа", "работы", "работ")} в корпусе, {rep.headlines_seen} {plural(rep.headlines_seen, "заголовок", "заголовка", "заголовков")}</dd>
            <dt>Модели</dt><dd>{models.join(", ")}</dd>
            <dt>Отчёт</dt><dd className="mono">{rep.name}, версия {rep.version}</dd>
          </dl>
          <div className="how">
            <h2>Как читать</h2>
            <ol>
              <li>Радар: пояс — стадия зрелости, в центре раннее внедрение, ближе всего к рынку, у края концепция; сектор — направление запроса; за кругом — снятое.</li>
              <li>Номер в точке — место в топе по уверенности модели роста; размер — число источников; заливка — уверенность.</li>
              <li>Карточка сигнала: почему это слабый сигнал, кто это делает, хронология, проверки свидетельств и пронумерованные источники.</li>
            </ol>
          </div>
        </div>
      </section>

      <section className="page">
        <h2 className="ph">Радар<small>{n} {plural(n, "сигнал", "сигнала", "сигналов")}, {rep.facets.length} {plural(rep.facets.length, "направление", "направления", "направлений")}</small></h2>
        <div dangerouslySetInnerHTML={{ __html: R.toSVG(rep) }} style={{ display: "contents" }} />
        <div className="radar-key" dangerouslySetInnerHTML={{ __html: R.keyHTML(rep.facets) }} />
        <ol className="pindex">{rep.signals.map((s, k) => <li key={s.label}><span className="n">{k + 1}</span><span>{s.title}</span><span className="c">{pct(s.confidence)}</span></li>)}</ol>
      </section>

      <section className="page">
        <h2 className="ph">Топ-{n}<small>по уверенности модели роста; сектор — буква на радаре</small></h2>
        <table className="ptable">
          <thead><tr><th className="n">№</th><th>Технология</th><th style={{ width: "22mm" }}>Стадия</th><th style={{ width: "9mm" }} title="Направление запроса: буква сектора радара">Сект.</th><th style={{ width: "40mm" }}>Кто делает</th><th className="cf">Уверенность</th></tr></thead>
          <tbody>
            {rep.signals.map((s, k) => {
              const f = facetOf(s);
              return (
                <tr key={s.label}>
                  <td className="n">{k + 1}</td>
                  <td><div className="ttl">{s.title}</div><div className="lab">{s.label}</div></td>
                  <td>{stageShort(s.stage)}</td>
                  <td className="mono" title={rep.facets[f] || ""}>{f >= 0 ? R.LETTERS.charAt(f) : "—"}</td>
                  <td>{(s.companies || []).slice(0, 3).join(", ")}</td>
                  <td className="cf"><div><span className="mono">{pct(s.confidence)}</span><span className={"bar" + (s.confidence > 0.75 ? " hi" : "")}><i style={{ width: Math.round(s.confidence * 100) + "%" }} /></span></div></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {rep.signals.map((s, k) => {
        const f = facetOf(s);
        return (
          <section className="page cardpage" key={s.label}>
            <div className="cardno"><span>№ {k + 1} из {n}</span><span>{f >= 0 ? R.LETTERS.charAt(f) + "  " + rep.facets[f] : ""}</span></div>
            <Card sig={s} runs={rep.history_runs} />
          </section>
        );
      })}

      <section className="page appx">
        <h2 className="ph">Приложение: исключено {cut.length}<small>с причиной по каждому</small></h2>
        {excludedSummary(cut).map((g) => (
          <div key={g.key}>
            <h3>{g.label}, {g.n}</h3>
            <table><tbody>{cut.filter((s) => excludedKey(s) === g.key).map((s) => <Row key={s.label} s={s} why={s.why} />)}</tbody></table>
          </div>
        ))}
        {thin.length ? (
          <>
            <h3>Зарождающиеся вне топа, {thin.length}</h3>
            <table><tbody>{thin.map((s) => <Row key={s.label} s={s} why={"уверенность " + pct(s.confidence) + " — " + (s.why || "")} />)}</tbody></table>
          </>
        ) : null}
        {(rep.categories || []).length ? <><h3>Снято как рыночная категория</h3><p>{rep.categories.join(", ")}</p></> : null}
      </section>

      <section className="page method">
        <h2 className="ph">Как считалось</h2>
        <p>Модель раскрывает запрос в направления, ищет технологии в локальном корпусе научных работ и в новостях о раундах, пилотах и сделках, сводит кандидатов, меряет их след и оценивает зрелость. В топ попадают только зарождающиеся технологии, прямо или смежно относящиеся к запросу; порядок — по уверенности модели роста.</p>
        <h3>Этапы прогона и их типичная длительность</h3>
        <table><tbody>
          {T.BASE.map((s, k) => {
            const end = k + 1 < T.BASE.length ? T.BASE[k + 1].at : T.TOTAL;
            return <tr key={s.id}><td className="mono" style={{ width: "16mm" }}>{T.clock(s.at)}</td><td>{s.name}</td><td className="mono muted" style={{ width: "20mm" }}>{T.clock(end - s.at)}</td></tr>;
          })}
        </tbody></table>
        <h3>Проверки свидетельств у каждого сигнала</h3>
        <ul className="checks">{checks.map((c, i) => <li key={i}>{c.name}{c.entry ? <span className="muted"> — условие входа в топ</span> : null}</li>)}</ul>
        <h3>Модели</h3>
        <table><tbody>{(rep.models || []).map((x, i) => <tr key={i}><td>{x.role}</td><td className="mono">{x.model}</td></tr>)}</tbody></table>
      </section>
    </>
  );
}
