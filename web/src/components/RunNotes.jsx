/* Что прогон прочитал и чего не смог, и почему ТОП короче пятнадцати — то же, что у
   отчёта-документа (`weak/page.py`: строка «Живой поиск», «Не ответили», `_shortfall`).
   Без этого отказ источника и короткий ТОП на экране неотличимы от сбоя. */
import { excludedSummary } from "../lib/format.js";
import { useGlossary } from "./Hint.jsx";

/* Полный ТОП — `weak.page.TOP_FULL`. */
export const TOP_FULL = 15;

/* «Живой поиск: Google Новости (англ.): 45 · …» и «Не ответили: патенты EPO (…)». */
export function LiveLine({ rep }) {
  const names = useGlossary().live_names;
  const name = (k) => names[k] || k;
  const live = Object.entries(rep.live_by_source || {});
  const errors = Object.entries(rep.live_errors || {});
  if (!live.length && !errors.length) return null;
  return (
    <p className="runline">
      {live.length ? <span>Живой поиск: {live.map(([k, v]) => name(k) + ": " + v).join(" · ")}.</span> : null}
      {errors.length ? (
        <span className="no"> <b>Не ответили:</b> {errors.map(([k, v]) => name(k) + " (" + v + ")").join(" · ")}</span>
      ) : null}
    </p>
  );
}

/* Почему ТОП пуст или короче полного: куда ушли оценённые кандидаты. */
export function Shortfall({ rep }) {
  const n = rep.signals.length;
  if (n >= TOP_FULL) return null;
  const cut = excludedSummary(rep.excluded || []);
  const parts = cut.map((g) => g.label + " — " + g.n);
  if ((rep.thin || []).length) parts.push("зарождающиеся вне топа — " + rep.thin.length);
  if ((rep.watch || []).length) parts.push("наблюдать — " + rep.watch.length);
  if ((rep.unassessed || []).length) parts.push("оценка не удалась — " + rep.unassessed.length);
  if ((rep.categories || []).length) parts.push("рыночная категория — " + rep.categories.length);
  return (
    <div className="shortfall" role="note">
      <b>{n === 0 ? "Зарождающихся технологий по этому запросу не найдено." : "В ТОП набралось " + n + " из " + TOP_FULL + "."}</b>{" "}
      Оценено кандидатов: {rep.candidates}, научных работ найдено: {rep.works_found}, новостей просмотрено: {rep.headlines_seen}.
      {parts.length ? " " + (n === 0 ? "Все" : "Остальные") + " ушли с причиной: " + parts.join(", ") + "." : ""}
      {" "}Это не сбой: ТОП собирается только из того, что найдено в источниках. Если по теме мало
      исследований и стартапов, ТОП короткий; помогает назвать в запросе, какие именно технологии интересуют.
    </div>
  );
}
