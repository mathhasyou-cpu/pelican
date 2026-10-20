/* «Сейчас в работе»: очередь из `GET /api/queue`, пока в ней есть свои задания — свои со
   ссылкой на экран ожидания, чужие строкой «чужой запрос» (PQA-001, QP-01, QP-02, UC-09). Без
   него запущенная задача жила только во вкладке #job=: закрыл её — дороги назад нет. Текста
   чужих запросов API не отдаёт (NFR-52). Опрос раз в 12 с, на скрытой вкладке — пауза. */
import { useEffect, useState } from "react";
import { Go } from "../appctx.jsx";
import A from "../lib/api.js";
import { plural } from "../lib/format.js";
import { Icon } from "./Icon.jsx";

/* ⚠️ Догадка, не замер: этап длится минуты, 12 с — заметно свежее и не нагружает стенд. */
const POLL_MS = 12000;

export function stateText(x, now = Date.now()) {
  if (x.status === "running") {
    const min = x.started ? Math.max(0, Math.round((now - Date.parse(x.started)) / 60000)) : null;
    return "в работе" + (x.stage ? ", этап " + x.stage.n + " из " + x.stage.of : "") + (min != null ? ", " + min + " мин" : "");
  }
  const eta = x.eta_start_s ? ", начнётся примерно через " + Math.max(1, Math.round(x.eta_start_s / 60)) + " мин" : "";
  return "в очереди, впереди " + x.position + " " + plural(x.position, "запрос", "запроса", "запросов") + eta;
}

export default function Active() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    let live = true, timer = 0;
    function poll() {
      clearTimeout(timer);
      if (document.hidden) return;
      A.queue().then((q) => { if (live) setItems(q || []); }, () => { /* нет связи — блок прежний */ })
        .then(() => { if (live) timer = setTimeout(poll, POLL_MS); });
    }
    function onVisible() { if (!document.hidden) poll(); }
    poll();
    document.addEventListener("visibilitychange", onVisible);
    return () => { live = false; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, []);
  if (!items.some((x) => x.mine)) return null;
  return (
    <section className="active-jobs surface" id="active" aria-live="polite">
      <h2 className="h3">Сейчас в работе</h2>
      <ul className="arows">
        {items.map((x) => (
          <li key={x.mine ? x.id : "p" + x.position} className={x.mine ? "" : "other"}>
            <span className={"pulse" + (x.status === "running" ? "" : " idle")} aria-hidden="true" />
            {x.mine ? <Go to={"job=" + x.id} className="q">{x.query}</Go> : <span className="q">чужой запрос</span>}
            <span className="s">{stateText(x)}</span>
            {x.mine ? <Go to={"job=" + x.id} className="btn sm quiet" tabIndex={-1}><Icon name="arrow-right" />следить</Go> : <span />}
          </li>
        ))}
      </ul>
    </section>
  );
}
