/* Полная карточка сигнала: то, что ТЗ требует показать по инсайту, — описание, преимущество,
   кейс, объяснение статуса и уверенности, источники с наименованием, ссылкой, датой, типом,
   языком и доверием. Данные — Signal из `weak/ask.py`. */
import { useRef } from "react";
import { fmtDate, pct, plural, RELEVANCE, safeUrl } from "../lib/format.js";
import { stageLabel } from "../lib/stages.js";
import Cited, { jumpToSource } from "./Cited.jsx";
import Hint from "./Hint.jsx";

/* Пометки BR-20 и BR-27 — те же слова, что у отчёта-документа (`weak/page.py` _LOW_TRUST,
   _UNMEASURED). `corroborated` отсутствует у отчётов старше поля — тогда пометки нет. */
const LOW_TRUST = "пониженная доверенность: подтверждено только соцсетями, блогами или пресс-релизами";
const UNMEASURED = "новости не проверены: поиск не ответил — отсутствие новостей здесь ничего не значит";

/* Проверка в доле уверенности (`Check.counted`): не условие входа и не подпись. */
const counted = (c) => c.scored !== false && !c.entry;

export function Stamp({ sig }) {
  if (sig.kind === "emerging") {
    if (sig.relevance != null && sig.relevance < 2) return <span className="stamp cut">снято: не о предмете</span>;
    return <span className="stamp signal">сигнал</span>;
  }
  return <span className="stamp cut">снято: {sig.kind_label}</span>;
}

/* Резюме источника на русском. ТЗ: у зарубежного материала — русское резюме с пометкой,
   что оно машинное; нет резюме — так и сказано, а не пусто. */
function Summary({ s, block }) {
  const foreign = s.lang && s.lang !== "ru";
  if (!s.summary_ru && !foreign) return null;
  const body = s.summary_ru
    ? <>{s.summary_ru}{s.machine_summary ? <span className="muted"> (машинное резюме)</span> : null}</>
    : <span className="muted">русского резюме нет</span>;
  return block ? <div className="sum">{body}</div> : <> — {body}</>;
}

function SourceLink({ s }) {
  const url = safeUrl(s.url);
  return url ? <a href={url} target="_blank" rel="noopener noreferrer">{s.title}</a> : <span>{s.title}</span>;
}

export function SourcesTable({ list }) {
  if (!list || !list.length) return null;
  return (
    <table className="srcs">
      <thead><tr><th>№</th><th>Источник</th><th>Дата</th><th>Тип</th><th>Язык</th><th>Доверие</th></tr></thead>
      <tbody>
        {list.map((s) => (
          <tr key={s.n} data-src={s.n} id={"src-" + s.n}>
            <td className="n">{s.n}</td>
            <td><SourceLink s={s} /><div className="pub">{s.publisher}<Summary s={s} /></div></td>
            <td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtDate(s.date) || "не указана"}</td>
            <td className="muted">{s.kind}</td>
            <td className="muted">{s.lang}</td>
            <td className="muted">{s.trust_label}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* Источники списком: без таблицы в шесть колонок — ничего не уходит вбок на узкой карточке. */
export function SourcesList({ list }) {
  if (!list || !list.length) return null;
  return (
    <ol className="srclist">
      {list.map((s) => (
        <li key={s.n} data-src={s.n}>
          <span className="n">{s.n}</span>
          <div>
            <SourceLink s={s} />
            <div className="pub">{[s.publisher, fmtDate(s.date) || "дата не указана", s.kind, s.lang, "доверие " + s.trust_label].filter(Boolean).join(", ")}</div>
            <Summary s={s} block />
          </div>
        </li>
      ))}
    </ol>
  );
}

/* Раздел карточки; fold — свёрнутый (<details>), чтобы карточка оставалась короткой. */
function Section({ title, fold, count, hint, children }) {
  const ico = hint ? <Hint k={hint} /> : null;
  if (!fold) return <section><h3>{title}{ico}</h3>{children}</section>;
  return (
    <section className="fold">
      <details>
        <summary>
          {/* щелчок по ⓘ не сворачивает раздел */}
          <h3>{title}{hint ? <span onClick={(e) => e.preventDefault()}>{ico}</span> : null}</h3>{count != null ? <span className="cnt">{count}</span> : null}
          <svg className="ic" aria-hidden="true"><use href="#i-chevron" /></svg>
        </summary>
        {children}
      </details>
    </section>
  );
}

/* ТЗ 7.2: «оценки в аналитических отчётах». Отдельного типа «аналитический отчёт» у источников
   нет (`weak/trust.py`) — берём аналитические по природе: регулятор, ассоциация / международная
   организация, научный центр, отраслевое медиа, — их русское резюме и патентную динамику.
   Нет таких — так и сказано. */
const ANALYTIC = ["государство / регулятор", "отраслевая ассоциация / международная организация", "университет / научный центр", "отраслевое медиа"];
function Assessments({ sig, fold, onRef }) {
  const list = (sig.sources || []).filter((s) => ANALYTIC.includes(s.kind) && (s.summary_ru || s.title));
  return (
    <Section title="Оценки в аналитических материалах" fold={fold} count={list.length + (sig.patents ? 1 : 0)}>
      {list.length ? (
        <ul className="assess">
          {list.map((s) => (
            <li key={s.n}>
              <b>{s.publisher || s.kind}</b>{s.date ? <span className="muted">, {fmtDate(s.date)}</span> : null}: {s.summary_ru || s.title}
              {" "}{onRef ? <a href={"#src-" + s.n} onClick={(e) => { e.preventDefault(); onRef(s.n); }}>[{s.n}]</a> : "[" + s.n + "]"}
            </li>
          ))}
        </ul>
      ) : <p className="muted">Среди прочитанных источников нет аналитики, регуляторов или отраслевых медиа об этой технологии.</p>}
      {sig.patents ? <p className="t13">Патенты: {sig.patents}</p> : null}
    </Section>
  );
}

/* compact — без источников; fold — хронология, проверки, след и источники свёрнуты, источники списком.
   runs — сколько прошлых прогонов сравнивалось (`rep.history_runs`), для подписи «держится». */
export default function Card({ sig, compact, fold, runs }) {
  const emerging = sig.kind === "emerging";
  const root = useRef(null);
  // Ссылки [n] ведут к источникам карточки; без раздела источников (compact) — просто текст.
  const cite = (text) => <Cited text={text} sources={compact ? null : sig.sources} onRef={(n) => jumpToSource(root.current, n)} />;
  return (
    <article className="card" data-label={sig.label} ref={root}>
      <header>
        <h2 className="t26">{sig.title}</h2>
        <div className="lab">{sig.label}{sig.variants && sig.variants.length ? ", " + sig.variants.join(", ") : ""}</div>
        <div className="row">
          <Stamp sig={sig} />
          <span className="chip">{stageLabel(sig.stage)}<Hint k="stage" /></span>
          {sig.stream ? <span className="chip">поток: {sig.stream}</span> : null}
          {sig.held ? <span className="chip">держится в {sig.held}{runs ? " из " + runs + " " + plural(runs, "прошлого прогона", "прошлых прогонов", "прошлых прогонов") : " " + plural(sig.held, "прошлом прогоне", "прошлых прогонах", "прошлых прогонах")}</span> : null}
        </div>
        {sig.corroborated === false || sig.news_unmeasured ? (
          <div className="row marks">
            {sig.corroborated === false ? <span className="chip warn">{LOW_TRUST}</span> : null}
            {sig.news_unmeasured ? <span className="chip warn">{UNMEASURED}</span> : null}
          </div>
        ) : null}
      </header>

      {emerging ? (
        <section>
          <h3>Уверенность модели роста<Hint k="confidence" /></h3>
          <div className="conf">
            <div className={"bar" + (sig.confidence > 0.75 ? " hi" : "")}><i style={{ width: Math.round(sig.confidence * 100) + "%" }} /></div>
            <div className="mono">{pct(sig.confidence)}</div>
          </div>
          {sig.predictors && sig.predictors.length ? (
            <>
              <div className="muted t13" style={{ marginTop: 8 }}>Ключевые предикторы<Hint k="predictors" /></div>
              <ul className="preds">{sig.predictors.map((p, i) => <li key={i}>{p}</li>)}</ul>
            </>
          ) : null}
        </section>
      ) : null}

      <section>
        <h3>{emerging ? "Почему это слабый сигнал" : "Почему исключено"}<Hint k="why" /></h3>
        {sig.why ? <p>{cite(sig.why)}</p> : <p className="muted">Объяснение не сверено с источниками: статус держится на признаках уверенности выше и проверках свидетельств ниже.</p>}
        {sig.why_quote ? <blockquote>{sig.why_quote}</blockquote> : null}
      </section>

      {sig.relevance != null ? (
        <section>
          <h3>Соответствие запросу<Hint k="relevance" /></h3>
          <p>{sig.relevance} из 3 — {RELEVANCE[sig.relevance] || ""}{sig.relevance_why ? <span className="muted">: {cite(sig.relevance_why)}</span> : null}</p>
        </section>
      ) : null}
      {sig.description ? <section><h3>Что это</h3><p>{cite(sig.description)}</p></section> : null}
      {sig.advantage ? <section><h3>Потенциальное преимущество</h3><p>{cite(sig.advantage)}</p></section>
        : emerging ? <section><h3>Потенциальное преимущество</h3><p className="muted">В свидетельствах карточки преимущество не названо.</p></section> : null}
      {sig.case ? <section><h3>Кейс для банка</h3><p>{cite(sig.case)}</p></section> : null}
      {sig.facts && sig.facts.length ? (
        <Section title="Хронология" fold={fold} count={sig.facts.length}>
          <ul className="facts">{sig.facts.map((f, i) => <li key={i}><time>{fmtDate(f.date)}</time><span>{cite(f.text)}</span></li>)}</ul>
        </Section>
      ) : null}
      {sig.companies && sig.companies.length ? (
        <section><h3>Кто это делает<Hint k="companies" /></h3><div className="chips">{sig.companies.map((c) => <span key={c} className="chip solid">{c}</span>)}</div></section>
      ) : null}
      <Assessments sig={sig} fold={fold} onRef={compact ? null : (n) => jumpToSource(root.current, n)} />
      {sig.hypothesis ? <section><h3>Гипотеза для проверки</h3><p>{cite(sig.hypothesis)}</p></section> : null}

      {sig.checks && sig.checks.length ? (
        <Section title="Проверки свидетельств" fold={fold} count={sig.checks.filter((c) => c.passed && counted(c)).length + " из " + sig.checks.filter(counted).length}>
          <ul className="checks">
            {sig.checks.map((c, i) => (
              <li key={i} className={(c.passed ? "ok" : "no") + (c.entry ? " entry" : "")}>
                <span /><span>{c.name} <span className="muted">— {c.detail}{c.entry ? " (условие входа)" : ""}</span></span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
      {sig.core ? (
        <Section title="След в корпусе" fold={fold}>
          <p className="t13">
            ядро «{sig.core}»: {sig.core_works || 0} {plural(sig.core_works || 0, "работа", "работы", "работ")}
            {sig.core_since ? " с " + sig.core_since : ""}, доля за последний год {pct(sig.core_recent_share)}
            {sig.low_visibility ? " — низкая заметность" : ""}
          </p>
        </Section>
      ) : null}
      {!compact ? (
        <Section title="Источники" fold={fold} count={(sig.sources || []).length} hint="evidence">
          {fold ? <SourcesList list={sig.sources} /> : <SourcesTable list={sig.sources} />}
        </Section>
      ) : null}
    </article>
  );
}
